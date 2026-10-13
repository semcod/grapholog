use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};

pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn digest(tokens: &[String]) -> String {
    hash(&serde_json::to_vec(tokens).unwrap())
}
pub fn shingles(tokens: &[String]) -> Vec<String> {
    if tokens.is_empty() {
        return vec![];
    }
    let width = 5.min(tokens.len());
    // Preserve the v1 JSON-array byte contract, including escaping and UTF-8.
    let mut encoded = HashMap::new();
    for token in tokens {
        encoded
            .entry(token)
            .or_insert_with(|| serde_json::to_vec(token).unwrap());
    }
    let mut seen = HashSet::new();
    let mut hashes = Vec::new();
    let mut buffer = Vec::new();
    for part in tokens.windows(width) {
        if !seen.insert(part) {
            continue;
        }
        buffer.clear();
        buffer.push(b'[');
        for (i, token) in part.iter().enumerate() {
            if i > 0 {
                buffer.push(b',');
            }
            buffer.extend_from_slice(&encoded[token]);
        }
        buffer.push(b']');
        let digest = Sha256::digest(&buffer);
        hashes.push(u64::from_be_bytes(digest[..8].try_into().unwrap()));
    }
    hashes.sort_unstable();
    hashes.dedup();
    hashes.into_iter().map(|h| format!("{h:016x}")).collect()
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Symbol {
    pub name: String,
    pub qualname: String,
    pub kind: String,
    pub line: usize,
    pub end_line: usize,
    pub syntax_hash: String,
    pub body_hash: String,
    pub shape_hash: String,
    pub nodes: usize,
    pub decisions: usize,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Import {
    pub module: String,
    pub kind: String,
    pub line: usize,
    pub names: Vec<String>,
    pub bindings: BTreeMap<String, String>,
    pub targets: Vec<String>,
    pub status: String,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Call {
    pub caller: String,
    pub name: String,
    pub line: usize,
    pub target: String,
    pub resolution: String,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Logic {
    pub scope: String,
    pub kind: String,
    pub line: usize,
    pub depth: usize,
    pub label: String,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct File {
    pub path: String,
    pub kind: String,
    pub language: String,
    pub size: u64,
    pub lines: usize,
    pub sha256: String,
    pub parser: String,
    pub status: String,
    pub syntax_hash: String,
    pub shape_hash: String,
    pub fingerprints: Vec<String>,
    pub nodes: usize,
    pub symbols: Vec<Symbol>,
    pub imports: Vec<Import>,
    pub calls: Vec<Call>,
    pub logic: Vec<Logic>,
    pub notes: Vec<String>,
    pub link_target: String,
    pub executable: bool,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Project {
    pub root: String,
    pub files: Vec<File>,
    pub directories: Vec<String>,
    pub excluded: Vec<String>,
    pub warnings: Vec<String>,
    pub options: serde_json::Value,
    pub cycles: Vec<Vec<String>>,
    pub schema: String,
    #[serde(default)]
    pub runtime: serde_json::Value,
}
pub fn symbol_ref(file: &File, symbol: &Symbol) -> String {
    format!("{}::{}@{}", file.path, symbol.qualname, symbol.line)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shingles_preserve_v1_json_byte_contract() {
        let alphabet = [
            "(",
            ")",
            "x",
            "10",
            "1000",
            "\\",
            "\"",
            "\n",
            "\0",
            "zażółć",
            "😀",
            "\u{2028}",
        ];
        for n in [0, 1, 4, 5, 6, 80, 1000] {
            let tokens = (0..n)
                .map(|i| alphabet[(i * i + i * 7) % alphabet.len()].to_string())
                .collect::<Vec<_>>();
            let mut expected = if n == 0 {
                vec![]
            } else {
                tokens
                    .windows(5.min(n))
                    .map(|w| digest(w)[..16].to_string())
                    .collect::<Vec<_>>()
            };
            expected.sort();
            expected.dedup();
            assert_eq!(shingles(&tokens), expected);
        }
    }
}
