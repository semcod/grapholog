use crate::model::*;
use std::collections::{BTreeSet, HashMap, HashSet};

fn parent(path: &str) -> &str {
    path.rsplit_once('/').map(|p| p.0).unwrap_or("")
}

fn norm(path: &str) -> String {
    if path.starts_with('/') {
        return String::new();
    }
    let mut parts = vec![];
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return String::new();
                }
            }
            _ => parts.push(part),
        }
    }
    parts.join("/")
}

fn join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        String::new()
    } else if a.is_empty() {
        norm(b)
    } else {
        norm(&format!("{a}/{b}"))
    }
}

fn py_module(name: &str, roots: &[String], files: &HashSet<String>) -> Vec<String> {
    let mut result = BTreeSet::new();
    let rel_module = name.replace('.', "/");
    for root in roots {
        for suffix in [".py", ".pyi", "/__init__.py", "/__init__.pyi"] {
            let path = join(root, &format!("{rel_module}{suffix}"));
            if files.contains(&path) {
                result.insert(path);
            }
        }
    }
    result.into_iter().collect()
}

fn py_name(path: &str, module: &str, roots: &[String]) -> String {
    if !module.starts_with('.') {
        return module.into();
    }
    let level = module.chars().take_while(|c| *c == '.').count();
    let mut dir = parent(path);
    if let Some(root) = roots
        .iter()
        .filter(|r| r.as_str() != "." && (dir == r.as_str() || dir.starts_with(&format!("{r}/"))))
        .max_by_key(|r| r.len())
    {
        dir = dir[root.len()..].trim_start_matches('/');
    }
    let mut parts = if dir.is_empty() {
        vec![]
    } else {
        dir.split('/').collect::<Vec<_>>()
    };
    if level > parts.len() {
        return "<invalid-relative>".into();
    }
    parts.truncate(parts.len() - level + 1);
    if !module[level..].is_empty() {
        parts.push(&module[level..]);
    }
    parts.join(".")
}

struct TargetInfo {
    stem: String,
    // qualname -> Vec<symbol_ref>
    symbols: HashMap<String, Vec<String>>,
}

pub fn resolve(p: &mut Project, roots: &[String]) {
    let regular: HashSet<String> = p
        .files
        .iter()
        .filter(|f| f.kind == "file")
        .map(|f| f.path.clone())
        .collect();

    for f in &mut p.files {
        for item in &mut f.imports {
            let mut module = item.module.clone();
            let mut targets = vec![];
            match f.language.as_str() {
                "python" => {
                    module = py_name(&f.path, &module, roots);
                    targets = py_module(&module, roots, &regular);
                    if item.kind == "from" {
                        for name in &item.names {
                            if name != "*" {
                                targets.extend(py_module(
                                    &format!("{module}.{name}"),
                                    roots,
                                    &regular,
                                ));
                            }
                        }
                    }
                }
                "javascript" | "typescript" | "tsx" if module.starts_with('.') => {
                    let path = join(parent(&f.path), &module);
                    if regular.contains(&path) {
                        targets.push(path);
                    } else {
                        let mut candidates = vec![];
                        for ext in [".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json"] {
                            candidates.push(format!("{path}{ext}"));
                        }
                        for ext in [".js", ".ts", ".tsx", ".jsx"] {
                            candidates.push(format!("{path}/index{ext}"));
                        }
                        if [".js", ".mjs", ".cjs"].iter().any(|e| path.ends_with(e)) {
                            let stem = path.rsplit_once('.').unwrap().0;
                            for ext in [".ts", ".tsx", ".mts", ".cts"] {
                                candidates.push(format!("{stem}{ext}"));
                            }
                        }
                        targets.extend(candidates.into_iter().filter(|c| regular.contains(c)));
                    }
                }
                "php" | "bash" if matches!(item.kind.as_str(), "include" | "source") => {
                    let candidates = if let Some(suffix) = module.strip_prefix("@DIR@/") {
                        vec![join(parent(&f.path), suffix)]
                    } else {
                        vec![join(parent(&f.path), &module), norm(&module)]
                    };
                    targets.extend(candidates.into_iter().filter(|c| regular.contains(c)));
                }
                _ => {}
            }
            targets.sort();
            targets.dedup();
            item.targets = targets;
            item.status = if !item.targets.is_empty() {
                "local-candidate"
            } else if module.starts_with('.')
                || module.starts_with("@DIR@")
                || matches!(item.kind.as_str(), "include" | "source")
            {
                "unresolved-local"
            } else {
                "external-or-unresolved"
            }
            .into();
        }
    }

    let mut index: HashMap<String, TargetInfo> = HashMap::with_capacity(p.files.len());
    for f in &p.files {
        let stem = f
            .path
            .rsplit('/')
            .next()
            .unwrap_or(&f.path)
            .split('.')
            .next()
            .unwrap_or("")
            .to_string();
        let mut symbols: HashMap<String, Vec<String>> = HashMap::with_capacity(f.symbols.len());
        for s in &f.symbols {
            symbols
                .entry(s.qualname.clone())
                .or_default()
                .push(symbol_ref(f, s));
        }
        index.insert(f.path.clone(), TargetInfo { stem, symbols });
    }

    for f in &mut p.files {
        for c in &mut f.calls {
            let mut name = c.name.clone();
            let mut candidates = BTreeSet::new();
            if ["self.", "cls.", "this.", "$this."]
                .iter()
                .any(|s| name.starts_with(s))
                && c.caller.contains('.')
            {
                name = format!(
                    "{}.{}",
                    c.caller.rsplit_once('.').unwrap().0,
                    name.split_once('.').unwrap().1
                );
            }
            if let Some(target_info) = index.get(&f.path) {
                if let Some(refs) = target_info.symbols.get(&name) {
                    for r in refs {
                        candidates.insert(r.as_str());
                    }
                }
            }
            if !name.contains('.') && c.caller != "<module>" {
                let mut prefix = c.caller.as_str();
                loop {
                    let wanted = format!("{prefix}.{name}");
                    if let Some(target_info) = index.get(&f.path) {
                        if let Some(refs) = target_info.symbols.get(&wanted) {
                            for r in refs {
                                candidates.insert(r.as_str());
                            }
                            break;
                        }
                    }
                    if let Some((parent_prefix, _)) = prefix.rsplit_once('.') {
                        prefix = parent_prefix;
                    } else {
                        break;
                    }
                }
            }
            let (head, tail) = name.split_once('.').unwrap_or((&name, ""));
            for item in &f.imports {
                if let Some(original) = item.bindings.get(head) {
                    let remainder = if let Some(module) = original.strip_prefix("@module:") {
                        name.strip_prefix(&format!("{module}."))
                            .unwrap_or("")
                            .to_string()
                    } else if original == "*" {
                        tail.to_string()
                    } else if tail.is_empty() {
                        original.clone()
                    } else {
                        format!("{original}.{tail}")
                    };
                    for target in &item.targets {
                        if let Some(target_info) = index.get(target) {
                            if let Some(refs) = target_info.symbols.get(&remainder) {
                                for r in refs {
                                    candidates.insert(r.as_str());
                                }
                            }
                            if !tail.is_empty() && &target_info.stem == original {
                                if let Some(refs) = target_info.symbols.get(tail) {
                                    for r in refs {
                                        candidates.insert(r.as_str());
                                    }
                                }
                            }
                        }
                    }
                }
            }
            if candidates.len() == 1 {
                c.target = candidates.into_iter().next().unwrap().to_string();
                c.resolution = "candidate".into();
            } else if candidates.len() > 1 {
                c.resolution = "ambiguous".into();
            }
        }
    }
    p.cycles = cycles(p);
}

