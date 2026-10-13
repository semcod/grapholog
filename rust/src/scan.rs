use crate::{cache::Cache, model::*, resolve, syntax};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
};
use tree_sitter::Parser;

pub const EXCLUDES: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
];
#[derive(Clone)]
pub struct Options {
    pub all: bool,
    pub excludes: Vec<String>,
    pub max_bytes: u64,
    pub source_roots: Vec<String>,
    pub cache_dir: Option<PathBuf>,
    pub jobs: usize,
    pub profile: String,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            all: false,
            excludes: vec![],
            max_bytes: 2 * 1024 * 1024,
            source_roots: vec![".".into(), "src".into()],
            cache_dir: None,
            jobs: 1,
            profile: "inventory".into(),
        }
    }
}

struct Policy {
    prune_directories: std::collections::HashSet<String>,
    generated_globs: Vec<glob::Pattern>,
    source_extensions: std::collections::HashSet<String>,
    context_names: std::collections::HashSet<String>,
    unsupported_extensions: std::collections::HashSet<String>,
}

impl Policy {
    fn new() -> Self {
        let v: serde_json::Value =
            serde_json::from_slice(include_bytes!("../../src/grapholog/data/logic-profile.json"))
                .unwrap();
        let to_set = |key: &str| -> std::collections::HashSet<String> {
            v[key]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|s| s.as_str().map(String::from))
                .collect()
        };
        let globs = v["generated_globs"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|s| s.as_str().and_then(|p| glob::Pattern::new(p).ok()))
            .collect();
        Self {
            prune_directories: to_set("prune_directories"),
            generated_globs: globs,
            source_extensions: to_set("source_extensions"),
            context_names: to_set("context_names"),
            unsupported_extensions: to_set("unsupported_extensions"),
        }
    }
}

fn filter_reason(path: &Path, directory: bool, executable: bool) -> Option<&'static str> {
    static POLICY: std::sync::OnceLock<Policy> = std::sync::OnceLock::new();
    let p = POLICY.get_or_init(Policy::new);
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    if directory {
        return p
            .prune_directories
            .contains(name.as_ref())
            .then_some("generated-or-tool-directory");
    }
    if p.generated_globs.iter().any(|g| g.matches(&name)) {
        return Some("generated-or-minified");
    }
    let ext = path
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_ascii_lowercase();
    if p.source_extensions.contains(&ext)
        || p.context_names.contains(name.as_ref())
        || (ext.is_empty() && executable)
    {
        return None;
    }
    Some(if p.unsupported_extensions.contains(&ext) {
        "unsupported-language"
    } else {
        "non-code"
    })
}

fn language(path: &Path, raw: &[u8]) -> String {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let result = match ext.as_str() {
        "py" | "pyi" => "python",
        "js" | "mjs" | "cjs" | "jsx" => "javascript",
        "ts" | "mts" | "cts" => "typescript",
        "tsx" => "tsx",
        "php" | "phtml" => "php",
        "sh" | "bash" => "bash",
        _ => "other",
    };
    if result == "other" && raw.starts_with(b"#!") {
        let line = String::from_utf8_lossy(raw.split(|c| *c == b'\n').next().unwrap_or(b""));
        if line.contains("python") {
            return "python".into();
        }
        if ["/bash", " bash", "/sh", " sh"]
            .iter()
            .any(|p| line.contains(p))
        {
            return "bash".into();
        }
    }
    result.into()
}
fn read_file(
    path: &Path,
    f: &mut File,
    limit: u64,
    parsers: &mut BTreeMap<String, Parser>,
    buf: &mut [u8],
    cache: &Cache,
) -> io::Result<()> {
    let mut open = fs::OpenOptions::new();
    open.read(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        open.custom_flags(0x20000 | 0x800);
    }
    let mut stream = open.open(path)?;
    let before = stream.metadata()?;
    if !before.is_file() {
        return Err(io::Error::other("Entry changed type during scan"));
    }
    let mut hasher = Sha256::new();
    let mut content = vec![];
    let mut first = vec![];
    let mut size = 0;
    loop {
        let n = stream.read(buf)?;
        if n == 0 {
            break;
        }
        if first.is_empty() {
            first.extend_from_slice(&buf[..n.min(4096)]);
        }
        hasher.update(&buf[..n]);
        size += n as u64;
        if limit == 0 || size <= limit {
            content.extend_from_slice(&buf[..n]);
        } else {
            content.clear();
        }
    }
    let after = stream.metadata()?;
    f.size = size;
    f.sha256 = format!("{:x}", hasher.finalize());
    f.language = language(path, &first);
    if before.len() != after.len() || before.modified().ok() != after.modified().ok() {
        f.status = "changed-during-scan".into();
        f.sha256.clear();
        f.notes.push("File changed while reading; rerun".into());
        return Ok(());
    }
    if limit > 0 && size > limit {
        f.status = "size-limit".into();
        f.notes.push(format!(
            "SHA-256 complete; parsing limited to {limit} bytes"
        ));
        return Ok(());
    }
    if content.contains(&0) {
        f.status = "binary".into();
        return Ok(());
    }
    let source = match std::str::from_utf8(&content) {
        Ok(s) => s,
        Err(_) => {
            f.status = "non-utf8".into();
            return Ok(());
        }
    };
    f.lines = source.lines().count();
    if f.language == "other" {
        f.status = "text".into();
        return Ok(());
    }
    if !cache.load(f) {
        syntax::parse(f, &content, parsers);
        cache.store(f);
    }
    Ok(())
}

