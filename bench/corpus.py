"""Deterministic, original fixtures. No download or project execution."""
from pathlib import Path
import argparse
import json


def put(root, path, text):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode() if isinstance(text, str) else text)


def small(root):
    a, b = Path(root) / "A", Path(root) / "B"
    common = {
        "web/math.js": "export function sum(a, b) { return a + b; }\n",
        "web/main.js": "import {sum as add} from './math.js';\nexport function run(x) { if (x > 0) return add(x, 1); return 0; }\n",
        "web/types.ts": "export interface User { name: string; age: number; }\n",
        "web/card.tsx": "import {sum} from './math.js';\nexport const Card = (x: number) => <div>{sum(x, 1)}</div>;\n",
        "web/uni.js": "export function suma(liczba) { return liczba + ą; }\n",
        "php/util.php": "<?php function double_value($x) { if ($x < 0) return 0; return $x * 2; }\n",
        "php/main.php": "<?php require_once __DIR__ . '/util.php'; echo double_value(4);\n",
        "shell/lib.sh": "double_value() { echo \"$(( $1 * 2 ))\"; }\n",
        "shell/main.sh": "#!/bin/bash\nsource ./lib.sh\nif test -n \"$1\"; then double_value 4; fi\n",
        "src/pkg/__init__.py": "",
        "src/pkg/math.py": "def sum_values(a, b):\n    return a + b\n",
        "src/pkg/main.py": "from .math import sum_values as add\n\ndef run(x):\n    if x > 0:\n        return add(x, 1)\n    return 0\n",
        "cycle/a.js": "import './b.js';\nexport function first(x) { return x + 1; }\n",
        "cycle/b.js": "import './a.js';\nexport function second(x) { return x * 2; }\n",
        "assets/icon.bin": b"\x00\xff\x12\x00",
        "config.json": '{"enabled":true}\n',
        "notes.txt": "A project fixture\n",
        "same.py": "def f(x):\n    return x + 1\n",
        "bad.js": "export function broken( {\n",
    }
    for path, content in common.items():
        put(a, path, content)
        put(b, path, content)
    modifications = {
        "format.ts": ("export function calc(x: number) { return x * 2; }\n", "// comment\nexport function calc( x: number ) {\n  return x * 2;\n}\n"),
        "rename.js": ("export function total(a, b) { return a + b; }\n", "export function amount(x, y) { return x + y; }\n"),
        "literal.py": ("def limit(x):\n    return x < 10\n", "def limit(x):\n    return x < 20\n"),
        "operator.js": ("export function calc(x, y) { return x + y; }\n", "export function calc(x, y) { return x * y; }\n"),
        "quote.py": ("value = 'hello'\n", 'value = "hello"\n'),
        "paren.py": ("value = 1 + 2\n", "value = (1 + 2)\n"),
    }
    for path, (one, two) in modifications.items():
        put(a, path, one); put(b, path, two)
    put(a, "old.php", "<?php function moved($x) { return $x + 42; }\n")
    put(b, "new/moved.php", "<?php function moved($x) { return $x + 42; }\n")
    put(a, "deleted.txt", "only left\n"); put(b, "added.txt", "only right\n")
    for root in (a, b):
        put(root, "copy_a.js", "export function copied(x) { if (x < 0) return 0; return x * 3; }\n")
        put(root, "copy_b.js", "export function copied(x) { if (x < 0) return 0; return x * 3; }\n")
        put(root, "node_modules/ignored.js", "skip this\n")
        (root / "empty").mkdir(parents=True, exist_ok=True)
    expected = {path: "unchanged" for path in common}
    expected.update({"format.ts": "syntax-equal", "rename.js": "identifier-change-candidate", "literal.py": "changed",
        "operator.js": "changed", "quote.py": "syntax-equal", "paren.py": "syntax-equal",
        "new/moved.php": "relocated-identical", "deleted.txt": "removed", "added.txt": "added",
        "copy_a.js": "unchanged", "copy_b.js": "unchanged"})
    put(root=Path(root).parent, path="expected-python.json", text=json.dumps(expected, indent=2))
    return a, b


def scale(root, count=200, functions_per_file=1):
    """JS/TS only: identical grammar versions in Python and Rust wrappers."""
    a, b = Path(root) / "A", Path(root) / "B"
    for i in range(count):
        path = f"module_{i:05}/main.ts"
        bodies = []
        for j in range(functions_per_file):
            name = f"compute_{i}" if functions_per_file == 1 else f"compute_{i}_{j}"
            bodies.append(f"export function {name}(values: number[]) {{\n"
                f"  let total = {i + j};\n  for (const x of values) {{\n    if (x > 0) total += helper(x);\n"
                "    else total -= 1;\n  }\n  return total;\n}\n")
        code = "import { helper } from './util.js';\n" + "".join(bodies)
        utility = f"export function helper(x) {{ return x * 2 + {i}; }}\n"
        for project in (a, b):
            put(project, f"module_{i:05}/util.js", utility)
            put(project, f"module_{i:05}/info.json", json.dumps({"module": i}))
        put(a, path, code)
        if i % 5 == 0:
            altered = code.replace("total -= 1", "total -= 2")
        elif i % 5 == 1:
            altered = "// new documentation\n" + code
        elif i % 5 == 2:
            altered = code.replace("total", "result")
        else:
            altered = code
        put(b, path, altered)
    return a, b


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--small", action="store_true")
    args = parser.parse_args()
    print(*(small(args.directory) if args.small else scale(args.directory, args.count)))
