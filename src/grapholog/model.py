from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json


def digest(tokens: list[str]) -> str:
    return sha256(json.dumps(tokens, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def shingles(tokens: list[str], width: int = 5) -> list[str]:
    """Same v1 hashes: encode each token and hash each distinct window once."""
    if not tokens:
        return []
    if width < 1:
        raise ValueError("shingle width must be positive")
    width = min(width, len(tokens))
    encoded = {token: json.dumps(token, ensure_ascii=False).encode() for token in set(tokens)}
    windows = {tuple(tokens[i:i + width]) for i in range(len(tokens) - width + 1)}
    return sorted({sha256(b"[" + b",".join(encoded[t] for t in window) + b"]").hexdigest()[:16]
                   for window in windows})


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str
    line: int
    end_line: int
    syntax_hash: str = ""
    body_hash: str = ""
    shape_hash: str = ""
    nodes: int = 0
    decisions: int = 0


@dataclass
class Import:
    module: str
    kind: str
    line: int
    names: list[str] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    status: str = "unresolved"


@dataclass
class Call:
    caller: str
    name: str
    line: int
    target: str = ""
    resolution: str = "unresolved"


@dataclass
class Logic:
    scope: str
    kind: str
    line: int
    depth: int
    label: str


@dataclass
class File:
    path: str
    kind: str = "file"
    language: str = "other"
    size: int = 0
    lines: int = 0
    sha256: str = ""
    parser: str = "none"
    status: str = "inventory"
    syntax_hash: str = ""
    shape_hash: str = ""
    fingerprints: list[str] = field(default_factory=list)
    nodes: int = 0
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    logic: list[Logic] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    link_target: str = ""
    executable: bool = False


@dataclass
class Project:
    root: str
    files: list[File] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    schema: str = "grapholog.project.v1"
    runtime: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def symbol_ref(file: File, symbol: Symbol) -> str:
    return f"{file.path}::{symbol.qualname}@{symbol.line}"
