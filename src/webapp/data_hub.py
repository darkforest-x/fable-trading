"""P2.5 Phase 3: read-only data hub (coverage, audit summary, forward health).

No write paths. Scans filesystem metadata + embeds existing audit JSON when present.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.bars import BAR_CHOICES, normalize_bar
from src.data.loader import BLOCKED_BASES, CACHE_DIR, CACHE_PATTERN, FETCHED_DIR
from src.judgment.forward_types import FORWARD_LOG_PATH
from src.webapp.forward_payloads import FORWARD_COST, FORWARD_DECISION_TRADES, forward_payload

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SUMMARY_PATH = PROJECT_ROOT / "analysis" / "output" / "data_audit_summary.json"
AUDIT_REPORT_PATH = PROJECT_ROOT / "analysis" / "p2_data_audit_report.md"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _file_mtime_iso(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def coverage_by_bar(fetched_dir: Path | None = None, cache_dir: Path | None = None) -> dict[str, Any]:
    """Count series and raw CSV files per bar from list_series + directory globs.

    Prefer list_series for usable (source, symbol) counts (BLOCKED_BASES excluded).
    Also report raw file counts under kline_fetched / kline_cache for disk truth.
    """
    fetched = fetched_dir if fetched_dir is not None else FETCHED_DIR
    cache = cache_dir if cache_dir is not None else CACHE_DIR

    by_bar: list[dict[str, Any]] = []
    for bar in BAR_CHOICES:
        # Mirror loader.list_series: scan cache + fetched, group by (source, symbol).
        groups = _list_series_dirs([cache, fetched], bar=bar)
        series_n = len(groups)
        # Approximate row budget from filename `_rows` suffix (same as naming convention).
        named_rows = 0
        file_n = 0
        latest_mtime: float | None = None
        for paths in groups.values():
            for p in paths:
                file_n += 1
                m = CACHE_PATTERN.match(p.name)
                if m:
                    try:
                        named_rows += int(m.group("rows"))
                    except (TypeError, ValueError):
                        pass
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if latest_mtime is None or mt > latest_mtime:
                    latest_mtime = mt

        raw_fetched = _raw_csv_count(fetched, bar)
        raw_cache = _raw_csv_count(cache, bar)
        by_bar.append(
            {
                "bar": bar,
                "series_n": series_n,
                "file_n": file_n,
                "named_rows_sum": named_rows,
                "raw_fetched_csv": raw_fetched,
                "raw_cache_csv": raw_cache,
                "latest_mtime": (
                    datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
                    if latest_mtime is not None
                    else None
                ),
            }
        )

    return {
        "fetched_dir": _rel(fetched) if fetched.exists() or fetched_dir is not None else "data/kline_fetched",
        "cache_dir": _rel(cache) if cache.exists() or cache_dir is not None else "data/kline_cache",
        "fetched_exists": fetched.is_dir(),
        "cache_exists": cache.is_dir() or cache.is_symlink(),
        "by_bar": by_bar,
        "series_total": sum(row["series_n"] for row in by_bar),
        "file_total": sum(row["file_n"] for row in by_bar),
    }


def _list_series_dirs(dirs: list[Path], *, bar: str) -> dict[tuple[str, str], list[Path]]:
    """list_series variant over explicit dirs (for tests / isolation)."""
    bar = normalize_bar(bar)
    paths: list[Path] = []
    for d in dirs:
        if d is not None and d.is_dir():
            paths.extend(d.glob("*.csv"))
    groups: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(paths):
        matched = CACHE_PATTERN.match(path.name)
        if matched is None or matched.group("bar") != bar:
            continue
        source = matched.group("prefix") or "okx"
        symbol = matched.group("symbol")
        if symbol.split("_", 1)[0] in BLOCKED_BASES:
            continue
        groups.setdefault((source, symbol), []).append(path)
    return groups


def _raw_csv_count(directory: Path, bar: str) -> int:
    if not directory.is_dir():
        return 0
    n = 0
    for path in directory.glob("*.csv"):
        matched = CACHE_PATTERN.match(path.name)
        if matched is not None and matched.group("bar") == bar:
            n += 1
    return n


def load_audit_summary(path: Path | None = None) -> dict[str, Any]:
    """Embed data_audit_summary.json if present; never invent metrics."""
    p = path if path is not None else AUDIT_SUMMARY_PATH
    report = AUDIT_REPORT_PATH
    if not p.is_file():
        return {
            "exists": False,
            "path": _rel(p),
            "mtime": None,
            "summary": None,
            "report_path": _rel(report) if report.is_file() else None,
            "report_exists": report.is_file(),
        }
    try:
        summary = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": _rel(p),
            "mtime": _file_mtime_iso(p),
            "summary": None,
            "error": f"failed to parse audit summary: {exc}",
            "report_path": _rel(report) if report.is_file() else None,
            "report_exists": report.is_file(),
        }
    # Keep payload bounded: drop large nested lists if huge.
    compact = _compact_audit_summary(summary)
    return {
        "exists": True,
        "path": _rel(p),
        "mtime": _file_mtime_iso(p),
        "summary": compact,
        "report_path": _rel(report) if report.is_file() else None,
        "report_exists": report.is_file(),
    }


def _compact_audit_summary(summary: Any) -> Any:
    if not isinstance(summary, dict):
        return summary
    out = dict(summary)
    for key in ("part_files", "worst_gaps", "blacklist_candidates", "flagged_rows"):
        val = out.get(key)
        if isinstance(val, list) and len(val) > 20:
            out[key] = val[:20]
            out[f"{key}_truncated"] = True
            out[f"{key}_total"] = len(val)
    return out


def forward_log_health(
    log_path: Path | None = None,
    *,
    cost: float = FORWARD_COST,
) -> dict[str, Any]:
    """Forward log row counts and progress toward 100 decision trades."""
    path = log_path if log_path is not None else FORWARD_LOG_PATH
    if log_path is not None:
        # Isolated path for tests: count without full dashboard forward_payload.
        return _forward_health_from_path(path, cost=cost)
    # Production path reuses forward_payload (same counters as 前向 tab).
    if not path.is_file():
        return {
            "exists": False,
            "path": _rel(path),
            "total_rows": 0,
            "closed_rows": 0,
            "open_rows": 0,
            "decision_trades": 0,
            "decision_target": FORWARD_DECISION_TRADES,
            "decision_remaining": FORWARD_DECISION_TRADES,
            "progress": 0.0,
            "latest_detected_at": None,
            "mtime": None,
        }
    payload = forward_payload(cost=cost)
    return {
        "exists": True,
        "path": payload.get("log_path") or _rel(path),
        "total_rows": payload.get("total_rows", 0),
        "closed_rows": payload.get("closed_rows", 0),
        "open_rows": payload.get("open_rows", 0),
        "decision_trades": payload.get("decision_trades", 0),
        "decision_target": payload.get("decision_target", FORWARD_DECISION_TRADES),
        "decision_remaining": payload.get("decision_remaining", FORWARD_DECISION_TRADES),
        "progress": payload.get("progress", 0.0),
        "latest_detected_at": payload.get("latest_detected_at"),
        "mtime": _file_mtime_iso(path),
        "cost": payload.get("cost"),
        "cost_label": payload.get("cost_label"),
    }


def _forward_health_from_path(path: Path, *, cost: float) -> dict[str, Any]:
    """Lightweight CSV stats for tests (no full equity/metrics)."""
    if not path.is_file():
        return {
            "exists": False,
            "path": str(path),
            "total_rows": 0,
            "closed_rows": 0,
            "open_rows": 0,
            "decision_trades": 0,
            "decision_target": FORWARD_DECISION_TRADES,
            "decision_remaining": FORWARD_DECISION_TRADES,
            "progress": 0.0,
            "latest_detected_at": None,
            "mtime": None,
            "cost": cost,
        }
    try:
        import pandas as pd

        frame = pd.read_csv(path)
    except (OSError, ValueError) as exc:
        return {
            "exists": True,
            "path": str(path),
            "error": str(exc),
            "total_rows": 0,
            "closed_rows": 0,
            "open_rows": 0,
            "decision_trades": 0,
            "decision_target": FORWARD_DECISION_TRADES,
            "decision_remaining": FORWARD_DECISION_TRADES,
            "progress": 0.0,
            "latest_detected_at": None,
            "mtime": _file_mtime_iso(path),
            "cost": cost,
        }
    total = int(len(frame))
    status = frame["status"] if "status" in frame.columns else None
    closed = int((status == "closed").sum()) if status is not None else 0
    open_rows = total - closed if status is not None else 0
    decision = 0
    if "maker_filled" in frame.columns and status is not None:
        mf = frame["maker_filled"]
        # truthy strings/bools
        filled = mf.astype(str).str.lower().isin({"true", "1", "yes"}) | (mf == True)  # noqa: E712
        decision = int(((status == "closed") & filled & frame["realized_ret"].notna()).sum()) if "realized_ret" in frame.columns else int(((status == "closed") & filled).sum())
    latest = None
    if "detected_at" in frame.columns and not frame.empty:
        latest = str(frame["detected_at"].dropna().astype(str).iloc[-1]) if frame["detected_at"].notna().any() else None
    remaining = max(FORWARD_DECISION_TRADES - decision, 0)
    return {
        "exists": True,
        "path": str(path),
        "total_rows": total,
        "closed_rows": closed,
        "open_rows": open_rows,
        "decision_trades": decision,
        "decision_target": FORWARD_DECISION_TRADES,
        "decision_remaining": remaining,
        "progress": round(min(decision / FORWARD_DECISION_TRADES, 1.0), 4) if FORWARD_DECISION_TRADES else 0.0,
        "latest_detected_at": latest,
        "mtime": _file_mtime_iso(path),
        "cost": cost,
    }


def data_hub_payload(
    *,
    fetched_dir: Path | None = None,
    cache_dir: Path | None = None,
    audit_path: Path | None = None,
    forward_log_path: Path | None = None,
) -> dict[str, Any]:
    """Full GET /api/ops/data-hub body."""
    coverage = coverage_by_bar(fetched_dir=fetched_dir, cache_dir=cache_dir)
    audit = load_audit_summary(audit_path)
    forward = forward_log_health(forward_log_path)
    return {
        "generated_at": _iso_now(),
        "coverage": coverage,
        "audit": audit,
        "forward": forward,
        "read_only": True,
    }
