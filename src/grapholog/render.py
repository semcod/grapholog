from __future__ import annotations

from collections import Counter, defaultdict
import json
import unicodedata
from . import __version__
from .compare import duplicates
from .model import Project


def safe(value) -> str:
    """Prevent filenames/source strings from injecting terminal controls or bidi text."""
    result = []
    for char in str(value):
        if unicodedata.category(char).startswith("C"):
            code = ord(char)
            result.append(f"\\u{code:04x}" if code > 127 else f"\\x{code:02x}")
        else:
            result.append(char)
    return "".join(result)


def _runtime(data: dict) -> list[str]:
    cache = data.get("cache", {})
    workers = data.get("workers", 1)
    if "directory" not in cache and workers == 1:
        return []
    line = f"WORKERS {workers}"
    if "directory" in cache:
        line += " | CACHE " + ("on" if cache.get("enabled") else "unavailable")
        line += " " + " ".join(f"{k}={cache.get(k, 0)}" for k in ("hits", "misses", "writes", "invalid", "errors"))
    return [line] + ["  ! cache: " + safe(s) for s in cache.get("issues", [])]


def _tree(files: dict[str, list[str]], directories: list[str], ascii_only=False):
    children = defaultdict(list)
    for path in directories:
        parent, _, name = path.rpartition("/")
        children[parent].append((name + "/", path, True))
    for path in files:
        parent, _, name = path.rpartition("/")
        children[parent].append((name, path, False))
    for siblings in children.values():
        siblings.sort(key=lambda item: (not item[2], item[0]))
    tee, end, pipe, blank = ("|-- ", "`-- ", "|   ", "    ") if ascii_only else ("├── ", "└── ", "│   ", "    ")
    stack = [(entry, "", i == len(children[""]) - 1) for i, entry in reversed(list(enumerate(children[""])))]
    lines = []
    while stack:
        (name, path, directory), prefix, last = stack.pop()
        continuation = prefix + (blank if last else pipe)
        if directory:
            lines.append(prefix + (end if last else tee) + safe(name))
            stack.extend((entry, continuation, i == len(children[path]) - 1)
                         for i, entry in reversed(list(enumerate(children[path]))))
        else:
            content = files[path]
            lines.append(prefix + (end if last else tee) + content[0].replace("{name}", safe(name)))
            lines.extend(continuation + line for line in content[1:])
    return lines


def _coverage(project: Project) -> str:
    counts = Counter(file.status for file in project.files)
    return "  ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "pusty katalog"


def _dupes(data: dict, arrow: str) -> list[str]:
    labels = {"exact_files": "BAJTY =", "syntax_files": "SKLADNIA =", "shape_candidates": "NAZWY ~?",
              "function_clones": "FUNKCJE =*", "function_shape_candidates": "FUNKCJE ~?"}
    lines = []
    for category, groups in data.items():
        for group in groups:
            lines.append(f"  {labels[category]} " + f" {arrow} ".join(safe(p) for p in group))
    return lines or ["  Brak grup powyzej progu wielkosci."]


def _finish(lines: list[str], ascii_only: bool, color: bool) -> str:
    if ascii_only:
        lines = [line.encode("ascii", "backslashreplace").decode("ascii") for line in lines]
    if color:
        lines = ["\x1b[1;36m" + line + "\x1b[0m" if line and not line.startswith(" ")
                 and not any(c in line for c in ("├", "└", "│", "|--", "`--")) else line for line in lines]
    return "\n".join(lines) + "\n"


