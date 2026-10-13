import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def core(report):
    report = copy.deepcopy(report)
    report.pop("runtime", None)
    return report


class CacheCases:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.cache = self.base / "cache"

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, source):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def run_scan(self, *extra, cached=True, expected=0):
        args = self.command + ["scan", str(self.root), "--format", "json", *map(str, extra)]
        if cached:
            args += ["--cache-dir", str(self.cache)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if expected == 0 else result

    def test_cache_hits_and_same_mtime_content_change(self):
        file = self.put("f.py", "def f(x):\n    return x + 1\n")
        fresh = self.run_scan(cached=False)
        cold = self.run_scan()
        warm = self.run_scan()
        self.assertEqual(core(fresh), core(cold))
        self.assertEqual(core(fresh), core(warm))
        self.assertEqual(cold["runtime"]["cache"]["writes"], 1)
        self.assertEqual(warm["runtime"]["cache"]["hits"], 1)
        info = file.stat()
        file.write_text("def f(x):\n    return x + 2\n")
        os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
        changed = self.run_scan()
        self.assertEqual(changed["runtime"]["cache"]["hits"], 0)
        self.assertNotEqual(changed["files"][0]["syntax_hash"], fresh["files"][0]["syntax_hash"])
        self.assertEqual(core(changed), core(self.run_scan(cached=False)))
        file.chmod(0o755)
        metadata = self.run_scan()
        self.assertEqual(metadata["runtime"]["cache"]["hits"], 1)
        self.assertTrue(metadata["files"][0]["executable"])

    def test_move_recomputes_relative_imports_and_calls(self):
        source = self.put("a/main.js", "import {work} from './util.js'; work(1);\n")
        self.put("a/util.js", "export function work(x) { return x + 1; }\n")
        self.put("b/util.js", "export function work(x) { return x + 2; }\n")
        self.run_scan()
        source.rename(self.root / "b/main.js")
        moved = self.run_scan()
        file = next(f for f in moved["files"] if f["path"] == "b/main.js")
        self.assertEqual(moved["runtime"]["cache"]["hits"], 3)
        self.assertEqual(file["imports"][0]["targets"], ["b/util.js"])
        self.assertEqual(file["calls"][0]["target"], "b/util.js::work@1")
        self.assertEqual(core(moved), core(self.run_scan(cached=False)))

    def test_source_roots_and_new_dependency_recomputed(self):
        self.put("app/main.py", "from util import work\nwork(1)\n")
        self.run_scan()
        self.put("lib/util.py", "def work(x):\n    return x + 1\n")
        default = self.run_scan()
        custom = self.run_scan("--source-root", "lib")
        self.assertEqual(default["files"][0]["imports"][0]["targets"], [])
        self.assertEqual(custom["files"][0]["imports"][0]["targets"], ["lib/util.py"])
        self.assertEqual(custom["runtime"]["cache"]["hits"], 2)
        self.assertEqual(core(custom), core(self.run_scan("--source-root", "lib", cached=False)))

    def test_corrupt_cache_falls_back_and_repairs(self):
        self.put("f.py", "def f(x):\n    return x + 1\n")
        expected = self.run_scan()
        entry = next(self.cache.rglob("*.jsonl"))
        entry.write_bytes(b"invalid\n{broken")
        repaired = self.run_scan()
        self.assertEqual(core(repaired), core(expected))
        self.assertEqual(repaired["runtime"]["cache"]["invalid"], 1)
        self.assertEqual(repaired["runtime"]["cache"]["writes"], 1)
        self.assertEqual(self.run_scan()["runtime"]["cache"]["hits"], 1)

    def test_wrong_namespace_and_malformed_typed_payload_rejected(self):
        self.put("f.py", "def f(x):\n    return x + 1\n")
        expected = self.run_scan()
        entry = next(self.cache.rglob("*.jsonl"))
        original = entry.read_bytes()
        for mode in ("namespace", "type", "resolved", "source", "checksum"):
            head, raw = original.split(b"\n", 1)
            header, payload = json.loads(head), json.loads(raw)
            if mode == "namespace": header["namespace"] = "0" * 64
            if mode == "source": header["source_sha256"] = "0" * 64
            if mode == "type": payload["symbols"][0]["line"] = "not an integer"
            if mode == "resolved":
                payload["calls"] = [{"caller":"f","name":"x","line":1,"target":"stale.py::x@1","resolution":"local"}]
            raw = json.dumps(payload, separators=(",", ":")).encode()
            header["payload_sha256"] = hashlib.sha256(raw).hexdigest() if mode != "checksum" else "0" * 64
            entry.write_bytes(json.dumps(header).encode() + b"\n" + raw)
            result = self.run_scan()
            self.assertEqual(result["runtime"]["cache"]["invalid"], 1, mode)
            self.assertEqual(core(result), core(expected), mode)

    def test_limits_errors_and_language_are_not_bypassed(self):
        file = self.put("f.py", "def f(x):\n    return x + 1\n")
        self.run_scan()
        limited = self.run_scan("--max-bytes", "8")
        self.assertEqual(limited["files"][0]["status"], "size-limit")
        self.assertEqual(limited["runtime"]["cache"]["hits"], 0)
        file.rename(self.root / "f.js")
        different_language = self.run_scan()
        self.assertEqual(different_language["runtime"]["cache"]["hits"], 0)
        self.assertEqual(different_language["files"][0]["status"], "parse-error")
        again = self.run_scan()
        self.assertEqual(again["runtime"]["cache"]["writes"], 0)

    def test_cache_under_project_excluded_even_with_all(self):
        self.put("f.py", "x = 1\n")
        self.cache = self.root / "parsecache"
        one = self.run_scan("--all")
        two = self.run_scan("--all")
        self.assertEqual([f["path"] for f in two["files"]], ["f.py"])
        self.assertEqual(two["excluded"], ["parsecache"])
        self.assertEqual(core(one), core(two))
        self.cache = self.root
        self.assertIn("ancestor", self.run_scan(expected=2).stderr)

    def test_unavailable_cache_falls_back_without_overwriting_file(self):
        self.put("f.py", "x = 1\n")
        self.cache.write_text("sentinel")
        data = self.run_scan("--strict")
        self.assertFalse(data["runtime"]["cache"]["enabled"])
        self.assertGreater(data["runtime"]["cache"]["errors"], 0)
        self.assertEqual(self.cache.read_text(), "sentinel")
        self.assertEqual(core(data), core(self.run_scan(cached=False)))

    def test_decoded_line_count_preserved(self):
        self.put("f.py", 'value = "a\u2028b"\n')
        self.run_scan()
        warm = self.run_scan()
        self.assertEqual(warm["runtime"]["cache"]["hits"], 1)
        self.assertEqual(core(warm), core(self.run_scan(cached=False)))

    def test_symlinked_entry_cannot_restore_or_overwrite_target(self):
        self.put("f.py", "x = 1\n")
        expected = self.run_scan()
        entry = next(self.cache.rglob("*.jsonl"))
        target = self.base / "outside"
        target.write_text("sentinel")
        entry.unlink(); entry.symlink_to(target)
        result = self.run_scan()
        self.assertEqual(core(result), core(expected))
        self.assertEqual(target.read_text(), "sentinel")
        self.assertFalse(entry.is_symlink())


class PythonCacheTests(CacheCases, unittest.TestCase):
    command = [sys.executable, "-m", "grapholog"]


@unittest.skipUnless(os.environ.get("GRAPHOLOG_RUST_BIN"), "set GRAPHOLOG_RUST_BIN")
class RustCacheTests(CacheCases, unittest.TestCase):
    @property
    def command(self):
        return [os.environ["GRAPHOLOG_RUST_BIN"]]

    def test_parallel_and_concurrent_processes_preserve_results(self):
        for i in range(16):
            self.put(f"m{i}/main.js", "import {work} from './util.js'; work(1);\n")
            self.put(f"m{i}/util.js", "export function work(x) { return x + 1; }\n")
        expected = self.run_scan(cached=False)
        parallel = self.run_scan("--jobs", "4", cached=False)
        self.assertEqual(core(expected), core(parallel))
        args = self.command + ["scan", str(self.root), "--format", "json", "--cache-dir", str(self.cache), "--jobs", "4"]
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, err)
            data = json.loads(out)
            self.assertEqual(core(data), core(expected))
            self.assertEqual(data["runtime"]["cache"]["invalid"], 0)
            self.assertEqual(data["runtime"]["cache"]["errors"], 0)
        warm = self.run_scan("--jobs", "4")
        self.assertEqual(warm["runtime"]["cache"]["hits"], 32)
        self.assertEqual(core(warm), core(expected))
        self.assertFalse(list(self.cache.rglob(".tmp-*")))
        self.run_scan("--jobs", "0", expected=2)
        self.run_scan("--jobs", "65", expected=2)


if __name__ == "__main__": unittest.main()
