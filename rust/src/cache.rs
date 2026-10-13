//! Disposable, content-addressed parse data. Resolution is recomputed per project.
use crate::model::{hash, Call, File, Import, Logic, Symbol};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        Mutex,
    },
};

const FORMAT: &str = "grapholog.parse-cache.v1";
const MAX_ENTRY: u64 = 64 * 1024 * 1024;
static TEMP_ID: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Header {
    schema: String,
    namespace: String,
    language: String,
    source_sha256: String,
    payload_sha256: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Parsed {
    lines: usize,
    parser: String,
    status: String,
    syntax_hash: String,
    shape_hash: String,
    fingerprints: Vec<String>,
    nodes: usize,
    symbols: Vec<Symbol>,
    imports: Vec<Import>,
    calls: Vec<Call>,
    logic: Vec<Logic>,
    notes: Vec<String>,
}

#[derive(Serialize)]
struct ParsedRef<'a> {
    lines: usize,
    parser: &'a str,
    status: &'a str,
    syntax_hash: &'a str,
    shape_hash: &'a str,
    fingerprints: &'a [String],
    nodes: usize,
    symbols: &'a [Symbol],
    imports: &'a [Import],
    calls: &'a [Call],
    logic: &'a [Logic],
    notes: &'a [String],
}
impl<'a> From<&'a File> for ParsedRef<'a> {
    fn from(f: &'a File) -> Self {
        Self {
            lines: f.lines,
            parser: &f.parser,
            status: &f.status,
            syntax_hash: &f.syntax_hash,
            shape_hash: &f.shape_hash,
            fingerprints: &f.fingerprints,
            nodes: f.nodes,
            symbols: &f.symbols,
            imports: &f.imports,
            calls: &f.calls,
            logic: &f.logic,
            notes: &f.notes,
        }
    }
}
impl Parsed {
    fn valid(&self, language: &str) -> bool {
        let hex = |s: &str, n| {
            s.len() == n
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        };
        self.status == "parsed"
            && self.parser == format!("tree-sitter:{language}")
            && hex(&self.syntax_hash, 64)
            && hex(&self.shape_hash, 64)
            && self.fingerprints.iter().all(|s| hex(s, 16))
            && self.fingerprints.windows(2).all(|p| p[0] < p[1])
            && self
                .imports
                .iter()
                .all(|i| i.targets.is_empty() && i.status == "unresolved")
            && self
                .calls
                .iter()
                .all(|c| c.target.is_empty() && c.resolution == "unresolved")
    }
    fn restore(self, f: &mut File) {
        f.lines = self.lines;
        f.parser = self.parser;
        f.status = self.status;
        f.syntax_hash = self.syntax_hash;
        f.shape_hash = self.shape_hash;
        f.fingerprints = self.fingerprints;
        f.nodes = self.nodes;
        f.symbols = self.symbols;
        f.imports = self.imports;
        f.calls = self.calls;
        f.logic = self.logic;
        f.notes = self.notes;
    }
}

