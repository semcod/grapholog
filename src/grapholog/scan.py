from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
import stat
from .model import File, Project
from .python_parser import parse_python
from .tree_parser import parse_tree
from .resolve import resolve
from .cache import ParseCache
from .selection import skip_reason

DEFAULT_EXCLUDES = {".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build", "target", "coverage", ".next"}
LANGUAGES = {".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".mts": "typescript",
    ".cts": "typescript", ".tsx": "tsx", ".php": "php", ".phtml": "php", ".sh": "bash",
    ".bash": "bash"}


def language_of(path: Path, raw: bytes) -> str:
    language = LANGUAGES.get(path.suffix.lower(), "other")
    if language == "other" and raw.startswith(b"#!"):
        first = raw.split(b"\n", 1)[0].decode("ascii", "ignore")
        if "python" in first:
            return "python"
        if any(word in first for word in ("/bash", " bash", "/sh", " sh")):
            return "bash"
    return language


def scan(root: str | Path, *, all_files: bool = False, exclude: list[str] | None = None,
         max_bytes: int = 2 * 1024 * 1024, source_roots: list[str] | None = None,
         cache_dir: str | Path | None = None, profile: str = "inventory") -> Project:
    if profile not in {"inventory", "logic"}:
        raise ValueError("profile must be inventory or logic")
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if cache_dir is not None and root.is_relative_to(Path(cache_dir).expanduser().resolve()):
        raise ValueError("--cache-dir must not be the project root or its ancestor; use a dedicated cache directory")
    cache = ParseCache(cache_dir)
    patterns = list(exclude or [])
    project = Project(str(root), options={"all_files": all_files, "exclude": patterns.copy(),
        "max_parse_bytes": max_bytes, "source_roots": source_roots or [".", "src"]})
    ignore_file = root / ".graphologignore"
    if not all_files and ignore_file.is_file() and not ignore_file.is_symlink():
        try:
            patterns.extend(line.strip() for line in ignore_file.read_text(encoding="utf-8").splitlines()
                            if line.strip() and not line.lstrip().startswith("#"))
        except (OSError, UnicodeError) as exc:
            project.warnings.append(f"Cannot read .graphologignore: {exc}")
    project.options["effective_patterns"] = patterns.copy()
    project.options["default_excludes"] = sorted(DEFAULT_EXCLUDES) if not all_files else []
    filtered = []
    if profile == "logic":
        project.options["profile"] = "logic-v1"

    def omit(rel, reason, size=0):
        project.excluded.append(rel)
        if profile == "logic":
            filtered.append({"path": rel, "reason": reason, "bytes": size})

    def ignored(rel: str, name: str) -> bool:
        return (not all_files and name in DEFAULT_EXCLUDES) or any(
            fnmatch.fnmatchcase(rel, pattern.rstrip("/")) or
            fnmatch.fnmatchcase(name, pattern.rstrip("/")) or
            rel.startswith(pattern.rstrip("/") + "/") for pattern in patterns)

    directories = [root]
    while directories:
        folder = directories.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            project.warnings.append(f"Cannot list {folder.relative_to(root)}: {exc}")
            continue
        for path in entries:
            rel = path.relative_to(root).as_posix()
            if cache.root is not None and path == cache.root:
                omit(rel, "parse-cache")
                continue
            if ignored(rel, path.name):
                omit(rel, "default-or-user-exclude")
                continue
            file = File(rel)
            try:
                info = path.lstat()
                file.size = info.st_size
                file.executable = bool(info.st_mode & 0o111)
                if profile == "logic" and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    reason = skip_reason(path, directory=stat.S_ISDIR(info.st_mode), executable=file.executable)
                    if reason:
                        omit(rel, reason, info.st_size if stat.S_ISREG(info.st_mode) else 0)
                        continue
                if stat.S_ISLNK(info.st_mode):
                    file.kind, file.status = "symlink", "symlink"
                    file.link_target = os.readlink(path)
                    file.sha256 = hashlib.sha256(os.fsencode(file.link_target)).hexdigest()
                    file.notes.append("Link target inventoried; not followed")
                elif stat.S_ISDIR(info.st_mode):
                    project.directories.append(rel)
                    directories.append(path)
                    continue
                elif not stat.S_ISREG(info.st_mode):
                    file.kind, file.status = "special", "special"
                    file.notes.append("Special file; not opened")
                else:
                    _read_file(path, file, max_bytes, cache)
            except OSError as exc:
                file.status = "read-error"
                file.notes.append(str(exc))
            project.files.append(file)
    project.files.sort(key=lambda f: f.path)
    project.directories.sort()
    project.excluded.sort()
    resolve(project)
    project.runtime = {"workers": 1, "cache": cache.stats}
    if profile == "logic":
        project.runtime["filter"] = {"policy": "logic-v1", "excluded_entries": sorted(filtered, key=lambda x: x["path"]),
            "note": "A pruned directory counts as one entry; descendant counts and bytes are unknown."}
    return project


def _read_file(path: Path, file: File, max_bytes: int, cache: ParseCache) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            file.status = "read-error"
            file.notes.append("Entry changed type during scan")
            return
        hasher = hashlib.sha256()
        chunks = []
        size = 0
        first = b""
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if not first:
                first = chunk[:4096]
            hasher.update(chunk)
            size += len(chunk)
            if max_bytes == 0 or size <= max_bytes:
                chunks.append(chunk)
            else:
                chunks.clear()
        after = os.fstat(stream.fileno())
    file.size = size
    file.sha256 = hasher.hexdigest()
    file.language = language_of(path, first)
    if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino):
        file.status = "changed-during-scan"
        file.sha256 = ""
        file.notes.append("File changed while reading; rerun for a consistent fingerprint")
        return
    if max_bytes and size > max_bytes:
        file.status = "size-limit"
        file.notes.append(f"SHA-256 complete; parsing limited to {max_bytes} bytes")
        return
    raw = b"".join(chunks)
    if b"\x00" in raw:
        file.status = "binary"
        return
    file.lines = len(raw.splitlines())
    if file.language != "python":
        try:
            raw.decode("utf-8")
        except UnicodeError:
            file.status = "non-utf8"
            return
    if file.language == "other":
        file.status = "text"
        return
    if cache.load(file):
        return
    try:
        if file.language == "python":
            parse_python(file, raw)
        else:
            parse_tree(file, raw)
    except (ValueError, RecursionError, RuntimeError) as exc:
        file.status = "parse-error"
        file.syntax_hash = file.shape_hash = ""
        file.fingerprints = []
        for symbol in file.symbols:
            symbol.syntax_hash = symbol.body_hash = symbol.shape_hash = ""
        file.notes.append(f"Parser failed: {type(exc).__name__}: {exc}")
    cache.store(file)
