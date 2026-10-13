use crate::model::*;
use std::collections::BTreeMap;
use tree_sitter::{Language, Node, Parser};

fn field<'a>(n: Node<'a>, key: &str) -> Option<Node<'a>> {
    n.child_by_field_name(key)
}
fn txt(n: Node, raw: &[u8]) -> String {
    String::from_utf8_lossy(&raw[n.byte_range()]).into_owned()
}
fn ftxt(n: Node, key: &str, raw: &[u8]) -> String {
    field(n, key).map(|x| txt(x, raw)).unwrap_or_default()
}
fn children(n: Node, named: bool) -> Vec<Node> {
    let mut cursor = n.walk();
    if named {
        n.named_children(&mut cursor).collect()
    } else {
        n.children(&mut cursor).collect()
    }
}
fn count_named_nodes(n: Node) -> usize {
    let mut count = 0;
    let mut stack = vec![n];
    while let Some(node) = stack.pop() {
        count += 1;
        for i in (0..node.named_child_count()).rev() {
            if let Some(c) = node.named_child(i) {
                stack.push(c);
            }
        }
    }
    count
}
fn has_interpolation(n: Node) -> bool {
    let mut stack = vec![n];
    while let Some(node) = stack.pop() {
        if matches!(
            node.kind(),
            "variable_name"
                | "template_substitution"
                | "expansion"
                | "simple_expansion"
                | "command_substitution"
                | "interpolation"
        ) {
            return true;
        }
        for i in (0..node.named_child_count()).rev() {
            if let Some(c) = node.named_child(i) {
                stack.push(c);
            }
        }
    }
    false
}
fn walk(n: Node) -> Vec<Node> {
    let mut out = vec![];
    let mut stack = vec![n];
    while let Some(node) = stack.pop() {
        out.push(node);
        for i in (0..node.named_child_count()).rev() {
            if let Some(c) = node.named_child(i) {
                stack.push(c);
            }
        }
    }
    out
}
fn definition(kind: &str) -> Option<&'static str> {
    match kind {
        "function_declaration"
        | "function_definition"
        | "function_expression"
        | "arrow_function" => Some("function"),
        "generator_function_declaration" => Some("generator"),
        "method_definition" | "method_declaration" => Some("method"),
        "class_declaration" | "class_definition" => Some("class"),
        "interface_declaration" => Some("interface"),
        "type_alias_declaration" => Some("type"),
        "enum_declaration" => Some("enum"),
        "trait_declaration" => Some("trait"),
        _ => None,
    }
}
fn control(kind: &str) -> bool {
    matches!(
        kind,
        "if_statement"
            | "else_clause"
            | "elif_clause"
            | "for_statement"
            | "for_in_statement"
            | "foreach_statement"
            | "while_statement"
            | "do_statement"
            | "switch_statement"
            | "switch_case"
            | "case_statement"
            | "case_item"
            | "try_statement"
            | "catch_clause"
            | "finally_clause"
            | "ternary_expression"
            | "conditional_expression"
            | "with_statement"
            | "match_statement"
            | "except_clause"
    )
}

pub fn canonical(root: Node, raw: &[u8], shape: bool, body: bool) -> Vec<String> {
    let mut names = BTreeMap::<String, String>::new();
    let mut out = vec![];
    let root_name = if body {
        field(root, "name").map(|n| n.id())
    } else {
        None
    };
    let mut stack = vec![(Some(root), false)];
    while let Some((node, close)) = stack.pop() {
        if close {
            out.push(")".into());
            continue;
        }
        let n = node.unwrap();
        let kind = n.kind();
        if matches!(kind, "comment" | "hash_bang_line") {
            continue;
        }
        out.push(format!("({kind}"));
        stack.push((None, true));
        if Some(n.id()) == root_name {
            out.push("<declaration>".into());
        } else if shape
            && matches!(
                kind,
                "identifier"
                    | "type_identifier"
                    | "property_identifier"
                    | "name"
                    | "variable_name"
                    | "shorthand_property_identifier"
                    | "shorthand_property_identifier_pattern"
            )
        {
            let len = names.len();
            let id = names
                .entry(txt(n, raw))
                .or_insert_with(|| format!("id{len}"));
            out.push(id.clone());
        } else if n.child_count() > 0 {
            for i in (0..n.child_count()).rev() {
                if let Some(child) = n.child(i) {
                    stack.push((Some(child), false));
                }
            }
        } else {
            out.push(txt(n, raw));
        }
    }
    out
}

