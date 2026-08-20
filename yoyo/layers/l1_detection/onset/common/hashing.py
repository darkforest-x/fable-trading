"""SHA-256 helpers. Every artefact this phase produces records where it came from."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_sha256(obj: Any) -> str:
    """Hash of a canonical JSON encoding.

    sort_keys and a fixed separator make the digest depend on content rather than
    on dict ordering, so re-serialising an unchanged object does not look like a
    change in an audit.
    """
    return bytes_sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