pub struct Cache {
    root: Option<PathBuf>,
    directory: Option<PathBuf>,
    namespace: String,
    hits: AtomicUsize,
    misses: AtomicUsize,
    writes: AtomicUsize,
    invalid: AtomicUsize,
    errors: AtomicUsize,
    skipped_large: AtomicUsize,
    issues: Mutex<Vec<String>>,
}
fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn json_error(error: serde_json::Error) -> io::Error {
    invalid(&error.to_string())
}
fn directory(path: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)?;
    if !fs::symlink_metadata(path)?.is_dir() {
        return Err(invalid("Cache parent is not a regular directory"));
    }
    Ok(())
}
impl Cache {
    pub fn new(path: Option<&Path>) -> Self {
        let mut cache = Self {
            root: None,
            directory: path.map(Path::to_path_buf),
            namespace: String::new(),
            hits: AtomicUsize::new(0),
            misses: AtomicUsize::new(0),
            writes: AtomicUsize::new(0),
            invalid: AtomicUsize::new(0),
            errors: AtomicUsize::new(0),
            skipped_large: AtomicUsize::new(0),
            issues: Mutex::new(vec![]),
        };
        if let Some(path) = path {
            let result = (|| {
                directory(path)?;
                let root = path.canonicalize()?;
                let mut digest = Sha256::new();
                for value in [
                    FORMAT.as_bytes(),
                    b"rust-tree-sitter",
                    env!("CARGO_PKG_VERSION").as_bytes(),
                    include_bytes!("../Cargo.lock").as_slice(),
                    include_bytes!("syntax.rs").as_slice(),
                    include_bytes!("model.rs").as_slice(),
                    include_bytes!("cache.rs").as_slice(),
                ] {
                    digest.update((value.len() as u64).to_be_bytes());
                    digest.update(value);
                }
                cache.namespace = format!("{:x}", digest.finalize());
                cache.directory = Some(root.clone());
                cache.root = Some(root);
                Ok::<(), io::Error>(())
            })();
            if let Err(error) = result {
                cache.error(&error);
            }
        }
        cache
    }
    pub fn root(&self) -> Option<&Path> {
        self.root.as_deref()
    }
    fn error(&self, error: &io::Error) {
        self.errors.fetch_add(1, Ordering::Relaxed);
        let mut issues = self.issues.lock().unwrap();
        if issues.len() < 3 {
            issues.push(error.to_string());
        }
    }
    fn entry(&self, f: &File) -> PathBuf {
        let key = hash(format!("{}\0{}", f.language, f.sha256).as_bytes());
        self.root
            .as_ref()
            .unwrap()
            .join("rust-v1")
            .join(&self.namespace)
            .join(&key[..2])
            .join(format!("{key}.jsonl"))
    }
    fn parents(&self, entry: &Path, create: bool) -> io::Result<()> {
        let root = self.root.as_ref().unwrap();
        let mut current = root.clone();
        if !fs::symlink_metadata(&current)?.is_dir() {
            return Err(invalid("Cache root is not a regular directory"));
        }
        for part in entry
            .parent()
            .unwrap()
            .strip_prefix(root)
            .unwrap()
            .components()
        {
            current.push(part);
            if create {
                directory(&current)?;
            }
            if !fs::symlink_metadata(&current)?.is_dir() {
                return Err(invalid("Cache parent is not a regular directory"));
            }
        }
        Ok(())
    }
    fn read(&self, f: &File) -> io::Result<Parsed> {
        let path = self.entry(f);
        self.parents(&path, false)?;
        let mut options = fs::OpenOptions::new();
        options.read(true);
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(0x20000 | 0x800);
        }
        let stream = options.open(path)?;
        let meta = stream.metadata()?;
        if !meta.is_file() || meta.len() > MAX_ENTRY {
            return Err(invalid("Invalid cache entry size or type"));
        }
        let mut data = Vec::new();
        stream.take(MAX_ENTRY + 1).read_to_end(&mut data)?;
        if data.len() as u64 > MAX_ENTRY {
            return Err(invalid("Cache entry exceeds size limit"));
        }
        let split = data
            .iter()
            .position(|b| *b == b'\n')
            .ok_or_else(|| invalid("Missing cache header"))?;
        if split > 4096 {
            return Err(invalid("Invalid cache header"));
        }
        let header: Header = serde_json::from_slice(&data[..split]).map_err(json_error)?;
        let payload = &data[split + 1..];
        if header.schema != FORMAT
            || header.namespace != self.namespace
            || header.language != f.language
            || header.source_sha256 != f.sha256
            || header.payload_sha256 != hash(payload)
        {
            return Err(invalid("Cache header or checksum mismatch"));
        }
        let parsed: Parsed = serde_json::from_slice(payload).map_err(json_error)?;
        if !parsed.valid(&f.language) {
            return Err(invalid("Incompatible or resolved parse data"));
        }
        Ok(parsed)
    }
    pub fn load(&self, f: &mut File) -> bool {
        if self.root.is_none() {
            return false;
        }
        match self.read(f) {
            Ok(parsed) => {
                parsed.restore(f);
                self.hits.fetch_add(1, Ordering::Relaxed);
                return true;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(error) if error.kind() == io::ErrorKind::InvalidData => {
                self.invalid.fetch_add(1, Ordering::Relaxed);
            }
            Err(error) => self.error(&error),
        }
        self.misses.fetch_add(1, Ordering::Relaxed);
        false
    }
    pub fn store(&self, f: &File) {
        if self.root.is_none() || f.status != "parsed" {
            return;
        }
        let mut temporary = None;
        let result = (|| {
            let payload = serde_json::to_vec(&ParsedRef::from(f)).map_err(json_error)?;
            let header = serde_json::to_vec(&Header {
                schema: FORMAT.into(),
                namespace: self.namespace.clone(),
                language: f.language.clone(),
                source_sha256: f.sha256.clone(),
                payload_sha256: hash(&payload),
            })
            .map_err(json_error)?;
            if payload.len() + header.len() + 1 > MAX_ENTRY as usize {
                self.skipped_large.fetch_add(1, Ordering::Relaxed);
                return Ok(());
            }
            let path = self.entry(f);
            self.parents(&path, true)?;
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let temp = path.parent().unwrap().join(format!(
                ".tmp-{}-{stamp}-{}",
                std::process::id(),
                TEMP_ID.fetch_add(1, Ordering::Relaxed)
            ));
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut stream = options.open(&temp)?;
            temporary = Some(temp.clone());
            stream.write_all(&header)?;
            stream.write_all(b"\n")?;
            stream.write_all(&payload)?;
            drop(stream);
            fs::rename(&temp, &path)?;
            temporary = None;
            self.writes.fetch_add(1, Ordering::Relaxed);
            Ok::<(), io::Error>(())
        })();
        if let Some(temp) = temporary {
            let _ = fs::remove_file(temp);
        }
        if let Err(error) = result {
            self.error(&error);
        }
    }
    pub fn stats(&self) -> Value {
        if self.directory.is_none() {
            return json!({"enabled":false});
        }
        let read = |counter: &AtomicUsize| counter.load(Ordering::Relaxed);
        let mut issues = self.issues.lock().unwrap().clone();
        issues.sort();
        json!({"enabled":self.root.is_some(),"directory":self.directory.as_ref().unwrap().to_string_lossy(),
            "namespace":self.namespace,"hits":read(&self.hits),"misses":read(&self.misses),"writes":read(&self.writes),
            "invalid":read(&self.invalid),"errors":read(&self.errors),"skipped_large":read(&self.skipped_large),"issues":issues})
    }
}