fn literal(node: Option<Node>, raw: &[u8]) -> Option<String> {
    let n = node?;
    let value = txt(n, raw);
    match n.kind() {
        "string" | "raw_string" | "encapsed_string" | "string_literal" => {
            if has_interpolation(n) {
                return None;
            }
            if value.starts_with('"') {
                serde_json::from_str(&value).ok()
            } else if value.starts_with('\'') && value.ends_with('\'') {
                let inner = &value[1..value.len() - 1];
                // No guessed decoding of language-specific escape sequences.
                if inner.contains('\\') {
                    None
                } else {
                    Some(inner.to_string())
                }
            } else {
                None
            }
        }
        "word" if !value.chars().any(|c| "$`*?{}~".contains(c)) => Some(value),
        "name" if value == "__DIR__" => Some("@DIR@".into()),
        "parenthesized_expression" if n.named_child_count() == 1 => literal(n.named_child(0), raw),
        "binary_expression"
            if ftxt(n, "operator", raw) == "."
                || children(n, false)
                    .iter()
                    .any(|c| !c.is_named() && txt(*c, raw) == ".") =>
        {
            Some(literal(field(n, "left"), raw)? + &literal(field(n, "right"), raw)?)
        }
        _ => None,
    }
}
fn import(module: String, kind: &str, line: usize, bindings: BTreeMap<String, String>) -> Import {
    Import {
        module,
        kind: kind.into(),
        line,
        bindings,
        status: "unresolved".into(),
        ..Default::default()
    }
}

