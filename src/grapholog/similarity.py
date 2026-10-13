"""Exact prefix filtering of v1 shingle sets; no probabilistic candidate loss."""
from collections import defaultdict


def search(left, right, threshold, max_pairs):
    groups = defaultdict(list)
    for file in right:
        if file.status == "parsed":
            groups[(file.language, file.parser)].append(file)
    indexed = {}
    for key, files in groups.items():
        sets = [set(f.fingerprints) for f in files]
        postings = defaultdict(list)
        for i, tokens in enumerate(sets):
            for token in tokens:
                postings[token].append(i)
        indexed[key] = files, sets, postings
    eligible = sum(len(groups[(f.language, f.parser)]) for f in left if f.status == "parsed")
    considered = verified = 0
    found = []
    for a in left:
        if considered >= max_pairs:
            break
        if a.status != "parsed" or (a.language, a.parser) not in indexed:
            continue
        files, sets, postings = indexed[(a.language, a.parser)]
        take = min(len(files), max_pairs - considered)
        considered += take  # Preserve the lexicographic v1 pair budget, including safe exclusions.
        one = set(a.fingerprints)
        if threshold <= 0:
            candidates = range(take)
        else:
            # J >= t implies intersection >= ceil(t*|A|). The slightly longer
            # floor-based prefix is conservative at floating-point boundaries.
            length = min(len(one), len(one) - int(threshold * len(one)) + 1)
            prefix = sorted(one, key=lambda t: (len(postings.get(t, ())), t))[:length]
            candidates = sorted({i for token in prefix for i in postings.get(token, ()) if i < take})
        for i in candidates:
            two = sets[i]
            if min(len(one), len(two)) / max(len(one), len(two)) < threshold:
                continue
            verified += 1
            overlap = len(one & two)
            score = overlap / (len(one) + len(two) - overlap)
            if score >= threshold:
                found.append({"left": a.path, "right": files[i].path, "score": round(score, 4),
                              "kind": "syntax-shingle-candidate"})
    return found, {"considered": considered, "eligible": eligible, "limit": max_pairs,
                   "truncated": eligible > considered, "threshold": threshold,
                   "verified": verified, "pruned": considered - verified,
                   "method": "exact-prefix-v1", "probabilistic": False}