def render_project(project: Project, *, detail="compact", ascii_only=False,
                   color=False, min_nodes=12) -> str:
    arrow = "->" if ascii_only else "→"
    ids = {f.path: f"F{i:03}" for i, f in enumerate(project.files, 1)}
    lines = [f"GRAPHOLOG {__version__} | MAPA PROJEKTU", safe(project.root),
        f"{len(project.files)} plikow/linkow | {len(project.directories)} katalogow | "
        f"{sum(len(f.symbols) for f in project.files)} symboli | {len(project.excluded)} wykluczonych wpisow",
        "POKRYCIE  " + _coverage(project)]
    lines.extend(_runtime(project.runtime))
    lines.extend(["", "DRZEWO + ZALEZNOSCI"])
    views = {}
    for f in project.files:
        meta = [f"[{ids[f.path]}] {{name}}  {f.language}  {f.lines}L  {f.size}B  [{f.status}]"]
        if f.kind == "symlink":
            meta.append(f"link {arrow} {safe(f.link_target)}")
        if detail != "files":
            for item in f.imports:
                target = ", ".join(ids[t] + "?" for t in item.targets) or "? " + safe(item.status)
                meta.append(f"  import L{item.line} {safe(item.module)} {arrow} {target}")
            for symbol in f.symbols:
                meta.append(f"  {safe(symbol.kind)} {safe(symbol.qualname)} L{symbol.line}-{symbol.end_line} "
                            f"nodes={symbol.nodes} decisions={symbol.decisions}")
            if detail == "compact":
                calls = defaultdict(set)
                for call in f.calls:
                    target = safe(call.target) + "?" if call.target else safe(call.name) + "[?]"
                    calls[call.caller].add(target)
                for caller, targets in sorted(calls.items()):
                    meta.append(f"  calls {safe(caller)} {arrow} " + ", ".join(sorted(targets)))
            elif detail == "full":
                for event in f.logic:
                    meta.append(f"  L{event.line} {safe(event.scope)} | " + "  " * event.depth + safe(event.label))
                for call in f.calls:
                    if call.target:
                        meta.append(f"  call-link L{call.line} {safe(call.caller)} {arrow} {safe(call.target)}?")
        for note in f.notes:
            meta.append("  ! " + safe(note))
        views[f.path] = meta
    lines.extend(_tree(views, project.directories, ascii_only))
    lines.extend(["", "CYKLE IMPORTOW (skladowe grafu kandydatow)"])
    lines.extend("  {" + ", ".join(ids[p] + " " + safe(p) for p in group) + "}"
                 for group in project.cycles)
    if not project.cycles:
        lines.append("  Brak wykrytych cykli lokalnych.")
    lines.extend(["", f"DUPLIKATY I KANDYDACI (min. {min_nodes} wezlow dla skladni/funkcji)"])
    lines.extend(_dupes(duplicates([("", project)], min_nodes), arrow))
    lines.extend(_notes(project))
    lines.extend(["", "LEGENDA  = identyczne bajty; =* skladnia funkcji bez nazwy deklaracji; ~? podobny ksztalt.",
        "  ? oznacza kandydata statycznego. Brak linku nie dowodzi braku zaleznosci.",
        "  decisions = liczba konstrukcji decyzyjnych; nie jest dowodem zachowania ani pelnym CFG.",
        "  Wszystkie uwzglednione pliki sa wyswietlone; --all wlacza domyslnie wykluczone katalogi."])
    return _finish(lines, ascii_only, color)


def _notes(project: Project, label=""):
    lines = []
    if project.excluded:
        lines.extend(["", "WYKLUCZONE WPISY " + label,
            "  " + ", ".join(safe(p) for p in project.excluded),
            "  Wykluczone katalogi nie byly przegladane; licznik nie obejmuje ich zawartosci."])
    if project.warnings:
        lines.extend(["", "UWAGI " + label] + ["  ! " + safe(w) for w in project.warnings])
    return lines


STATUS = {"unchanged": "=", "changed": "~", "syntax-equal": "S", "identifier-change-candidate": "N?",
          "relocated-identical": "R=", "relocated-syntax-equal": "RS", "added": "+", "removed": "-",
          "unreadable": "!", "type-changed": "T", "mode-changed": "X"}


