"""Optional, disposable parse cache. Source bytes are always hashed again.

Entries contain a JSON header followed by a compact JSON payload. Only successful
path-independent parse data is stored; dependency resolution is never cached.
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from functools import lru_cache
from hashlib import sha256
from importlib import metadata, resources
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import get_args, get_origin, get_type_hints

from . import __version__
from .model import Call, File, Import, Logic, Symbol

FORMAT = "grapholog.parse-cache.v1"
MAX_ENTRY = 64 * 1024 * 1024
PARSE_FIELDS = ("lines", "parser", "status", "syntax_hash", "shape_hash", "fingerprints",
                "nodes", "symbols", "imports", "calls", "logic", "notes")
RECORDS = {"symbols": Symbol, "imports": Import, "calls": Call, "logic": Logic}


@lru_cache(maxsize=1)
def identity() -> dict:
    versions = {}
    for package in ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript",
                    "tree-sitter-php", "tree-sitter-bash"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "missing"
    source = resources.files("grapholog")
    return {"format": FORMAT, "engine": "python", "version": __version__,
            "interpreter": [sys.implementation.name, *sys.version_info[:3]],
            "packages": versions,
            "code": {name: sha256(source.joinpath(name).read_bytes()).hexdigest()
                     for name in ("cache.py", "model.py", "python_parser.py", "tree_parser.py")}}


def _encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


@lru_cache(maxsize=None)
def _hints(cls):
    return get_type_hints(cls)


def _valid(value, kind) -> bool:
    origin, args = get_origin(kind), get_args(kind)
    if kind is str:
        return isinstance(value, str)
    if kind is int:
        return type(value) is int and value >= 0
    if kind is bool:
        return type(value) is bool
    if origin is list:
        return isinstance(value, list) and all(_valid(v, args[0]) for v in value)
    if origin is dict:
        return isinstance(value, dict) and all(_valid(k, args[0]) and _valid(v, args[1]) for k, v in value.items())
    if is_dataclass(kind):
        hints = _hints(kind)
        return isinstance(value, dict) and value.keys() == hints.keys() and all(_valid(value[k], v) for k, v in hints.items())
    return False


def _hex(value: str, size: int) -> bool:
    return len(value) == size and all(c in "0123456789abcdef" for c in value)


class ParseCache:
    def __init__(self, directory: str | Path | None):
        self.root = None
        self.namespace = ""
        self.stats = {"enabled": False}
        if directory is None:
            return
        path = Path(directory).expanduser().absolute()
        self.stats = {"enabled": False, "directory": str(path), "hits": 0, "misses": 0,
                      "writes": 0, "invalid": 0, "errors": 0, "skipped_large": 0, "issues": []}
        try:
            if path.is_symlink():
                raise OSError("Cache directory must not be a symlink")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root = path.resolve()
            self.namespace = sha256(_encoded(identity())).hexdigest()
            self.stats.update(enabled=True, directory=str(self.root), namespace=self.namespace)
        except (OSError, ValueError) as exc:
            self.root = None
            self._error(exc)

    def _error(self, error):
        self.stats["errors"] += 1
        if len(self.stats["issues"]) < 3:
            self.stats["issues"].append(str(error))

    def _entry(self, file: File) -> Path:
        key = sha256((file.language + "\0" + file.sha256).encode()).hexdigest()
        return self.root / "python-v1" / self.namespace / key[:2] / (key + ".jsonl")

    def _safe_parents(self, path: Path, create: bool = False):
        # Cache names are generated hashes, never source paths. Reject symlinked shards.
        for folder in reversed(list(path.parents)):
            if folder == self.root or self.root in folder.parents:
                if create:
                    folder.mkdir(exist_ok=True, mode=0o700)
                info = folder.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError("Cache parent is not a regular directory")

    def load(self, file: File) -> bool:
        if self.root is None:
            return False
        try:
            path = self._entry(file)
            self._safe_parents(path)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Cache entry is not a regular file")
                data = stream.read(MAX_ENTRY + 1)
            if len(data) > MAX_ENTRY:
                raise ValueError("Cache entry exceeds size limit")
            head, payload = data.split(b"\n", 1)
            if len(head) > 4096:
                raise ValueError("Invalid cache header")
            header = json.loads(head)
            expected = {"schema": FORMAT, "namespace": self.namespace, "language": file.language,
                        "source_sha256": file.sha256, "payload_sha256": sha256(payload).hexdigest()}
            if header != expected:
                raise ValueError("Cache header or checksum mismatch")
            result = json.loads(payload)
            hints = _hints(File)
            if (not isinstance(result, dict) or set(result) != set(PARSE_FIELDS)
                    or not all(_valid(result[k], hints[k]) for k in PARSE_FIELDS)):
                raise ValueError("Invalid parse data")
            expected_parser = "python.ast" if file.language == "python" else "tree-sitter:" + file.language
            if (result["status"] != "parsed" or result["parser"] != expected_parser
                    or not all(_hex(result[k], 64) for k in ("syntax_hash", "shape_hash"))
                    or any(not _hex(s, 16) for s in result["fingerprints"])
                    or any(a >= b for a, b in zip(result["fingerprints"], result["fingerprints"][1:]))):
                raise ValueError("Incompatible parse data")
            if (any(i["targets"] or i["status"] != "unresolved" for i in result["imports"])
                    or any(c["target"] or c["resolution"] != "unresolved" for c in result["calls"])):
                raise ValueError("Cache contains resolved dependencies")
            # Apply only after validating the entire entry; no partially restored state.
            for name, cls in RECORDS.items():
                result[name] = [cls(**item) for item in result[name]]
            for name in PARSE_FIELDS:
                setattr(file, name, result[name])
            self.stats["hits"] += 1
            return True
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            self.stats["invalid"] += 1
        except OSError as exc:
            self._error(exc)
        self.stats["misses"] += 1
        return False

    def store(self, file: File):
        if self.root is None or file.status != "parsed":
            return
        temporary = None
        try:
            data = {name: getattr(file, name) for name in PARSE_FIELDS}
            for name in RECORDS:
                data[name] = [asdict(item) for item in data[name]]
            payload = _encoded(data)
            header = _encoded({"schema": FORMAT, "namespace": self.namespace, "language": file.language,
                               "source_sha256": file.sha256, "payload_sha256": sha256(payload).hexdigest()})
            if len(payload) + len(header) + 1 > MAX_ENTRY:
                self.stats["skipped_large"] += 1
                return
            path = self._entry(file)
            self._safe_parents(path, create=True)
            fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(header + b"\n")
                stream.write(payload)
            os.replace(temporary, path)
            temporary = None
            self.stats["writes"] += 1
        except (OSError, ValueError, RecursionError) as exc:
            self._error(exc)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
