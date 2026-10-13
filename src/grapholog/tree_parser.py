"""Optional Tree-sitter adapters. CST-derived structure, no regular-expression parser."""
from __future__ import annotations

import ast
import importlib
from functools import lru_cache
from .model import Call, File, Import, Logic, Symbol, digest, shingles

GRAMMARS = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "php": ("tree_sitter_php", "language_php"),
    "bash": ("tree_sitter_bash", "language"),
}
DEFINITIONS = {
    "function_declaration": "function", "generator_function_declaration": "generator",
    "function_definition": "function", "function_expression": "function",
    "arrow_function": "function", "method_definition": "method",
    "method_declaration": "method", "class_declaration": "class",
    "interface_declaration": "interface", "type_alias_declaration": "type",
    "enum_declaration": "enum", "trait_declaration": "trait",
}
CONTROLS = {"if_statement", "else_clause", "elif_clause", "for_statement", "for_in_statement",
    "foreach_statement", "while_statement", "do_statement", "switch_statement",
    "switch_case", "case_statement", "case_item", "try_statement", "catch_clause",
    "finally_clause", "ternary_expression", "conditional_expression"}
CALLS = {"call_expression", "function_call_expression", "member_call_expression",
         "scoped_call_expression", "new_expression", "object_creation_expression", "command"}
IDENTIFIERS = {"identifier", "type_identifier", "property_identifier", "name",
               "variable_name", "shorthand_property_identifier", "shorthand_property_identifier_pattern"}
COMMENTS = {"comment", "hash_bang_line"}


@lru_cache(maxsize=None)
def get_parser(language: str):
    from tree_sitter import Language, Parser
    module, function = GRAMMARS[language]
    capsule = getattr(importlib.import_module(module), function)()
    return Parser(Language(capsule))


def text(node, raw: bytes) -> str:
    return raw[node.start_byte:node.end_byte].decode("utf-8", "replace") if node else ""


def literal(node, raw: bytes) -> str | None:
    """Accept only statically literal paths, never expand shell/PHP/JS expressions."""
    if node is None:
        return None
    value = text(node, raw)
    if node.type in {"string", "raw_string", "encapsed_string", "string_literal"}:
        if any(c.type in {"variable_name", "template_substitution", "expansion",
                          "simple_expansion", "command_substitution"} for c in node.named_children):
            return None
        try:
            result = ast.literal_eval(value)
            return result if isinstance(result, str) else None
        except (SyntaxError, ValueError):
            return None
    if node.type == "word" and not any(c in value for c in "$`*?{}~"):
        return value
    if node.type == "name" and value == "__DIR__":
        return "@DIR@"
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        return literal(node.named_children[0], raw)
    if node.type == "binary_expression":
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        op = text(node.child_by_field_name("operator"), raw)
        if not op:
            op = "".join(text(n, raw) for n in node.children if not n.is_named)
        if op == ".":
            a, b = literal(left, raw), literal(right, raw)
            if a is not None and b is not None:
                return a + b
    return None


def canonical(root, raw: bytes, *, shape=False, body=False) -> list[str]:
    names: dict[str, str] = {}
    tokens: list[str] = []
    root_name = root.child_by_field_name("name") if body else None
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            tokens.append(node)
            continue
        if node.type in COMMENTS:
            continue
        tokens.append("(" + node.type)
        stack.append(")")
        if root_name is not None and node.id == root_name.id:
            tokens.append("<declaration>")
        elif shape and node.type in IDENTIFIERS:
            value = text(node, raw)
            tokens.append(names.setdefault(value, f"id{len(names)}"))
        elif node.child_count:
            stack.extend(reversed(node.children))
        else:
            tokens.append(text(node, raw))
    return tokens


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def js_bindings(node, raw: bytes) -> dict[str, str]:
    bindings = {}
    for child in walk(node):
        if child.type == "import_specifier":
            name = text(child.child_by_field_name("name"), raw)
            alias = text(child.child_by_field_name("alias"), raw) or name
            bindings[alias] = name
        elif child.type == "namespace_import":
            ids = [n for n in child.named_children if n.type == "identifier"]
            if ids:
                bindings[text(ids[-1], raw)] = "*"
        elif child.type == "import_clause":
            for item in child.named_children:
                if item.type == "identifier":
                    bindings[text(item, raw)] = "default"
    return bindings


