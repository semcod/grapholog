"""Real Python AST. Inferred links are candidates, never a runtime trace."""
from __future__ import annotations

import ast
import io
import tokenize
from .model import Call, File, Import, Logic, Symbol, digest, shingles

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
CONTROLS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
            ast.AsyncWith, ast.Match, ast.IfExp, ast.ExceptHandler)
DECISIONS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp,
             ast.ExceptHandler, ast.comprehension, ast.match_case)
NAME_FIELDS = {"id", "arg", "name", "attr", "asname", "rest"}


def canonical(node: ast.AST, *, shape: bool = False, body: bool = False) -> list[str]:
    """Preserve order, operators and literal types/values. Ignore locations only."""
    names: dict[str, str] = {}
    out: list[str] = []
    stack = [("value", node, "", True)]
    while stack:
        mode, value, field, root = stack.pop()
        if mode == "token":
            out.append(value)
        elif isinstance(value, ast.AST):
            out.append("(" + type(value).__name__)
            stack.append(("token", ")", "", False))
            fields = [(k, v) for k, v in ast.iter_fields(value)
                      if k not in {"lineno", "col_offset", "end_lineno", "end_col_offset"}]
            for key, val in reversed(fields):
                if body and root and key == "name":
                    val = "<declaration>"
                stack.append(("value", val, key, False))
                stack.append(("token", key + "=", "", False))
        elif isinstance(value, list):
            out.append("[")
            stack.append(("token", "]", "", False))
            stack.extend(("value", x, field, False) for x in reversed(value))
        else:
            if shape and isinstance(value, str) and field in NAME_FIELDS:
                value = names.setdefault(value, f"id{len(names)}")
            out.append(type(value).__name__ + ":" + repr(value))
    return out


def name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = name_of(node.value)
        return f"{base}.{node.attr}" if base else "<dynamic>." + node.attr
    return "<dynamic>"


def decisions(node: ast.AST) -> int:
    """Syntactic decision count for this scope, excluding nested declarations."""
    stack = list(ast.iter_child_nodes(node))
    count = 0
    while stack:
        item = stack.pop()
        if isinstance(item, DEFINITIONS):
            continue
        if isinstance(item, DECISIONS):
            count += 1
        elif isinstance(item, ast.BoolOp):
            count += max(0, len(item.values) - 1)
        stack.extend(ast.iter_child_nodes(item))
    return count


def parse_python(file: File, raw: bytes) -> None:
    file.parser = "python.ast"
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        tree = ast.parse(source, filename=file.path, type_comments=True)
    except (SyntaxError, UnicodeError, LookupError, ValueError, RecursionError) as exc:
        file.status = "parse-error"
        file.notes.append(f"{type(exc).__name__}: {exc}")
        return
    file.status = "parsed"
    file.lines = len(source.splitlines())
    tokens = canonical(tree)
    shape = canonical(tree, shape=True)
    file.syntax_hash = digest(tokens)
    file.shape_hash = digest(shape)
    file.fingerprints = shingles(tokens)
    file.nodes = sum(1 for _ in ast.walk(tree))
    # Iterative traversal: visitors cannot overflow on long chains of ordinary nodes.
    stack: list[tuple[ast.AST, str, int]] = [(tree, "<module>", 0)]
    while stack:
        node, scope, depth = stack.pop()
        if isinstance(node, DEFINITIONS):
            qualname = node.name if scope == "<module>" else scope + "." + node.name
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            if isinstance(node, ast.AsyncFunctionDef):
                kind = "async function"
            file.symbols.append(Symbol(node.name, qualname, kind, node.lineno,
                node.end_lineno or node.lineno, digest(canonical(node)),
                digest(canonical(node, body=True)), digest(canonical(node, shape=True)),
                sum(1 for _ in ast.walk(node)), decisions(node)))
            scope, depth = qualname, 0
        if isinstance(node, ast.Import):
            for item in node.names:
                bound = item.asname or item.name.split(".")[0]
                file.imports.append(Import(item.name, "import", node.lineno,
                    bindings={bound: "*" if item.asname else "@module:" + item.name}))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            file.imports.append(Import(module, "from", node.lineno,
                [n.name for n in node.names],
                {n.asname or n.name: n.name for n in node.names}))
        elif isinstance(node, ast.Call):
            target = name_of(node.func)
            file.calls.append(Call(scope, target, node.lineno))
            file.logic.append(Logic(scope, "call", node.lineno, depth, target + "()"))
            # Literal dynamic imports are recorded without executing them.
            if target in {"importlib.import_module", "__import__"} and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    file.imports.append(Import(node.args[0].value, "dynamic-literal", node.lineno))
                else:
                    file.notes.append(f"L{node.lineno}: dynamic import; unresolved")
        if isinstance(node, CONTROLS):
            kind = type(node).__name__.lower()
            expr = getattr(node, "test", None)
            if isinstance(node, (ast.For, ast.AsyncFor)):
                label = "for " + ast.unparse(node.target) + " in " + ast.unparse(node.iter)
            elif isinstance(node, ast.Match):
                label = "match " + ast.unparse(node.subject)
            elif expr is not None:
                label = kind + " " + ast.unparse(expr)
            else:
                label = kind
            file.logic.append(Logic(scope, kind, getattr(node, "lineno", 1), depth, label))
            depth += 1
        elif isinstance(node, (ast.Return, ast.Raise, ast.Break, ast.Continue, ast.Await, ast.Yield)):
            label = type(node).__name__.lower()
            file.logic.append(Logic(scope, label, node.lineno, depth, label))
        stack.extend((child, scope, depth) for child in reversed(list(ast.iter_child_nodes(node))))