fn cycles(p: &Project) -> Vec<Vec<String>> {
    let mut all_paths: Vec<&str> = p.files.iter().map(|f| f.path.as_str()).collect();
    for f in &p.files {
        for i in &f.imports {
            for t in &i.targets {
                all_paths.push(t.as_str());
            }
        }
    }
    all_paths.sort_unstable();
    all_paths.dedup();

    let path_to_id: HashMap<&str, usize> = all_paths
        .iter()
        .enumerate()
        .map(|(i, &p)| (p, i))
        .collect();

    let n = all_paths.len();
    let mut adj = vec![Vec::new(); n];
    let mut rev = vec![Vec::new(); n];
    let mut self_loops = vec![false; n];

    for f in &p.files {
        if let Some(&src_id) = path_to_id.get(f.path.as_str()) {
            for item in &f.imports {
                for target in &item.targets {
                    if let Some(&dst_id) = path_to_id.get(target.as_str()) {
                        if src_id == dst_id {
                            self_loops[src_id] = true;
                        }
                        adj[src_id].push(dst_id);
                        rev[dst_id].push(src_id);
                    }
                }
            }
        }
    }

    for neighbors in &mut adj {
        neighbors.sort_unstable();
        neighbors.dedup();
    }
    for neighbors in &mut rev {
        neighbors.sort_unstable();
        neighbors.dedup();
    }

    let mut seen = vec![false; n];
    let mut order = Vec::with_capacity(n);
    for start in 0..n {
        if seen[start] {
            continue;
        }
        let mut stack = vec![(start, 0usize)];
        seen[start] = true;
        while let Some((u, edge_idx)) = stack.pop() {
            if edge_idx < adj[u].len() {
                let v = adj[u][edge_idx];
                stack.push((u, edge_idx + 1));
                if !seen[v] {
                    seen[v] = true;
                    stack.push((v, 0));
                }
            } else {
                order.push(u);
            }
        }
    }

    seen.fill(false);
    let mut groups = Vec::new();
    for &start in order.iter().rev() {
        if seen[start] {
            continue;
        }
        let mut group = Vec::new();
        let mut stack = vec![start];
        seen[start] = true;
        while let Some(u) = stack.pop() {
            group.push(all_paths[u].to_string());
            for &prev in &rev[u] {
                if !seen[prev] {
                    seen[prev] = true;
                    stack.push(prev);
                }
            }
        }
        if group.len() > 1 || self_loops[start] {
            group.sort();
            groups.push(group);
        }
    }
    groups.sort();
    groups
}
