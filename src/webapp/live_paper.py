"""Read-only payload for v10 paper live signals (simulated, no orders).

Consumes the artifact written by scripts/live_signal_tg.py:
  analysis/output/live_signals_v10/last_scan.json
  analysis/output/live_signals_v10/*.png

Returns metadata + public URLs (served via the debug-artifacts mount).
Never writes forward_log, never promotes, never places orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.webapp.dashboard_cache import relative_path

PROJECT = Path(__file__).resolve().parents[2]
LIVE_DIR = PROJECT / "analysis" / "output" / "live_signals_v10"
LAST_SCAN = LIVE_DIR / "last_scan.json"
PUBLIC_PREFIX = "/debug-artifacts/live_signals_v10"


def _to_public(p: str | Path) -> str:
    """Map an absolute or relative path under LIVE_DIR to a browser URL."""
    pp = Path(p)
    try:
        if pp.is_absolute():
            rel = pp.relative_to(LIVE_DIR)
        else:
            rel = pp
    except Exception:
        # fall back to basename if outside the tree
        rel = Path(pp.name)
    return f"{PUBLIC_PREFIX}/{rel.as_posix()}"


def live_paper_payload() -> dict[str, Any]:
    """Return latest v10 paper scan for dashboard consumption.

    Shape:
    {
      "available": bool,
      "scanned_at": str | None,
      "n_symbols": int,
      "tip_edge": int,
      "conf": float,
      "gate_min": float,
      "n_fired": int,
      "n_fresh": int,
      "wall_min": float,
      "hits": [ {symbol, conf, age_min, fresh, png_url, entry, tp, sl, atr, signal_time, ...}, ... ],
      "source": "analysis/output/live_signals_v10/last_scan.json"
    }
    """
    if not LAST_SCAN.exists():
        return {
            "available": False,
            "scanned_at": None,
            "n_symbols": 0,
            "tip_edge": 2,
            "conf": 0.30,
            "gate_min": 30.0,
            "n_fired": 0,
            "n_fresh": 0,
            "wall_min": 0.0,
            "hits": [],
            "source": relative_path(LAST_SCAN),
        }

    try:
        raw = json.loads(LAST_SCAN.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    hits_in = raw.get("hits") or []
    hits_out = []
    for h in hits_in:
        hh = dict(h)
        png = hh.get("png")
        if png:
            hh["png_url"] = _to_public(png)
        else:
            hh["png_url"] = None
        # ensure numeric types are JSON-safe
        for k in ("conf", "age_min", "entry", "tp", "sl", "atr", "rr"):
            if k in hh and hh[k] is not None:
                try:
                    hh[k] = float(hh[k])
                except Exception:
                    hh[k] = None
        hits_out.append(hh)

    return {
        "available": True,
        "scanned_at": raw.get("scanned_at"),
        "n_symbols": int(raw.get("n_symbols", 0)),
        "tip_edge": int(raw.get("tip_edge", 2)),
        "conf": float(raw.get("conf", 0.30)),
        "gate_min": float(raw.get("gate_min", 30.0)),
        "n_fired": int(raw.get("n_fired", 0)),
        "n_fresh": int(raw.get("n_fresh", 0)),
        "wall_min": float(raw.get("wall_min", 0.0)),
        "hits": hits_out,
        "source": relative_path(LAST_SCAN),
    }


def live_paper_status_line() -> str:
    """One-line summary for status strip."""
    p = live_paper_payload()
    if not p.get("available"):
        return "v10 纸面: 无最近扫描"
    fresh = p.get("n_fresh", 0)
    tot = p.get("n_fired", 0)
    ts = p.get("scanned_at") or ""
    ts_short = ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        ts_short = dt.astimezone(timezone.utc).strftime("%m-%d %H:%M")
    except Exception:
        ts_short = ts[:16] if ts else ""
    return f"v10 纸面: {fresh} 新鲜 / {tot} 总  · {ts_short} UTC"