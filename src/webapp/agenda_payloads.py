"""P2.5 Phase 1: read-only RESEARCH_AGENDA payload."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENDA_PATH = PROJECT_ROOT / "docs" / "RESEARCH_AGENDA.md"


def agenda_payload() -> dict:
    if not AGENDA_PATH.is_file():
        return {
            "path": "docs/RESEARCH_AGENDA.md",
            "exists": False,
            "markdown": "",
            "mtime": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "议程文件不存在",
        }
    text = AGENDA_PATH.read_text(encoding="utf-8")
    st = AGENDA_PATH.stat()
    return {
        "path": "docs/RESEARCH_AGENDA.md",
        "exists": True,
        "markdown": text,
        "mtime": st.st_mtime,
        "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "size": st.st_size,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
