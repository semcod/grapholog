import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from grapholog.scan import scan
from grapholog.compare import compare, duplicates
from grapholog.render import render_project

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
from corpus import small, put


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_expected_changes_and_imports(self):
        a, b = small(self.root)
        one, two = scan(a), scan(b)
        result = compare(one, two)
        expected = json.loads((self.root / "expected-python.json").read_text())
        actual = {p["right"] or p["left"]: p["status"] for p in result["pairs"]}
        self.assertEqual(actual, expected)
        files = {f.path: f for f in one.files}
        self.assertEqual(files["php/main.php"].imports[0].targets, ["php/util.php"])
        self.assertEqual(files["shell/main.sh"].imports[0].targets, ["shell/lib.sh"])
        self.assertTrue(files["src/pkg/main.py"].calls[0].target.startswith("src/pkg/math.py::sum_values"))
        self.assertEqual(one.cycles, [["cycle/a.js", "cycle/b.js"]])
        self.assertIn(["copy_a.js", "copy_b.js"], duplicates([("", one)])["exact_files"])
    def test_syntax_names_and_literals(self):
        for name, source in {"a.py":"def one(a, b):\n    return a-b\n", "b.py":"def two(x, y):\n    return x-y\n",
                             "c.py":"def two(x, y):\n    return y-x\n", "d.py":"def two(x, y):\n    return x+y\n"}.items():
            put(self.root, name, source)
        f = {f.path: f for f in scan(self.root).files}
        self.assertEqual(f["a.py"].shape_hash, f["b.py"].shape_hash)
        self.assertNotEqual(f["b.py"].shape_hash, f["c.py"].shape_hash)
        self.assertNotEqual(f["b.py"].shape_hash, f["d.py"].shape_hash)
    def test_ignored_binary_large_empty_and_links(self):
        put(self.root, "node_modules/x.js", "hello")
        put(self.root, "asset.bin", b"\x00abc")
        put(self.root, "big.py", "a=1\n"*50)
        (self.root/"empty").mkdir()
        (self.root/"loop").symlink_to(self.root, target_is_directory=True)
        p = scan(self.root, max_bytes=100)
        f = {f.path: f for f in p.files}
        self.assertEqual(f["loop"].status, "symlink")
        self.assertEqual(f["big.py"].status, "size-limit")
        self.assertTrue(f["big.py"].sha256)
        self.assertEqual(f["asset.bin"].status, "binary")
        self.assertIn("empty", p.directories)
        self.assertEqual(p.excluded, ["node_modules"])
        self.assertIn("node_modules/x.js", [f.path for f in scan(self.root, all_files=True).files])
    def test_determinism_and_terminal_controls(self):
        put(self.root, "bad\x1b[2J.py", "def safe(x):\n    return x\n")
        first, second = scan(self.root), scan(self.root)
        self.assertEqual(first.to_dict(), second.to_dict())
        rendered = render_project(first)
        self.assertNotIn("\x1b", rendered)
        self.assertIn("\\x1b", rendered)
    def test_parse_errors_no_false_syntax_matches(self):
        put(self.root/"a", "bad.py", "def (")
        put(self.root/"b", "bad.py", "if :")
        one, two = scan(self.root/"a"), scan(self.root/"b")
        self.assertEqual(one.files[0].status, "parse-error")
        self.assertFalse(one.files[0].syntax_hash)
        self.assertEqual(compare(one,two)["pairs"][0]["status"], "changed")
    def test_project_is_not_executed(self):
        marker = self.root/"should-not-exist"
        put(self.root, "danger.py", f"open({str(marker)!r}, 'w').write('executed')\n")
        scan(self.root)
        self.assertFalse(marker.exists())
    def test_directory_diff_and_executable_bit(self):
        for side in ("a", "b"):
            put(self.root/side, "go.sh", "echo ok\n")
        os.chmod(self.root/"b/go.sh", 0o755)
        (self.root/"b/empty").mkdir()
        result = compare(scan(self.root/"a"), scan(self.root/"b"))
        self.assertEqual(result["pairs"][0]["status"], "mode-changed")
        self.assertEqual(result["directories_added"], ["empty"])
    def test_ambiguous_moves_not_arbitrarily_paired(self):
        for name in ("one.py", "two.py"):
            put(self.root/"a", name, "x=1\n")
        put(self.root/"b", "three.py", "x=1\n")
        result = compare(scan(self.root/"a"), scan(self.root/"b"))
        self.assertEqual(result["counts"], {"added":1,"removed":2})
    def test_cli_check_strict_output(self):
        put(self.root/"a", "x.py", "a=1\n")
        put(self.root/"b", "x.py", "a=2\n")
        base = [sys.executable, "-m", "grapholog"]
        run = subprocess.run(base+["diff",str(self.root/"a"),str(self.root/"b"),"--check","--format","json"],capture_output=True)
        self.assertEqual(run.returncode,1)
        self.assertTrue(json.loads(run.stdout)["has_differences"])
        put(self.root/"a", "bad.py", "def (")
        run = subprocess.run(base+["scan",str(self.root/"a"),"--strict"],capture_output=True)
        self.assertEqual(run.returncode,2)
        existing = self.root/"a/x.py"
        run = subprocess.run(base+["scan",str(self.root/"a"),"-o",str(existing)],capture_output=True)
        self.assertEqual(run.returncode,2)
        self.assertEqual(existing.read_text(),"a=1\n")


if __name__ == "__main__":
    unittest.main()