def render_compare(result: dict, *, ascii_only=False, color=False) -> str:
    arrow = "->" if ascii_only else "→"
    old, new = result["left"], result["right"]
    oldfiles, newfiles = {f["path"]: f for f in old["files"]}, {f["path"]: f for f in new["files"]}
    lines = [f"GRAPHOLOG {__version__} | POROWNANIE A {arrow} B", "A: " + safe(old["root"]),
             "B: " + safe(new["root"]),
             "  ".join(f"{safe(k)}={v}" for k, v in result["counts"].items()), "", "WSPOLNE DRZEWO"]
    views = {}
    for pair in result["pairs"]:
        path = pair["right"] or pair["left"]
        status = pair["status"]
        content = [f"[{STATUS[status]}] {{name}}  {status}"]
        if pair["left"] and pair["right"] and pair["left"] != pair["right"]:
            content.append(f"  {safe(pair['left'])} {arrow} {safe(pair['right'])}")
        for change, names in pair.get("symbol_delta", {}).items():
            if names:
                content.append(f"  symbols {change}: " + ", ".join(safe(n) for n in names))
        if pair.get("executable_changed"):
            content.append("  executable bit changed")
        for label, files, key in (("A", oldfiles, "left"), ("B", newfiles, "right")):
            if pair.get(key):
                f = files[pair[key]]
                if f["status"] not in {"parsed", "text", "binary", "symlink"}:
                    content.append(f"  ! {label}: {f['status']}")
                for note in f["notes"]:
                    content.append(f"  ! {label}: {safe(note)}")
        views[path] = content
    directories = sorted(set(old["directories"]) | set(new["directories"]))
    lines.extend(_tree(views, directories, ascii_only))
    if result["directories_added"] or result["directories_removed"]:
        lines.extend(["", "ZMIANY KATALOGOW"])
        for label, key in (("+", "directories_added"), ("-", "directories_removed")):
            lines.extend(f"  {label} {safe(path)}/" for path in result[key])
    lines.extend(["", "ZMIANY ZALEZNOSCI (po dopasowaniu przeniesien)"])
    for label, key in (("+", "dependencies_added"), ("-", "dependencies_removed")):
        for src, kind, dst, state in result[key]:
            lines.append(f"  {label} {safe(src)} --{safe(kind)}{arrow} {safe(dst)} [{safe(state)}]")
    if not result["dependencies_added"] and not result["dependencies_removed"]:
        lines.append("  Brak zmian wykrytych zaleznosci.")
    lines.extend(["", "PODOBNE PLIKI BEZ DOPASOWANIA (kandydaci)"])
    for match in result["similarities"]:
        lines.append(f"  {100 * match['score']:.1f}%  {safe(match['left'])} {arrow} {safe(match['right'])}")
    if not result["similarities"]:
        lines.append("  Brak kandydatow powyzej progu.")
    search = result["similarity_search"]
    lines.append(f"  Porownano {search['considered']}/{search['eligible']} par, prog={search['threshold']:.0%}.")
    if search["truncated"]:
        lines.append("  ! Osiagnieto --max-pairs; lista kandydatow jest niepelna.")
    lines.extend(["", "DUPLIKATY A/B (grupy obejmuja tez kopie wewnatrz projektu)"])
    lines.extend(_dupes(result["duplicates"], arrow))
    for label, project in (("A", old), ("B", new)):
        lines.extend(["", "POKRYCIE " + label,
            "  " + "  ".join(f"{k}={v}" for k, v in sorted(Counter(f["status"] for f in project["files"]).items()))])
        if project["excluded"]:
            lines.append("  wykluczone wpisy: " + ", ".join(safe(p) for p in project["excluded"]))
        lines.extend("  ! " + safe(w) for w in project["warnings"])
        lines.extend(_runtime(project.get("runtime", {})))
    lines.extend(["", "LEGENDA  = bajty bez zmian; S zgodna skladnia; N? zmiana nazw-kandydat; ~ zmiana;",
        "  R=/RS to jednoznaczna para tresci pod inna sciezka, nie historia Git; + dodany; - usuniety;",
        "  X zmiana wykonywalnosci; T zmiana typu; ! brak wiarygodnego odczytu.",
        "  Zgodna skladnia i podobienstwo nie stanowia dowodu rownowaznosci zachowania."])
    return _finish(lines, ascii_only, color)


def to_dot(project: Project) -> str:
    quote = lambda value: json.dumps(safe(value), ensure_ascii=True)
    lines = ["digraph grapholog {", '  rankdir="LR";', '  node [shape="box", fontname="monospace"];']
    for file in project.files:
        lines.append(f"  {quote(file.path)} [label={quote(file.path + ' [' + file.status + ']')}];")
    external = set()
    for file in project.files:
        for item in file.imports:
            targets = item.targets or ["external:" + item.module]
            for target in targets:
                if not item.targets and target not in external:
                    lines.append(f"  {quote(target)} [style=dashed, label={quote(item.module + ' ?')}];")
                    external.add(target)
                lines.append(f"  {quote(file.path)} -> {quote(target)} [label={quote(item.kind + ' ?')}];")
    lines.append("}")
    return "\n".join(lines) + "\n"
