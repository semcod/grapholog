use crate::{compare::duplicates, model::*};
use serde_json::Value;
use std::collections::BTreeMap;

pub fn safe(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_control() || matches!(c,'\u{202a}'..='\u{202e}'|'\u{2066}'..='\u{2069}') {
                format!("\\u{:04x}", c as u32)
            } else {
                c.to_string()
            }
        })
        .collect()
}
fn coverage(p: &Project) -> String {
    let mut c = BTreeMap::new();
    for f in &p.files {
        *c.entry(&f.status).or_insert(0) += 1;
    }
    c.iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("  ")
}
fn duplicate_lines(data: &Value) -> Vec<String> {
    let mut out = vec![];
    for (kind, groups) in data.as_object().unwrap() {
        for group in groups.as_array().unwrap() {
            out.push(format!(
                "  {kind}: {}",
                group
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|p| safe(p.as_str().unwrap()))
                    .collect::<Vec<_>>()
                    .join(" = ")
            ));
        }
    }
    if out.is_empty() {
        out.push("  Brak grup.".into());
    }
    out
}
fn tree(views: &BTreeMap<String, Vec<String>>, dirs: &[String], ascii: bool) -> Vec<String> {
    let mut children = BTreeMap::<String, Vec<(String, bool)>>::new();
    for dir in dirs {
        let parent = dir.rsplit_once('/').map(|x| x.0).unwrap_or("");
        children
            .entry(parent.into())
            .or_default()
            .push((dir.clone(), true));
    }
    for file in views.keys() {
        let parent = file.rsplit_once('/').map(|x| x.0).unwrap_or("");
        children
            .entry(parent.into())
            .or_default()
            .push((file.clone(), false));
    }
    for siblings in children.values_mut() {
        siblings.sort_by_key(|(s, d)| (!*d, s.clone()));
    }
    let (tee, end, pipe) = if ascii {
        ("|-- ", "`-- ", "|   ")
    } else {
        ("├── ", "└── ", "│   ")
    };
    let mut stack = vec![];
    if let Some(items) = children.get("") {
        for (i, item) in items.iter().enumerate().rev() {
            stack.push((item.clone(), String::new(), i + 1 == items.len()));
        }
    }
    let mut out = vec![];
    while let Some(((path, dir), prefix, last)) = stack.pop() {
        let branch = if last { end } else { tee };
        let more = format!("{prefix}{}", if last { "    " } else { pipe });
        let name = safe(path.rsplit('/').next().unwrap());
        if dir {
            out.push(format!("{prefix}{branch}{name}/"));
            if let Some(items) = children.get(&path) {
                for (i, item) in items.iter().enumerate().rev() {
                    stack.push((item.clone(), more.clone(), i + 1 == items.len()));
                }
            }
        } else {
            let items = &views[&path];
            out.push(format!(
                "{prefix}{branch}{}",
                items[0].replace("{name}", &name)
            ));
            out.extend(items.iter().skip(1).map(|s| format!("{more}{s}")));
        }
    }
    out
}
fn runtime(data: &Value) -> Vec<String> {
    let cache = &data["cache"];
    let workers = data["workers"].as_u64().unwrap_or(1);
    if cache["directory"].is_null() && workers == 1 {
        return vec![];
    }
    let mut line = format!("WORKERS {workers}");
    if !cache["directory"].is_null() {
        line += if cache["enabled"] == true {
            " | CACHE on"
        } else {
            " | CACHE unavailable"
        };
        for key in ["hits", "misses", "writes", "invalid", "errors"] {
            line += &format!(" {key}={}", cache[key]);
        }
    }
    let mut out = vec![line];
    if let Some(issues) = cache["issues"].as_array() {
        out.extend(
            issues
                .iter()
                .map(|s| format!("  ! cache: {}", safe(s.as_str().unwrap_or("cache error")))),
        );
    }
    out
}