def add_imports(file: File, node, raw: bytes) -> None:
    line = node.start_point.row + 1
    if file.language in {"javascript", "typescript", "tsx"}:
        if node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            path = literal(source, raw)
            if path is not None:
                file.imports.append(Import(path, "re-export" if node.type == "export_statement" else "import",
                                          line, bindings=js_bindings(node, raw)))
        if node.type == "call_expression":
            func = text(node.child_by_field_name("function"), raw)
            if func in {"require", "import"}:
                args = node.child_by_field_name("arguments")
                path = literal(args.named_children[0], raw) if args and args.named_children else None
                if path is not None:
                    bindings = {}
                    parent = node.parent
                    if func == "require" and parent and parent.type == "variable_declarator":
                        binding = parent.child_by_field_name("name")
                        if binding and binding.type == "identifier":
                            bindings[text(binding, raw)] = "*"
                    file.imports.append(Import(path, func, line, bindings=bindings))
                else:
                    file.notes.append(f"L{line}: dynamic {func}; unresolved")
    elif file.language == "php":
        if node.type in {"include_expression", "include_once_expression", "require_expression", "require_once_expression"}:
            path = literal(node.named_children[-1], raw) if node.named_children else None
            if path is not None:
                file.imports.append(Import(path, "include", line))
            else:
                file.notes.append(f"L{line}: dynamic include; unresolved")
        elif node.type == "namespace_use_declaration":
            file.imports.append(Import(text(node, raw).removeprefix("use ").rstrip(";"), "namespace", line))
    elif file.language == "bash" and node.type == "command":
        name = text(node.child_by_field_name("name"), raw)
        if name in {"source", "."}:
            args = node.children_by_field_name("argument")
            path = literal(args[0], raw) if args else None
            if path is not None:
                file.imports.append(Import(path, "source", line))
            else:
                file.notes.append(f"L{line}: dynamic source; unresolved")


def parse_tree(file: File, raw: bytes) -> None:
    try:
        parser = get_parser(file.language)
    except ImportError:
        file.status = "parser-missing"
        file.notes.append("Install grapholog[parsers] for this language")
        return
    file.parser = "tree-sitter:" + file.language
    tree = parser.parse(raw)
    root = tree.root_node
    if root.has_error:
        file.status = "parse-error"
        errors = [n for n in walk(root) if n.type == "ERROR" or n.is_missing]
        file.notes.extend(f"L{n.start_point.row + 1}: {n.type} (partial syntax tree)" for n in errors[:10])
        if not errors:
            file.notes.append("Invalid or incomplete syntax; no syntax fingerprints")
    else:
        file.status = "parsed"
        tokens = canonical(root, raw)
        file.syntax_hash = digest(tokens)
        file.shape_hash = digest(canonical(root, raw, shape=True))
        file.fingerprints = shingles(tokens)
    file.nodes = sum(1 for _ in walk(root))
    stack = [(root, "<module>", 0)]
    while stack:
        node, scope, depth = stack.pop()
        if node.type in COMMENTS:
            continue
        add_imports(file, node, raw)
        line = node.start_point.row + 1
        if node.type in DEFINITIONS:
            name = text(node.child_by_field_name("name"), raw)
            if not name and node.parent and node.parent.type == "variable_declarator":
                name = text(node.parent.child_by_field_name("name"), raw)
            name = name or f"<anonymous@{line}:{node.start_point.column}>"
            qualname = name if scope == "<module>" else scope + "." + name
            descendants = list(walk(node))
            valid = file.status == "parsed"
            symbol = Symbol(name, qualname, DEFINITIONS[node.type], line, node.end_point.row + 1,
                digest(canonical(node, raw)) if valid else "",
                digest(canonical(node, raw, body=True)) if valid else "",
                digest(canonical(node, raw, shape=True)) if valid else "",
                len(descendants))
            local_stack = list(node.named_children)
            while local_stack:
                child = local_stack.pop()
                if child.type in DEFINITIONS:
                    continue
                symbol.decisions += int(child.type in CONTROLS - {"else_clause", "try_statement", "finally_clause"})
                local_stack.extend(child.named_children)
            file.symbols.append(symbol)
            scope, depth = qualname, 0
        if node.type in CALLS:
            callee = node.child_by_field_name("function") or node.child_by_field_name("constructor") or node.child_by_field_name("name")
            name = text(callee, raw) or "<dynamic>"
            if node.type in {"member_call_expression", "scoped_call_expression"}:
                owner = node.child_by_field_name("object") or node.child_by_field_name("scope")
                name = text(owner, raw) + "." + name
            file.calls.append(Call(scope, name, line))
            file.logic.append(Logic(scope, "call", line, depth, name + "()"))
        if node.type in CONTROLS:
            kind = node.type.removesuffix("_statement").removesuffix("_expression")
            condition = node.child_by_field_name("condition") or node.child_by_field_name("value")
            label = kind + (" " + text(condition, raw) if condition else "")
            file.logic.append(Logic(scope, kind, line, depth, label))
            depth += 1
        elif node.type in {"return_statement", "throw_statement", "break_statement", "continue_statement", "await_expression", "yield_expression"}:
            kind = node.type.split("_")[0]
            file.logic.append(Logic(scope, kind, line, depth, kind))
        stack.extend((child, scope, depth) for child in reversed(node.named_children))
