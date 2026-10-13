use crate::model::*;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(serde::Serialize)]
pub struct Comparison<'a> {
    pub left: &'a Project,
    pub right: &'a Project,
    #[serde(flatten)]
    pub details: Value,
}

pub fn duplicates(projects: &[(&str, &Project)], minimum: usize) -> Value {
    let mut groups = BTreeMap::<String, BTreeMap<String, Vec<(String, String)>>>::new();
    for category in [
        "exact_files",
        "syntax_files",
        "shape_candidates",
        "function_clones",
        "function_shape_candidates",
    ] {
        groups.insert(category.into(), BTreeMap::new());
    }
    for (prefix, p) in projects {
        for f in &p.files {
            let mut add = |category: &str, key: String, r: String, variant: String| {
                groups
                    .get_mut(category)
                    .unwrap()
                    .entry(key)
                    .or_default()
                    .push((r, variant));
            };
            let r = format!("{prefix}{}", f.path);
            if !f.sha256.is_empty() {
                add(
                    "exact_files",
                    format!("{}:{}", f.kind, f.sha256),
                    r.clone(),
                    String::new(),
                );
            }
            if f.status == "parsed" && f.nodes >= minimum {
                add(
                    "syntax_files",
                    format!("{}:{}", f.parser, f.syntax_hash),
                    r.clone(),
                    f.sha256.clone(),
                );
                add(
                    "shape_candidates",
                    format!("{}:{}", f.parser, f.shape_hash),
                    r.clone(),
                    f.syntax_hash.clone(),
                );
            }
            for s in &f.symbols {
                if s.nodes < minimum
                    || s.body_hash.is_empty()
                    || matches!(
                        s.kind.as_str(),
                        "class" | "interface" | "type" | "enum" | "trait"
                    )
                    || f.status != "parsed"
                {
                    continue;
                }
                let r = format!("{prefix}{}", symbol_ref(f, s));
                add(
                    "function_clones",
                    format!("{}:{}", f.parser, s.body_hash),
                    r.clone(),
                    String::new(),
                );
                add(
                    "function_shape_candidates",
                    format!("{}:{}", f.parser, s.shape_hash),
                    r,
                    s.body_hash.clone(),
                );
            }
        }
    }
    let mut result = serde_json::Map::new();
    for (category, values) in groups {
        let mut out = vec![];
        for group in values.into_values() {
            if group.len() < 2 {
                continue;
            }
            if !matches!(category.as_str(), "exact_files" | "function_clones")
                && group.iter().map(|x| &x.1).collect::<BTreeSet<_>>().len() < 2
            {
                continue;
            }
            let mut refs = group.into_iter().map(|x| x.0).collect::<Vec<_>>();
            refs.sort();
            out.push(refs);
        }
        out.sort();
        result.insert(category, json!(out));
    }
    Value::Object(result)
}
fn status(a: &File, b: &File) -> &'static str {
    if a.kind != b.kind {
        return "type-changed";
    }
    if a.sha256.is_empty() || b.sha256.is_empty() {
        return "unreadable";
    }
    if a.sha256 == b.sha256 {
        return if a.executable == b.executable {
            "unchanged"
        } else {
            "mode-changed"
        };
    }
    if a.status == "parsed"
        && b.status == "parsed"
        && a.parser == b.parser
        && a.language == b.language
    {
        if a.syntax_hash == b.syntax_hash {
            return "syntax-equal";
        }
        if a.shape_hash == b.shape_hash {
            return "identifier-change-candidate";
        }
    }
    "changed"
}
fn edges(p: &Project, mapping: &BTreeMap<String, String>) -> BTreeSet<Vec<String>> {
    let mapped = |s: &str| mapping.get(s).cloned().unwrap_or_else(|| s.to_string());
    let mut result = BTreeSet::new();
    for f in &p.files {
        for item in &f.imports {
            if item.targets.is_empty() {
                result.insert(vec![
                    mapped(&f.path),
                    item.kind.clone(),
                    item.module.clone(),
                    item.status.clone(),
                ]);
            } else {
                for t in &item.targets {
                    result.insert(vec![
                        mapped(&f.path),
                        item.kind.clone(),
                        mapped(t),
                        "local".into(),
                    ]);
                }
            }
        }
    }
    result
}
pub fn compare<'a>(
    a: &'a Project,
    b: &'a Project,
    threshold: f64,
    max_pairs: usize,
    min_nodes: usize,
) -> Comparison<'a> {
    let old = a
        .files
        .iter()
        .map(|f| (f.path.clone(), f))
        .collect::<BTreeMap<_, _>>();
    let new = b
        .files
        .iter()
        .map(|f| (f.path.clone(), f))
        .collect::<BTreeMap<_, _>>();
    let mut removed = old
        .keys()
        .filter(|k| !new.contains_key(*k))
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut added = new
        .keys()
        .filter(|k| !old.contains_key(*k))
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut pairs = vec![];
    for (p, f) in &old {
        if let Some(g) = new.get(p) {
            pairs.push(json!({"left":p,"right":p,"status":status(f,g)}));
        }
    }
    for mode in ["bytes", "syntax"] {
        let key = |f: &&File| {
            if mode == "bytes" && !f.sha256.is_empty() {
                Some(format!("{}:{}", f.kind, f.sha256))
            } else if mode == "syntax" && f.status == "parsed" && !f.syntax_hash.is_empty() {
                Some(format!("{}:{}", f.parser, f.syntax_hash))
            } else {
                None
            }
        };
        let mut left = BTreeMap::<String, Vec<String>>::new();
        let mut right = BTreeMap::<String, Vec<String>>::new();
        for p in &removed {
            if let Some(k) = key(&old[p]) {
                left.entry(k).or_default().push(p.clone());
            }
        }
        for p in &added {
            if let Some(k) = key(&new[p]) {
                right.entry(k).or_default().push(p.clone());
            }
        }
        for (k, paths) in left {
            if let Some(others) = right.get(&k) {
                if paths.len() == 1 && others.len() == 1 {
                    let x = &paths[0];
                    let y = &others[0];
                    pairs.push(json!({"left":x,"right":y,"status":if mode=="bytes"{"relocated-identical"}else{"relocated-syntax-equal"}}));
                    removed.remove(x);
                    added.remove(y);
                }
            }
        }
    }
    for pair in &mut pairs {
        let f = old[pair["left"].as_str().unwrap()];
        let g = new[pair["right"].as_str().unwrap()];
        let mut ls = BTreeMap::<String, Vec<String>>::new();
        let mut rs = BTreeMap::<String, Vec<String>>::new();
        for s in &f.symbols {
            ls.entry(s.qualname.clone())
                .or_default()
                .push(s.syntax_hash.clone());
        }
        for s in &g.symbols {
            rs.entry(s.qualname.clone())
                .or_default()
                .push(s.syntax_hash.clone());
        }
        pair["symbol_delta"] = json!({"added":rs.keys().filter(|k|!ls.contains_key(*k)).collect::<Vec<_>>(),
            "removed":ls.keys().filter(|k|!rs.contains_key(*k)).collect::<Vec<_>>(),"changed":ls.keys().filter(|k|rs.contains_key(*k)&&ls[*k]!=rs[*k]).collect::<Vec<_>>()});
        pair["executable_changed"] = json!(f.executable != g.executable);
    }
    for p in &removed {
        pairs.push(json!({"left":p,"right":null,"status":"removed"}));
    }
    for p in &added {
        pairs.push(json!({"left":null,"right":p,"status":"added"}));
    }
    pairs.sort_by_key(|p| {
        p["right"]
            .as_str()
            .or(p["left"].as_str())
            .unwrap()
            .to_string()
    });
    let mut counts = BTreeMap::new();
    let mut mapping = BTreeMap::new();
    for p in &pairs {
        *counts
            .entry(p["status"].as_str().unwrap().to_string())
            .or_insert(0) += 1;
        if let (Some(x), Some(y)) = (p["left"].as_str(), p["right"].as_str()) {
            mapping.insert(x.into(), y.into());
        }
    }
    let left_candidates = removed
        .iter()
        .map(|p| old[p])
        .filter(|f| f.nodes >= min_nodes && !f.fingerprints.is_empty())
        .collect::<Vec<_>>();
    let right_candidates = added
        .iter()
        .map(|p| new[p])
        .filter(|f| f.nodes >= min_nodes && !f.fingerprints.is_empty())
        .collect::<Vec<_>>();
    let (mut similar, search_stats) =
        crate::similarity::search(&left_candidates, &right_candidates, threshold, max_pairs);
    similar.sort_by(|x, y| {
        y["score"]
            .as_f64()
            .unwrap()
            .total_cmp(&x["score"].as_f64().unwrap())
            .then_with(|| x["left"].as_str().cmp(&y["left"].as_str()))
            .then_with(|| x["right"].as_str().cmp(&y["right"].as_str()))
    });
    let old_edges = edges(a, &mapping);
    let new_edges = edges(b, &BTreeMap::new());
    let da = a.directories.iter().collect::<BTreeSet<_>>();
    let db = b.directories.iter().collect::<BTreeSet<_>>();
    let changed = pairs.iter().any(|p| p["status"] != "unchanged") || da != db;
    let details = json!({"schema":"grapholog.compare.v1","pairs":pairs,"counts":counts,
        "directories_added":db.difference(&da).collect::<Vec<_>>(),"directories_removed":da.difference(&db).collect::<Vec<_>>(),
        "dependencies_added":new_edges.difference(&old_edges).collect::<Vec<_>>(),"dependencies_removed":old_edges.difference(&new_edges).collect::<Vec<_>>(),
        "similarities":similar,"similarity_search":search_stats,
        "duplicates":duplicates(&[("A:",a),("B:",b)],min_nodes),"has_differences":changed,
        "limitations":["Static candidates, not runtime or semantic equivalence.","Similarity search applies to unmatched removed/added files only.","Relocation is content matching, not VCS history."]});
    Comparison {
        left: a,
        right: b,
        details,
    }
}
