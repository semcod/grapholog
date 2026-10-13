"""Versioned file policy shared byte-for-byte with the native Rust scanner."""
import fnmatch
from functools import lru_cache
from importlib.resources import files
import json
from pathlib import Path


@lru_cache(maxsize=1)
def policy():
    return json.loads(files('grapholog').joinpath('data/logic-profile.json').read_text())


def skip_reason(path: Path, *, directory: bool, executable: bool = False) -> str | None:
    p = policy()
    if directory:
        return 'generated-or-tool-directory' if path.name in p['prune_directories'] else None
    if any(fnmatch.fnmatchcase(path.name, g) for g in p['generated_globs']):
        return 'generated-or-minified'
    ext = path.suffix.lower().lstrip('.')
    if ext in p['source_extensions'] or path.name in p['context_names']:
        return None
    if not ext and executable:
        return None
    return 'unsupported-language' if ext in p['unsupported_extensions'] else 'non-code'
