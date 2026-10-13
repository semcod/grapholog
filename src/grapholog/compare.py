from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from .model import File, Project, symbol_ref
from .similarity import search


def _groups(items, key, minimum=2):
    groups = defaultdict(list)
    for item in items:
        value = key(item)
        if value:
            groups[value].append(item)
    return [group for _, group in sorted(groups.items(), key=lambda kv: str(kv[0])) if len(group) >= minimum]


def duplicates(projects: list[tuple[str, Project]], min_nodes: int = 12) -> dict:
    records = [(prefix, file) for prefix, p in projects for file in p.files]
    ref = lambda r: r[0] + r[1].path
    result = {"exact_files": [], "syntax_files": [], "shape_candidates": [], "function_clones": [],
              "function_shape_candidates": []}
    for group in _groups(records, lambda r: (r[1].kind, r[1].sha256) if r[1].sha256 else None):
        result["exact_files"].append(sorted(ref(r) for r in group))
    parsed = [r for r in records if r[1].status == "parsed" and r[1].nodes >= min_nodes]
    for group in _groups(parsed, lambda r: (r[1].parser, r[1].syntax_hash)):
        if len({r[1].sha256 for r in group}) > 1:
            result["syntax_files"].append(sorted(ref(r) for r in group))
    for group in _groups(parsed, lambda r: (r[1].parser, r[1].shape_hash)):
        if len({r[1].syntax_hash for r in group}) > 1:
            result["shape_candidates"].append(sorted(ref(r) for r in group))
    symbols = [(prefix, f, s) for prefix, f in records for s in f.symbols
               if s.kind not in {"class", "interface", "type", "enum", "trait"}
               and s.nodes >= min_nodes and s.body_hash and f.status == "parsed"]
    sref = lambda r: r[0] + symbol_ref(r[1], r[2])
    for group in _groups(symbols, lambda r: (r[1].parser, r[2].body_hash)):
        result["function_clones"].append(sorted(sref(r) for r in group))
    for group in _groups(symbols, lambda r: (r[1].parser, r[2].shape_hash)):
        if len({r[2].body_hash for r in group}) > 1:
            result["function_shape_candidates"].append(sorted(sref(r) for r in group))
    return {k: sorted(v) for k, v in result.items()}


def compatible(a: File, b: File) -> bool:
    return a.parser == b.parser and a.language == b.language and a.status == b.status == "parsed"


def file_status(a: File, b: File) -> str:
    if a.kind != b.kind:
        return "type-changed"
    if not a.sha256 or not b.sha256:
        return "unreadable"
    if a.sha256 == b.sha256:
        return "unchanged" if a.executable == b.executable else "mode-changed"
    if compatible(a, b) and a.syntax_hash == b.syntax_hash:
        return "syntax-equal"
    if compatible(a, b) and a.shape_hash == b.shape_hash:
        return "identifier-change-candidate"
    return "changed"


def _symbol_delta(a: File, b: File) -> dict:
    # Repeated overloads/redefinitions are compared as ordered sequences per qualified name.
    old, new = defaultdict(list), defaultdict(list)
    for symbol in a.symbols:
        old[symbol.qualname].append(symbol.syntax_hash)
    for symbol in b.symbols:
        new[symbol.qualname].append(symbol.syntax_hash)
    return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k])}


def _edge_set(project: Project, rename: dict | None = None) -> set[tuple]:
    rename = rename or {}
    result = set()
    for f in project.files:
        for item in f.imports:
            if item.targets:
                for target in item.targets:
                    result.add((rename.get(f.path, f.path), item.kind, rename.get(target, target), "local"))
            else:
                result.add((rename.get(f.path, f.path), item.kind, item.module, item.status))
    return result


def compare(left: Project, right: Project, *, threshold: float = 0.70,
            max_pairs: int = 20000, min_nodes: int = 12) -> dict:
    old, new = {f.path: f for f in left.files}, {f.path: f for f in right.files}
    pairs = [{"left": p, "right": p, "status": file_status(old[p], new[p])}
             for p in sorted(old.keys() & new.keys())]
    removed, added = set(old.keys() - new.keys()), set(new.keys() - old.keys())
    # Pair only unambiguous 1:1 candidates. Many-to-many copies are duplicate groups.
    for mode in ("bytes", "syntax"):
        def key(f):
            if mode == "bytes":
                return (f.kind, f.sha256) if f.sha256 else None
            return (f.parser, f.syntax_hash) if f.status == "parsed" and f.syntax_hash else None
        a, b = defaultdict(list), defaultdict(list)
        for path in removed:
            if value := key(old[path]):
                a[value].append(path)
        for path in added:
            if value := key(new[path]):
                b[value].append(path)
        for value in sorted(a.keys() & b.keys()):
            if len(a[value]) == len(b[value]) == 1:
                src, dst = a[value][0], b[value][0]
                pairs.append({"left": src, "right": dst,
                              "status": "relocated-identical" if mode == "bytes" else "relocated-syntax-equal"})
                removed.remove(src)
                added.remove(dst)
    for pair in pairs:
        a, b = old[pair["left"]], new[pair["right"]]
        pair["symbol_delta"] = _symbol_delta(a, b)
        pair["executable_changed"] = a.executable != b.executable
    for path in sorted(removed):
        pairs.append({"left": path, "right": None, "status": "removed"})
    for path in sorted(added):
        pairs.append({"left": None, "right": path, "status": "added"})
    pairs.sort(key=lambda p: (p["right"] or p["left"], p["status"]))

    left_candidates = [old[p] for p in sorted(removed) if old[p].nodes >= min_nodes and old[p].fingerprints]
    right_candidates = [new[p] for p in sorted(added) if new[p].nodes >= min_nodes and new[p].fingerprints]
    similarities, search_stats = search(left_candidates, right_candidates, threshold, max_pairs)
    mapping = {p["left"]: p["right"] for p in pairs if p["left"] and p["right"]}
    old_edges, new_edges = _edge_set(left, mapping), _edge_set(right)
    directory_added = sorted(set(right.directories) - set(left.directories))
    directory_removed = sorted(set(left.directories) - set(right.directories))
    result = {"schema": "grapholog.compare.v1", "left": left.to_dict(), "right": right.to_dict(),
        "pairs": pairs, "counts": dict(sorted(Counter(p["status"] for p in pairs).items())),
        "directories_added": directory_added, "directories_removed": directory_removed,
        "dependencies_added": [list(e) for e in sorted(new_edges - old_edges)],
        "dependencies_removed": [list(e) for e in sorted(old_edges - new_edges)],
        "similarities": sorted(similarities, key=lambda p: (-p["score"], p["left"], p["right"])),
        "similarity_search": search_stats,
        "duplicates": duplicates([("A:", left), ("B:", right)], min_nodes),
        "has_differences": any(p["status"] != "unchanged" for p in pairs) or bool(directory_added or directory_removed),
        "limitations": ["Static candidates, not runtime or semantic equivalence.",
                        "Similarity search applies to unmatched removed/added files only.",
                        "Relocation means unambiguous equal content at different paths; it is not VCS history."]}
    return result