fn add_imports(file: &mut File, n: Node, raw: &[u8]) {
    let line = n.start_position().row + 1;
    match file.language.as_str() {
        "javascript" | "typescript" | "tsx" => {
            if matches!(n.kind(), "import_statement" | "export_statement") {
                if let Some(path) = literal(field(n, "source"), raw) {
                    let mut bindings = BTreeMap::new();
                    for c in walk(n) {
                        if c.kind() == "import_specifier" {
                            let name = ftxt(c, "name", raw);
                            let alias = ftxt(c, "alias", raw);
                            bindings.insert(
                                if alias.is_empty() {
                                    name.clone()
                                } else {
                                    alias
                                },
                                name,
                            );
                        } else if c.kind() == "namespace_import" {
                            if let Some(id) = children(c, true)
                                .into_iter()
                                .filter(|x| x.kind() == "identifier")
                                .last()
                            {
                                bindings.insert(txt(id, raw), "*".into());
                            }
                        } else if c.kind() == "import_clause" {
                            for id in children(c, true)
                                .into_iter()
                                .filter(|x| x.kind() == "identifier")
                            {
                                bindings.insert(txt(id, raw), "default".into());
                            }
                        }
                    }
                    file.imports.push(import(
                        path,
                        if n.kind() == "export_statement" {
                            "re-export"
                        } else {
                            "import"
                        },
                        line,
                        bindings,
                    ));
                }
            }
            if n.kind() == "call_expression" {
                let name = ftxt(n, "function", raw);
                if matches!(name.as_str(), "require" | "import") {
                    let arg = field(n, "arguments").and_then(|a| a.named_child(0));
                    if let Some(path) = literal(arg, raw) {
                        let mut bindings = BTreeMap::new();
                        if name == "require" {
                            if let Some(parent) = n.parent() {
                                if parent.kind() == "variable_declarator" {
                                    if let Some(binding) = field(parent, "name") {
                                        if binding.kind() == "identifier" {
                                            bindings.insert(txt(binding, raw), "*".into());
                                        }
                                    }
                                }
                            }
                        }
                        file.imports.push(import(path, &name, line, bindings));
                    } else {
                        file.notes
                            .push(format!("L{line}: dynamic {name}; unresolved"));
                    }
                }
            }
        }
        "php" => {
            if matches!(
                n.kind(),
                "include_expression"
                    | "include_once_expression"
                    | "require_expression"
                    | "require_once_expression"
            ) {
                if let Some(path) = literal(children(n, true).last().copied(), raw) {
                    file.imports
                        .push(import(path, "include", line, BTreeMap::new()));
                } else {
                    file.notes
                        .push(format!("L{line}: dynamic include; unresolved"));
                }
            } else if n.kind() == "namespace_use_declaration" {
                file.imports.push(import(
                    txt(n, raw)
                        .trim_start_matches("use ")
                        .trim_end_matches(';')
                        .to_string(),
                    "namespace",
                    line,
                    BTreeMap::new(),
                ));
            }
        }
        "bash" if n.kind() == "command" => {
            let name = ftxt(n, "name", raw);
            if matches!(name.as_str(), "source" | ".") {
                if let Some(path) = literal(field(n, "argument"), raw) {
                    file.imports
                        .push(import(path, "source", line, BTreeMap::new()));
                } else {
                    file.notes
                        .push(format!("L{line}: dynamic source; unresolved"));
                }
            }
        }
        "python" => {
            if n.kind() == "import_statement" {
                for child in children(n, true) {
                    let (module, bound) = if child.kind() == "aliased_import" {
                        (ftxt(child, "name", raw), ftxt(child, "alias", raw))
                    } else {
                        let module = txt(child, raw);
                        let bound = module.split('.').next().unwrap_or("").to_string();
                        (module, bound)
                    };
                    let value = if child.kind() == "aliased_import" {
                        "*".into()
                    } else {
                        format!("@module:{module}")
                    };
                    file.imports.push(import(
                        module,
                        "import",
                        line,
                        BTreeMap::from([(bound, value)]),
                    ));
                }
            } else if n.kind() == "import_from_statement" {
                let module = ftxt(n, "module_name", raw);
                let mut item = import(module, "from", line, BTreeMap::new());
                let module_id = field(n, "module_name").map(|x| x.id());
                for c in children(n, true) {
                    if Some(c.id()) == module_id {
                        continue;
                    }
                    let name = if c.kind() == "aliased_import" {
                        ftxt(c, "name", raw)
                    } else {
                        txt(c, raw)
                    };
                    let bound = if c.kind() == "aliased_import" {
                        ftxt(c, "alias", raw)
                    } else {
                        name.clone()
                    };
                    item.names.push(name.clone());
                    item.bindings.insert(bound, name);
                }
                file.imports.push(item);
            }
        }
        _ => {}
    }
}