fn work(
    items: &[(PathBuf, File)],
    next: &AtomicUsize,
    options: &Options,
    cache: &Cache,
) -> Vec<File> {
    let mut parsers = BTreeMap::new();
    let mut buffer = vec![0; 64 * 1024];
    let mut result = vec![];
    while let Some((path, template)) = items.get(next.fetch_add(1, Ordering::Relaxed)) {
        let mut file = template.clone();
        if let Err(error) = read_file(
            path,
            &mut file,
            options.max_bytes,
            &mut parsers,
            &mut buffer,
            cache,
        ) {
            file.status = "read-error".into();
            file.notes.push(error.to_string());
        }
        result.push(file);
    }
    result
}

fn process(
    items: &[(PathBuf, File)],
    options: &Options,
    cache: &Cache,
    workers: usize,
) -> io::Result<Vec<File>> {
    let next = AtomicUsize::new(0);
    if workers == 1 {
        return Ok(work(items, &next, options, cache));
    }
    std::thread::scope(|scope| {
        let mut handles = vec![];
        for _ in 0..workers {
            let next = &next;
            handles.push(
                std::thread::Builder::new()
                    .stack_size(8 * 1024 * 1024)
                    .spawn_scoped(scope, move || work(items, next, options, cache))?,
            );
        }
        let mut result = vec![];
        for handle in handles {
            result.extend(
                handle
                    .join()
                    .map_err(|_| io::Error::other("Parser worker failed"))?,
            );
        }
        Ok(result)
    })
}

