"""Conservative, filesystem-based candidates; no type inference or project execution."""
from __future__ import annotations

from collections import defaultdict
import posixpath
from .model import Project, symbol_ref


def norm(path: str) -> str:
    result = posixpath.normpath(path)
    return result if result != ".." and not result.startswith(("../", "/")) else ""


def resolve(project: Project) -> None:
    files = {f.path: f for f in project.files}
    regular = {p for p, f in files.items() if f.kind == "file"}
    roots = project.options.get("source_roots", [".", "src"])

    def py_module(module: str) -> list[str]:
        path = module.replace(".", "/")
        return sorted({candidate for root in roots for suffix in (".py", ".pyi", "/__init__.py", "/__init__.pyi")
            if (candidate := norm(posixpath.join(root, path) + suffix)) in regular})

    def py_name(file, module):
        if not module.startswith("."):
            return module
        level = len(module) - len(module.lstrip("."))
        directory = posixpath.dirname(file.path)
        applicable = [r.strip("/") for r in roots if r not in {".", ""}
                      and (directory == r or directory.startswith(r.rstrip("/") + "/"))]
        root = max(applicable, key=len, default="")
        if root:
            directory = directory[len(root):].lstrip("/")
        parts = directory.split("/") if directory else []
        if level > len(parts):
            return "<invalid-relative>"
        base = parts[:len(parts) - level + 1]
        tail = module[level:]
        return ".".join(base + ([tail] if tail else []))

    def js_paths(path):
        candidates = [path]
        candidates += [path + ext for ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json")]
        candidates += [path.rstrip("/") + "/index" + ext for ext in (".js", ".ts", ".tsx", ".jsx")]
        if path.endswith((".js", ".mjs", ".cjs")):
            stem = path.rsplit(".", 1)[0]
            candidates.extend(stem + ext for ext in (".ts", ".tsx", ".mts", ".cts"))
        # Prefer an exact filename. Alternative extension/index matches stay ambiguous.
        if path in regular:
            return [path]
        return sorted({p for p in candidates if p in regular})

    for file in project.files:
        for item in file.imports:
            targets = []
            module = item.module
            if file.language == "python":
                module = py_name(file, module)
                targets = py_module(module)
                if item.kind == "from":
                    for name in item.names:
                        if name != "*":
                            targets.extend(py_module(module + "." + name))
            elif file.language in {"javascript", "typescript", "tsx"}:
                if module.startswith("."):
                    targets = js_paths(norm(posixpath.join(posixpath.dirname(file.path), module)))
            elif file.language in {"php", "bash"} and item.kind in {"include", "source"}:
                if module.startswith("@DIR@/"):
                    candidates = [norm(posixpath.join(posixpath.dirname(file.path), module[6:]))]
                else:
                    # PHP include_path and shell CWD are runtime-dependent.
                    candidates = [norm(posixpath.join(posixpath.dirname(file.path), module)), norm(module)]
                targets = [p for p in candidates if p in regular]
            item.targets = sorted(set(targets))
            if item.targets:
                item.status = "local-candidate"
            elif module.startswith((".", "@DIR@")) or item.kind in {"include", "source"}:
                item.status = "unresolved-local"
            else:
                item.status = "external-or-unresolved"

    for file in project.files:
        local = defaultdict(list)
        for symbol in file.symbols:
            local[symbol.qualname].append(symbol_ref(file, symbol))
        for call in file.calls:
            candidates = []
            name = call.name
            scope = call.caller
            # A name may be rebound at runtime. Even these lexical matches remain candidates.
            if name.startswith(("self.", "cls.", "this.", "$this.")) and "." in scope:
                name = scope.rsplit(".", 1)[0] + "." + name.split(".", 1)[1]
            if name in local:
                candidates.extend(local[name])
            if "." not in name:
                parts = [] if scope == "<module>" else scope.split(".")
                while parts:
                    qualified = ".".join(parts + [name])
                    if qualified in local:
                        candidates.extend(local[qualified])
                        break
                    parts.pop()
            head, _, tail = name.partition(".")
            for item in file.imports:
                if head not in item.bindings:
                    continue
                original = item.bindings[head]
                if original.startswith("@module:"):
                    module = original[len("@module:"):]
                    remainder = name[len(module) + 1:] if name.startswith(module + ".") else ""
                elif original == "*":
                    remainder = tail
                else:
                    remainder = original + ("." + tail if tail else "")
                for target in item.targets:
                    # from pkg import module; module.function()
                    match_names = {remainder}
                    if tail and posixpath.basename(target).split(".")[0] == original:
                        match_names.add(tail)
                    for symbol in files[target].symbols:
                        if symbol.qualname in match_names:
                            candidates.append(symbol_ref(files[target], symbol))
            candidates = sorted(set(candidates))
            if len(candidates) == 1:
                call.target, call.resolution = candidates[0], "candidate"
            elif candidates:
                call.resolution = "ambiguous"
    project.cycles = dependency_cycles(project)


def dependency_cycles(project: Project) -> list[list[str]]:
    """Iterative Kosaraju SCC: handles dependency chains beyond recursion limits."""
    adjacency = {f.path: set(t for i in f.imports for t in i.targets) for f in project.files}
    reverse = {p: set() for p in adjacency}
    for src, targets in adjacency.items():
        for target in targets:
            reverse.setdefault(target, set()).add(src)
    seen, order = set(), []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack = [(start, False)]
        while stack:
            current, done = stack.pop()
            if done:
                order.append(current)
            elif current not in seen:
                seen.add(current)
                stack.append((current, True))
                stack.extend((p, False) for p in sorted(adjacency.get(current, ()), reverse=True) if p not in seen)
    seen, groups = set(), []
    for start in reversed(order):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            current = stack.pop()
            group.append(current)
            for parent in sorted(reverse.get(current, ())):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        if len(group) > 1 or start in adjacency.get(start, ()):
            groups.append(sorted(group))
    return sorted(groups)