fn language(name: &str) -> Option<Language> {
    Some(match name {
        "javascript" => tree_sitter_javascript::LANGUAGE.into(),
        "typescript" => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
        "tsx" => tree_sitter_typescript::LANGUAGE_TSX.into(),
        "php" => tree_sitter_php::LANGUAGE_PHP.into(),
        "bash" => tree_sitter_bash::LANGUAGE.into(),
        "python" => tree_sitter_python::LANGUAGE.into(),
        _ => return None,
    })
}
pub fn parse(file: &mut File, raw: &[u8], parsers: &mut BTreeMap<String, Parser>) {
    if !parsers.contains_key(&file.language) {
        let Some(lang) = language(&file.language) else {
            return;
        };
        let mut parser = Parser::new();
        parser.set_language(&lang).expect("compatible grammar");
        parsers.insert(file.language.clone(), parser);
    }
    file.parser = format!("tree-sitter:{}", file.language);
    let Some(tree) = parsers.get_mut(&file.language).unwrap().parse(raw, None) else {
        file.status = "parse-error".into();
        return;
    };
    let root = tree.root_node();
    let valid = !root.has_error();
    file.status = if valid { "parsed" } else { "parse-error" }.into();
    if valid {
        let tokens = canonical(root, raw, false, false);
        file.syntax_hash = digest(&tokens);
        file.shape_hash = digest(&canonical(root, raw, true, false));
        file.fingerprints = shingles(&tokens);
    } else {
        file.notes
            .push("Invalid or incomplete syntax; no syntax fingerprints".into());
    }
    file.nodes = count_named_nodes(root);
    let mut stack = vec![(root, "<module>".to_string(), 0)];
    while let Some((n, mut scope, mut depth)) = stack.pop() {
        if matches!(n.kind(), "comment" | "hash_bang_line") {
            continue;
        }
        add_imports(file, n, raw);
        let line = n.start_position().row + 1;
        if let Some(kind) = definition(n.kind()) {
            let mut name = ftxt(n, "name", raw);
            if name.is_empty() {
                if let Some(parent) = n.parent() {
                    if parent.kind() == "variable_declarator" {
                        name = ftxt(parent, "name", raw);
                    }
                }
            }
            if name.is_empty() {
                name = format!("<anonymous@{line}:{}>", n.start_position().column);
            }
            let qualname = if scope == "<module>" {
                name.clone()
            } else {
                format!("{scope}.{name}")
            };
            let mut decisions = 0;
            let mut local = vec![];
            for i in (0..n.named_child_count()).rev() {
                if let Some(c) = n.named_child(i) {
                    local.push(c);
                }
            }
            while let Some(c) = local.pop() {
                if definition(c.kind()).is_some() {
                    continue;
                }
                decisions += usize::from(
                    control(c.kind())
                        && !matches!(c.kind(), "else_clause" | "try_statement" | "finally_clause"),
                );
                for i in (0..c.named_child_count()).rev() {
                    if let Some(ch) = c.named_child(i) {
                        local.push(ch);
                    }
                }
            }
            file.symbols.push(Symbol {
                name,
                qualname: qualname.clone(),
                kind: kind.into(),
                line,
                end_line: n.end_position().row + 1,
                syntax_hash: if valid {
                    digest(&canonical(n, raw, false, false))
                } else {
                    String::new()
                },
                body_hash: if valid {
                    digest(&canonical(n, raw, false, true))
                } else {
                    String::new()
                },
                shape_hash: if valid {
                    digest(&canonical(n, raw, true, false))
                } else {
                    String::new()
                },
                nodes: count_named_nodes(n),
                decisions,
            });
            scope = qualname;
            depth = 0;
        }
        if matches!(
            n.kind(),
            "call_expression"
                | "function_call_expression"
                | "member_call_expression"
                | "scoped_call_expression"
                | "new_expression"
                | "object_creation_expression"
                | "command"
                | "call"
        ) {
            let callee = field(n, "function")
                .or_else(|| field(n, "constructor"))
                .or_else(|| field(n, "name"));
            let mut name = callee
                .map(|c| txt(c, raw))
                .unwrap_or_else(|| "<dynamic>".into());
            if matches!(
                n.kind(),
                "member_call_expression" | "scoped_call_expression"
            ) {
                let owner = field(n, "object").or_else(|| field(n, "scope"));
                name = format!(
                    "{}.{}",
                    owner.map(|o| txt(o, raw)).unwrap_or_default(),
                    name
                );
            }
            file.calls.push(Call {
                caller: scope.clone(),
                name: name.clone(),
                line,
                resolution: "unresolved".into(),
                ..Default::default()
            });
            file.logic.push(Logic {
                scope: scope.clone(),
                kind: "call".into(),
                line,
                depth,
                label: format!("{name}()"),
            });
        }
        if control(n.kind()) {
            let kind = n
                .kind()
                .trim_end_matches("_statement")
                .trim_end_matches("_expression");
            let condition = field(n, "condition").or_else(|| field(n, "value"));
            let label = format!(
                "{kind}{}",
                condition
                    .map(|c| format!(" {}", txt(c, raw)))
                    .unwrap_or_default()
            );
            file.logic.push(Logic {
                scope: scope.clone(),
                kind: kind.into(),
                line,
                depth,
                label,
            });
            depth += 1;
        } else if matches!(
            n.kind(),
            "return_statement"
                | "throw_statement"
                | "break_statement"
                | "continue_statement"
                | "await_expression"
                | "yield_expression"
                | "raise_statement"
        ) {
            let kind = n.kind().split('_').next().unwrap().to_string();
            file.logic.push(Logic {
                scope: scope.clone(),
                kind: kind.clone(),
                line,
                depth,
                label: kind,
            });
        }
        for i in (0..n.named_child_count()).rev() {
            if let Some(c) = n.named_child(i) {
                stack.push((c, scope.clone(), depth));
            }
        }
    }
}
