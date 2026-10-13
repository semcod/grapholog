use crate::model::File;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};

struct Index<'a> {
    files: Vec<&'a File>,
    postings: HashMap<&'a str, Vec<usize>>,
}

pub fn search(
    left: &[&File],
    right: &[&File],
    threshold: f64,
    limit: usize,
) -> (Vec<Value>, Value) {
    let mut groups = BTreeMap::new();
    for &f in right.iter().filter(|f| f.status == "parsed") {
        let group = groups
            .entry((f.language.as_str(), f.parser.as_str()))
            .or_insert_with(|| Index {
                files: vec![],
                postings: HashMap::new(),
            });
        let i = group.files.len();
        group.files.push(f);
        for token in &f.fingerprints {
            group.postings.entry(token).or_default().push(i);
        }
    }
    let eligible: usize = left
        .iter()
        .filter(|f| f.status == "parsed")
        .map(|f| {
            groups
                .get(&(f.language.as_str(), f.parser.as_str()))
                .map_or(0, |g| g.files.len())
        })
        .sum();
    let (mut considered, mut verified) = (0, 0);
    let mut found = vec![];
    for &a in left {
        if considered >= limit {
            break;
        }
        if a.status != "parsed" {
            continue;
        }
        let Some(group) = groups.get(&(a.language.as_str(), a.parser.as_str())) else {
            continue;
        };
        let take = group.files.len().min(limit - considered);
        considered += take;
        let one = &a.fingerprints;
        let candidates: Vec<usize> = if threshold <= 0.0 {
            (0..take).collect()
        } else {
            // Any qualifying pair must share a token in this prefix of A.
            // Using floor rather than ceil extends the prefix conservatively.
            let length = one
                .len()
                .min(one.len() - (threshold * one.len() as f64) as usize + 1);
            let mut prefix = one.iter().map(String::as_str).collect::<Vec<_>>();
            prefix.sort_unstable_by_key(|t| (group.postings.get(t).map_or(0, Vec::len), *t));
            let mut ids = HashSet::new();
            for token in &prefix[..length] {
                if let Some(postings) = group.postings.get(token) {
                    ids.extend(postings.iter().copied().take_while(|i| *i < take));
                }
            }
            let mut ids = ids.into_iter().collect::<Vec<_>>();
            ids.sort_unstable();
            ids
        };
        for i in candidates {
            let b = group.files[i];
            let two = &b.fingerprints;
            if one.len().min(two.len()) as f64 / (one.len().max(two.len()) as f64) < threshold {
                continue;
            }
            verified += 1;
            // Fingerprints are sorted unique arrays: no per-pair tree allocations.
            let (mut x, mut y, mut overlap) = (0, 0, 0);
            while x < one.len() && y < two.len() {
                match one[x].cmp(&two[y]) {
                    std::cmp::Ordering::Less => x += 1,
                    std::cmp::Ordering::Greater => y += 1,
                    std::cmp::Ordering::Equal => {
                        overlap += 1;
                        x += 1;
                        y += 1;
                    }
                }
            }
            let score = overlap as f64 / (one.len() + two.len() - overlap) as f64;
            if score >= threshold {
                found.push(
                    json!({"left":a.path,"right":b.path,"score":(score*10000.0).round()/10000.0,
                    "kind":"syntax-shingle-candidate"}),
                );
            }
        }
    }
    (
        found,
        json!({"considered":considered,"eligible":eligible,"limit":limit,"truncated":eligible>considered,
        "threshold":threshold,"verified":verified,"pruned":considered-verified,"method":"exact-prefix-v1","probabilistic":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    #[test]
    fn indexed_search_matches_exhaustive_oracle() {
        let mut seed = 391u64;
        let mut make = |prefix: &str| -> Vec<File> {
            (0..40)
                .map(|i| {
                    let tokens = (0..70)
                        .filter_map(|n| {
                            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                            (seed >> 56 < (i * 6 + 5) as u64 || n == 0).then(|| format!("{n:03}"))
                        })
                        .collect();
                    File {
                        path: format!("{prefix}{i:03}"),
                        language: "js".into(),
                        parser: if i % 5 == 0 { "q" } else { "p" }.into(),
                        status: if i % 7 == 0 { "parse-error" } else { "parsed" }.into(),
                        fingerprints: tokens,
                        ..Default::default()
                    }
                })
                .collect()
        };
        let left = make("a");
        let mut right = make("b");
        right[1].fingerprints = left[1].fingerprints.clone();
        for threshold in [0.0, 0.1, 1.0 / 3.0, 0.7, 0.9999999999999999, 1.0] {
            for limit in [0, 1, 33, 700, 10000] {
                let mut expected = vec![];
                let mut considered = 0;
                for a in &left {
                    for b in &right {
                        if considered >= limit
                            || a.status != "parsed"
                            || b.status != "parsed"
                            || a.parser != b.parser
                        {
                            continue;
                        }
                        considered += 1;
                        let x = a.fingerprints.iter().collect::<BTreeSet<_>>();
                        let y = b.fingerprints.iter().collect::<BTreeSet<_>>();
                        let score = x.intersection(&y).count() as f64 / x.union(&y).count() as f64;
                        if score >= threshold {
                            expected.push(json!({"left":a.path,"right":b.path,"score":(score*10000.0).round()/10000.0,
                        "kind":"syntax-shingle-candidate"}));
                        }
                    }
                }
                let (actual, stats) = search(
                    &left.iter().collect::<Vec<_>>(),
                    &right.iter().collect::<Vec<_>>(),
                    threshold,
                    limit,
                );
                assert_eq!(actual, expected, "threshold={threshold} limit={limit}");
                assert_eq!(stats["considered"], considered);
                assert_eq!(
                    stats["verified"].as_u64().unwrap() + stats["pruned"].as_u64().unwrap(),
                    considered as u64
                );
            }
        }
    }
}