pub fn project(p: &Project, detail: &str, ascii: bool, min_nodes: usize) -> String {
    let ids = p
        .files
        .iter()
        .enumerate()
        .map(|(i, f)| (f.path.clone(), format!("F{:03}", i + 1)))
        .collect::<BTreeMap<_, _>>();
    let mut out = vec![
        "GRAPHOLOG-RS 0.5.0 | MAPA PROJEKTU".into(),
        safe(&p.root),
        format!(
            "{} plikow/linkow | {} katalogow",
            p.files.len(),
            p.directories.len()
        ),
        format!("POKRYCIE {}", coverage(p)),
    ];
    out.extend(runtime(&p.runtime));
    out.push(String::new());
    let mut views = BTreeMap::new();
    for f in &p.files {
        let mut lines = vec![format!(
            "[{}] {{name}}  {} {}L {}B [{}]",
            ids[&f.path], f.language, f.lines, f.size, f.status
        )];
        if f.kind == "symlink" {
            lines.push(format!("  link -> {}", safe(&f.link_target)));
        }
        if detail != "files" {
            for i in &f.imports {
                lines.push(format!(
                    "  import L{} {} -> {}",
                    i.line,
                    safe(&i.module),
                    if i.targets.is_empty() {
                        format!("? {}", i.status)
                    } else {
                        i.targets
                            .iter()
                            .map(|t| format!("{}?", ids[t]))
                            .collect::<Vec<_>>()
                            .join(", ")
                    }
                ));
            }
            for s in &f.symbols {
                lines.push(format!(
                    "  {} {} L{}-{} nodes={} decisions={}",
                    s.kind,
                    safe(&s.qualname),
                    s.line,
                    s.end_line,
                    s.nodes,
                    s.decisions
                ));
            }
            if detail == "full" {
                for e in &f.logic {
                    lines.push(format!(
                        "  L{} {} | {}{}",
                        e.line,
                        safe(&e.scope),
                        "  ".repeat(e.depth),
                        safe(&e.label)
                    ));
                }
            }
            for c in &f.calls {
                lines.push(format!(
                    "  calls L{} {} -> {}?",
                    c.line,
                    safe(&c.caller),
                    safe(if c.target.is_empty() {
                        &c.name
                    } else {
                        &c.target
                    })
                ));
            }
        }
        for note in &f.notes {
            lines.push(format!("  ! {}", safe(note)));
        }
        views.insert(f.path.clone(), lines);
    }
    out.extend(tree(&views, &p.directories, ascii));
    out.push("\nCYKLE IMPORTOW (skladowe kandydatow)".into());
    for group in &p.cycles {
        out.push(format!(
            "  {{{}}}",
            group.iter().map(|s| safe(s)).collect::<Vec<_>>().join(", ")
        ));
    }
    if p.cycles.is_empty() {
        out.push("  Brak.".into());
    }
    out.push("\nDUPLIKATY / KANDYDACI".into());
    out.extend(duplicate_lines(&duplicates(&[("", p)], min_nodes)));
    out.push(format!(
        "\nWYKLUCZONE WPISY: {}",
        p.excluded
            .iter()
            .map(|s| safe(s))
            .collect::<Vec<_>>()
            .join(", ")
    ));
    for w in &p.warnings {
        out.push(format!("! {}", safe(w)));
    }
    out.push("\n? = kandydat statyczny; brak linku nie dowodzi braku zaleznosci.\nDrzewo Tree-sitter to CST; nie dowodzi rownowaznosci zachowania. --all wlacza wykluczone katalogi.".into());
    finish(out, ascii)
}
fn finish(lines: Vec<String>, ascii: bool) -> String {
    let s = lines.join("\n") + "\n";
    if ascii {
        s.chars()
            .map(|c| {
                if c.is_ascii() {
                    c.to_string()
                } else {
                    format!("\\u{:04x}", c as u32)
                }
            })
            .collect()
    } else {
        s
    }
}
pub fn diff(report: &crate::compare::Comparison, ascii: bool) -> String {
    let data = &report.details;
    let mut out = vec![
        "GRAPHOLOG-RS 0.5.0 | POROWNANIE A -> B".into(),
        format!("A: {}", safe(&report.left.root)),
        format!("B: {}", safe(&report.right.root)),
        data["counts"].to_string(),
    ];
    let mut views = BTreeMap::new();
    for pair in data["pairs"].as_array().unwrap() {
        let path = pair["right"].as_str().or(pair["left"].as_str()).unwrap();
        let status = pair["status"].as_str().unwrap();
        let marker = match status {
            "unchanged" => "=",
            "added" => "+",
            "removed" => "-",
            "relocated-identical" => "R=",
            "syntax-equal" => "S",
            "identifier-change-candidate" => "N?",
            "relocated-syntax-equal" => "RS",
            _ => "~",
        };
        let mut lines = vec![format!("[{marker}] {{name}}  {status}")];
        if let (Some(left), Some(right)) = (pair["left"].as_str(), pair["right"].as_str()) {
            if left != right {
                lines.push(format!("  {} -> {}", safe(left), safe(right)));
            }
        }
        if let Some(delta) = pair["symbol_delta"].as_object() {
            for (key, names) in delta {
                if !names.as_array().unwrap().is_empty() {
                    lines.push(format!("  symbols {key}: {}", safe(&names.to_string())));
                }
            }
        }
        if pair["executable_changed"] == true {
            lines.push("  executable bit changed".into());
        }
        views.insert(path.into(), lines);
    }
    let mut dirs = report
        .left
        .directories
        .iter()
        .chain(&report.right.directories)
        .cloned()
        .collect::<Vec<_>>();
    dirs.sort();
    dirs.dedup();
    out.extend(tree(&views, &dirs, ascii));
    for key in [
        "directories_added",
        "directories_removed",
        "dependencies_added",
        "dependencies_removed",
        "similarities",
    ] {
        out.push(format!("\n{key}"));
        for item in data[key].as_array().unwrap() {
            out.push(format!("  {}", safe(&item.to_string())));
        }
    }
    out.push(format!("\nSIMILARITY SEARCH {}", data["similarity_search"]));
    out.push("\nDUPLIKATY A/B".into());
    out.extend(duplicate_lines(&data["duplicates"]));
    for (key, project) in [("left", report.left), ("right", report.right)] {
        out.push(format!("\nPOKRYCIE {key}"));
        let mut counts = BTreeMap::new();
        for f in &project.files {
            *counts.entry(f.status.as_str()).or_insert(0) += 1;
            if !matches!(f.status.as_str(), "parsed" | "text" | "binary" | "symlink") {
                out.push(format!(
                    "  ! {} [{}]",
                    safe(&f.path),
                    serde_json::to_string(&f.status).unwrap()
                ));
            }
        }
        out.push(format!("  {counts:?}"));
        out.push(format!(
            "  excluded={}",
            serde_json::to_string(&project.excluded).unwrap()
        ));
        out.push(format!(
            "  warnings={}",
            serde_json::to_string(&project.warnings).unwrap()
        ));
        out.extend(runtime(&project.runtime));
    }
    out.push("\nS = zgodna skladnia; N? = kandydat zmiany nazw; R=/RS = dopasowanie tresci pod inna sciezka.\nZgodna skladnia nie dowodzi rownowaznosci zachowania.".into());
    finish(out, ascii)
}
pub fn dot(p: &Project) -> String {
    let q = |s: &str| serde_json::to_string(&safe(s)).unwrap();
    let mut out = vec![
        "digraph grapholog {".into(),
        "  rankdir=LR; node [shape=box];".into(),
    ];
    for f in &p.files {
        out.push(format!("  {};", q(&f.path)));
        for item in &f.imports {
            let targets = if item.targets.is_empty() {
                vec![format!("external:{}", item.module)]
            } else {
                item.targets.clone()
            };
            for target in targets {
                out.push(format!(
                    "  {} -> {} [label={}];",
                    q(&f.path),
                    q(&target),
                    q(&format!("{} ?", item.kind))
                ));
            }
        }
    }
    out.push("}".into());
    out.join("\n") + "\n"
}
