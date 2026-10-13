mod cache;
mod compare;
mod model;
mod render;
mod resolve;
mod scan;
mod similarity;
mod syntax;
use std::{
    env, fs,
    io::{self, BufWriter, Write},
    path::PathBuf,
};

#[derive(serde::Serialize)]
struct ScanReport<'a> {
    #[serde(flatten)]
    project: &'a model::Project,
    duplicates: serde_json::Value,
}
fn run() -> Result<i32, String> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    if args.iter().any(|s| s == "--help" || s == "-h") {
        println!("grapholog-rs [scan] DIR [--detail files|compact|full] [--format terminal|json|dot]\ngrapholog-rs diff A B [--format terminal|json] [--check]\nOptions: --all --exclude GLOB --source-root DIR --max-bytes N --min-nodes N\n         --threshold 0..1 --max-pairs N --ascii --strict --output FILE\n         --cache-dir DIR --jobs 1..64 (default 1) --profile inventory|logic\nOutput files must not already exist. The scanned code is never executed.");
        return Ok(0);
    }
    if args.iter().any(|s| s == "--version") {
        println!("0.5.0");
        return Ok(0);
    }
    let mut options = scan::Options::default();
    let mut command = "scan";
    let mut paths = vec![];
    let mut format = "terminal".to_string();
    let mut detail = "compact".to_string();
    let mut ascii = false;
    let mut strict = false;
    let mut check = false;
    let mut threshold = 0.7;
    let mut max_pairs = 20000;
    let mut min_nodes = 12;
    let mut output: Option<PathBuf> = None;
    let mut custom_roots = false;
    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        match arg.as_str() {
            "scan" | "diff" if i == 0 => command = arg,
            "--all" => options.all = true,
            "--ascii" => ascii = true,
            "--strict" => strict = true,
            "--check" => check = true,
            "--exclude" | "--source-root" | "--max-bytes" | "--min-nodes" | "--format"
            | "--detail" | "--threshold" | "--max-pairs" | "--output" | "-o" | "--cache-dir"
            | "--jobs" | "--profile" => {
                i += 1;
                let val = args
                    .get(i)
                    .ok_or_else(|| format!("Missing value for {arg}"))?;
                match arg.as_str() {
                    "--exclude" => options.excludes.push(val.clone()),
                    "--source-root" => {
                        if !custom_roots {
                            options.source_roots.clear();
                            custom_roots = true;
                        }
                        let p = PathBuf::from(val);
                        if p.is_absolute()
                            || p.components().any(|c| c == std::path::Component::ParentDir)
                        {
                            return Err("source root must be relative and inside project".into());
                        }
                        options.source_roots.push(val.clone());
                    }
                    "--max-bytes" => {
                        options.max_bytes = val.parse().map_err(|_| "invalid max-bytes")?
                    }
                    "--min-nodes" => min_nodes = val.parse().map_err(|_| "invalid min-nodes")?,
                    "--format" => format = val.clone(),
                    "--detail" => detail = val.clone(),
                    "--threshold" => {
                        threshold = val.parse::<f64>().map_err(|_| "invalid threshold")?
                    }
                    "--max-pairs" => max_pairs = val.parse().map_err(|_| "invalid max-pairs")?,
                    "--cache-dir" => options.cache_dir = Some(PathBuf::from(val)),
                    "--jobs" => options.jobs = val.parse().map_err(|_| "invalid jobs")?,
                    "--profile" => options.profile = val.clone(),
                    _ => output = Some(PathBuf::from(val)),
                }
            }
            _ if arg.starts_with('-') => return Err(format!("Unknown option: {arg}")),
            _ => paths.push(PathBuf::from(arg)),
        }
        i += 1;
    }
    if !(0.0..=1.0).contains(&threshold) {
        return Err("threshold must be between 0 and 1".into());
    }
    if !["terminal", "json", "dot"].contains(&format.as_str())
        || command == "diff" && format == "dot"
    {
        return Err("invalid format".into());
    }
    if !["files", "compact", "full"].contains(&detail.as_str()) {
        return Err("invalid detail".into());
    }
    if paths.is_empty() && command == "scan" {
        paths.push(PathBuf::from("."));
    }
    if paths.len() != if command == "scan" { 1 } else { 2 } {
        return Err("scan needs one directory; diff needs two".into());
    }
    if let Some(ref out) = output {
        if fs::symlink_metadata(out).is_ok() {
            return Err("Output exists; choose a new path".into());
        }
    }
    let mut projects = vec![];
    for path in paths {
        projects.push(scan::scan(&path, &options).map_err(|e| format!("{}: {e}", path.display()))?);
    }
    let incomplete = projects.iter().any(|p| {
        !p.warnings.is_empty()
            || p.files.iter().any(|f| {
                matches!(
                    f.status.as_str(),
                    "read-error" | "parse-error" | "changed-during-scan" | "parser-missing"
                ) || f.language != "other"
                    && matches!(f.status.as_str(), "size-limit" | "non-utf8" | "binary")
            })
    });
    let comparison = (command == "diff")
        .then(|| compare::compare(&projects[0], &projects[1], threshold, max_pairs, min_nodes));
    let changed = comparison
        .as_ref()
        .is_some_and(|c| c.details["has_differences"] == true);
    let write_report = |writer: &mut dyn Write| -> io::Result<()> {
        if format == "json" {
            if let Some(ref data) = comparison {
                serde_json::to_writer_pretty(&mut *writer, data).map_err(|e| {
                    io::Error::new(e.io_error_kind().unwrap_or(io::ErrorKind::Other), e)
                })?;
            } else {
                let data = ScanReport {
                    project: &projects[0],
                    duplicates: compare::duplicates(&[("", &projects[0])], min_nodes),
                };
                serde_json::to_writer_pretty(&mut *writer, &data).map_err(|e| {
                    io::Error::new(e.io_error_kind().unwrap_or(io::ErrorKind::Other), e)
                })?;
            }
            writer.write_all(b"\n")?;
        } else {
            let report = if let Some(ref data) = comparison {
                render::diff(data, ascii)
            } else if format == "dot" {
                render::dot(&projects[0])
            } else {
                render::project(&projects[0], &detail, ascii, min_nodes)
            };
            writer.write_all(report.as_bytes())?;
        }
        writer.flush()
    };
    if let Some(out) = output {
        let f = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&out)
            .map_err(|e| e.to_string())?;
        write_report(&mut BufWriter::with_capacity(64 * 1024, f)).map_err(|e| e.to_string())?;
        eprintln!("Saved: {}", render::safe(&out.to_string_lossy()));
    } else if let Err(e) = write_report(&mut BufWriter::with_capacity(
        64 * 1024,
        io::stdout().lock(),
    )) {
        if e.kind() != io::ErrorKind::BrokenPipe {
            return Err(e.to_string());
        }
    }
    Ok(if strict && incomplete {
        2
    } else if check && changed {
        1
    } else {
        0
    })
}
fn main() {
    let code = match run() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("grapholog-rs: {}", render::safe(&e));
            2
        }
    };
    std::process::exit(code);
}