pub fn scan(path: &Path, options: &Options) -> io::Result<Project> {
    if !["inventory", "logic"].contains(&options.profile.as_str()) {
        return Err(io::Error::other("profile must be inventory or logic"));
    }
    let root = path.canonicalize()?;
    if !root.is_dir() {
        return Err(io::Error::other("Not a directory"));
    }
    if !(1..=64).contains(&options.jobs) {
        return Err(io::Error::other("--jobs must be between 1 and 64"));
    }
    if options
        .cache_dir
        .as_ref()
        .and_then(|p| p.canonicalize().ok())
        .is_some_and(|p| root.starts_with(p))
    {
        return Err(io::Error::other("--cache-dir must not be the project root or its ancestor; use a dedicated cache directory"));
    }
    let cache = Cache::new(options.cache_dir.as_deref());
    if cache.root().is_some_and(|p| root.starts_with(p)) {
        return Err(io::Error::other("--cache-dir must not be the project root or its ancestor; use a dedicated cache directory"));
    }
    let mut p = Project {
        root: root.to_string_lossy().into(),
        schema: "grapholog.project.v1".into(),
        ..Default::default()
    };
    let mut patterns = options.excludes.clone();
    let ignore = root.join(".graphologignore");
    if !options.all
        && fs::symlink_metadata(&ignore)
            .map(|m| m.is_file())
            .unwrap_or(false)
    {
        match fs::read_to_string(&ignore) {
            Ok(s) => patterns.extend(
                s.lines()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && !s.starts_with('#'))
                    .map(str::to_string),
            ),
            Err(e) => p
                .warnings
                .push(format!("Cannot read .graphologignore: {e}")),
        }
    }
    p.options = serde_json::json!({"all_files":options.all,"exclude":options.excludes,"effective_patterns":patterns,
        "max_parse_bytes":options.max_bytes,"source_roots":options.source_roots,"engine":"rust-tree-sitter"});
    let mut pending = vec![];
    let mut filtered = vec![];
    if options.profile == "logic" { p.options["profile"] = serde_json::json!("logic-v1"); }
    let compiled_patterns = patterns
        .iter()
        .map(|p| {
            let trimmed = p.trim_end_matches('/');
            (glob::Pattern::new(trimmed).ok(), format!("{trimmed}/"))
        })
        .collect::<Vec<_>>();
    let mut dirs = vec![root.clone()];
    while let Some(dir) = dirs.pop() {
        let entries = match fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) => {
                p.warnings
                    .push(format!("Cannot list {}: {e}", dir.display()));
                continue;
            }
        };
        let mut paths = vec![];
        for entry in entries {
            match entry {
                Ok(e) => paths.push(e.path()),
                Err(e) => p.warnings.push(e.to_string()),
            }
        }
        paths.sort();
        for path in paths {
            let rel = path
                .strip_prefix(&root)
                .unwrap()
                .components()
                .map(|c| c.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            let name = path.file_name().unwrap().to_string_lossy();
            if cache.root() == Some(path.as_path()) {
                if options.profile == "logic" { filtered.push(serde_json::json!({"path":rel,"reason":"parse-cache","bytes":0})); }
                p.excluded.push(rel);
                continue;
            }
            if (!options.all && EXCLUDES.contains(&name.as_ref()))
                || compiled_patterns.iter().any(|(pattern, prefix)| {
                    pattern
                        .as_ref()
                        .map(|g| g.matches(&rel) || g.matches(&name))
                        .unwrap_or(false)
                        || rel.starts_with(prefix)
                })
            {
                if options.profile == "logic" { filtered.push(serde_json::json!({"path":rel,"reason":"default-or-user-exclude","bytes":0})); }
                p.excluded.push(rel);
                continue;
            }
            let mut f = File {
                path: rel,
                kind: "file".into(),
                language: "other".into(),
                parser: "none".into(),
                status: "inventory".into(),
                ..Default::default()
            };
            match fs::symlink_metadata(&path) {
                Err(e) => {
                    f.status = "read-error".into();
                    f.notes.push(e.to_string());
                }
                Ok(meta) => {
                    f.size = meta.len();
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        f.executable = meta.permissions().mode() & 0o111 != 0;
                    }
                    if options.profile == "logic" && (meta.is_file() || meta.is_dir()) {
                        if let Some(reason) = filter_reason(&path, meta.is_dir(), f.executable) {
                            filtered.push(serde_json::json!({"path":f.path,"reason":reason,"bytes":if meta.is_file(){meta.len()}else{0}}));
                            p.excluded.push(f.path);
                            continue;
                        }
                    }
                    if meta.is_symlink() {
                        f.kind = "symlink".into();
                        f.status = "symlink".into();
                        match fs::read_link(&path) {
                            Ok(t) => {
                                f.link_target = t.to_string_lossy().into();
                                f.sha256 = hash(f.link_target.as_bytes());
                                f.notes.push("Link target inventoried; not followed".into());
                            }
                            Err(e) => {
                                f.status = "read-error".into();
                                f.notes.push(e.to_string());
                            }
                        }
                    } else if meta.is_dir() {
                        p.directories.push(f.path);
                        dirs.push(path);
                        continue;
                    } else if !meta.is_file() {
                        f.kind = "special".into();
                        f.status = "special".into();
                        f.notes.push("Special file; not opened".into());
                    } else {
                        pending.push((path, f));
                        continue;
                    }
                }
            }
            p.files.push(f);
        }
    }
    let workers = options.jobs.min(pending.len().max(1));
    p.files.extend(process(&pending, options, &cache, workers)?);
    drop(pending);
    p.files.sort_by(|a, b| a.path.cmp(&b.path));
    p.directories.sort();
    p.excluded.sort();
    resolve::resolve(&mut p, &options.source_roots);
    p.runtime = serde_json::json!({"workers":workers,"cache":cache.stats()});
    if options.profile == "logic" {
        filtered.sort_by(|a,b| a["path"].as_str().cmp(&b["path"].as_str()));
        p.runtime["filter"] = serde_json::json!({"policy":"logic-v1","excluded_entries":filtered,
            "note":"A pruned directory counts as one entry; descendant counts and bytes are unknown."});
    }
    Ok(p)
}
