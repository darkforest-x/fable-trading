"""Read-only API adapter for the 突破 / 突破+spike book; sqlite only, no pandas."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from yoyo.monitor import FRESH_MS
from yoyo.monitor.spike_lines_worker import DATABASE, LEGACY_PERFORMANCE_BASIS
from yoyo.monitor.joint_notifications import JOINT_PROTOCOL

KINDS = ("joint", "break")
TIMEFRAMES = {"joint": ("15m", "30m", "1H", "4H"), "break": ("15m", "1H", "4H", "1Dutc")}
PERIOD_MS = {"15m": 900_000, "30m": 1_800_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
RSI_V1_PERFORMANCE_BASIS = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost"
RSI_V2_PERFORMANCE_BASIS = "v11_2_box_joint_rsi7_since_entry_same_tf_streak_next_open_v2_net_cost"


class LinesUnavailable(RuntimeError):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise LinesUnavailable("lines_not_started")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def _receipts(db: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Attach channel receipts without mutating historical event payloads."""
    for channel in ("bark", "telegram"):
        table = "joint_" + channel + "_outbox"
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        saved = ({r[0]: {"status": r[1], "error": r[2], "updated_ms": r[3]}
                  for r in db.execute(f"SELECT event_id,status,error,updated_ms FROM {table}")}
                 if exists else {})
        for event in rows:
            if event.get("kind") == "joint":
                event.setdefault("notifications", {})[channel] = saved.get(event["id"], {"status": "history"})
    return rows


def status(path: Path, now_ms: int | None = None) -> dict:
    try:
        db = _connect(path)
        try:
            counts = db.execute("SELECT kind,timeframe,COUNT(*) FROM events WHERE json_extract(payload,'$.protocol')=? GROUP BY kind,timeframe", (JOINT_PROTOCOL,)).fetchall()
            since = (now_ms or 0) - 86_400_000
            recent = dict(db.execute("SELECT kind,COUNT(*) FROM events WHERE bar_close_ms>=? AND json_extract(payload,'$.protocol')=? GROUP BY kind",
                                     (since, JOINT_PROTOCOL)).fetchall())
            meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,payload FROM meta")}
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LinesUnavailable("lines_book_invalid") from error
    by_kind: dict = {k: {} for k in KINDS}
    for kind, timeframe, n in counts:
        by_kind.setdefault(kind, {})[timeframe] = int(n)
    return {"configured": True, "counts": by_kind, "recent_24h": {k: int(recent.get(k, 0)) for k in KINDS},
            "protocol": JOINT_PROTOCOL, "activation": meta.get("activation:v128"), "scan": meta.get("scan"),
            "performance_policy": meta.get("performance_policy")}


def classify(event: dict, activated_ms: int | None, now_ms: int) -> dict:
    """Add display state: 实时 (seen within one bar of its close after activation), 补录, 启动前."""
    period = PERIOD_MS.get(event.get("timeframe"), 0)
    late = event["detected_at_ms"] - event["bar_close_ms"]
    if activated_ms is None or event["bar_close_ms"] <= activated_ms:
        event["display_state"] = "history"
    elif late <= period + 300_000:
        event["display_state"] = "live"
    else:
        event["display_state"] = "late"
    event["detect_delay_ms"] = late
    fresh_ms = FRESH_MS if event.get("kind") == "joint" else max(period, FRESH_MS)
    event["is_fresh"] = event["display_state"] == "live" and 0 <= now_ms - event["bar_close_ms"] <= fresh_ms
    return event


def events(path: Path, *, kind: str, now_ms: int, timeframe: str | None = None, search: str = "",
           limit: int = 300) -> list[dict]:
    if kind not in KINDS or (timeframe is not None and timeframe not in TIMEFRAMES[kind]):
        raise ValueError("unsupported kind or timeframe")
    where, values = ["kind=?", "json_extract(payload,'$.protocol')=?"], [kind, JOINT_PROTOCOL]
    if timeframe is not None:
        where.append("timeframe=?")
        values.append(timeframe)
    if search:
        where.append("symbol LIKE ?")
        values.append("%" + search.upper().replace("%", "").replace("_", "") + "%")
    try:
        db = _connect(path)
        try:
            rows = db.execute("SELECT payload FROM events WHERE " + " AND ".join(where)
                              + " ORDER BY bar_close_ms DESC, symbol LIMIT ?", values + [limit]).fetchall()
            decoded = _receipts(db, [json.loads(r[0]) for r in rows])
            row = db.execute("SELECT payload FROM meta WHERE key='activation:v128'").fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LinesUnavailable("lines_book_invalid") from error
    activated = json.loads(row[0]).get("activated_ms") if row else None
    return [classify(row, activated, now_ms) for row in decoded]


def database(runtime: Path) -> Path:
    return Path(runtime) / DATABASE


def _policy(db: sqlite3.Connection) -> dict | None:
    row = db.execute("SELECT payload FROM meta WHERE key='performance_policy'").fetchone()
    value = json.loads(row[0]) if row else None
    return value if isinstance(value, dict) else None


def _versioned_rows(db: sqlite3.Connection, *, kind: str, performance_version: str) -> tuple[list[dict], dict | None, str, int | None]:
    """Read current event cards or a frozen archive without writing the monitor book."""
    policy = _policy(db)
    if performance_version in ("current", "legacy"):
        selected_protocol = "spike-v11-2-lines-monitor-v1" if performance_version == "legacy" else JOINT_PROTOCOL
        rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events WHERE kind=? AND json_extract(payload,'$.protocol')=?", (kind, selected_protocol))]
        if performance_version == "legacy":
            basis = ((rows[0].get("performance") or {}).get("basis", LEGACY_PERFORMANCE_BASIS)
                     if rows else LEGACY_PERFORMANCE_BASIS)
            return _receipts(db, rows), policy, basis, (policy or {}).get("changed_at_ms")
        return _receipts(db, rows), policy, (policy or {}).get("version", LEGACY_PERFORMANCE_BASIS), None
    if kind != "joint":
        raise ValueError("frozen projections are only available for joint positions")
    prefix = "baseline" if performance_version == "baseline" else "previous"
    frozen_version = (policy or {}).get(f"{prefix}_version")
    snapshot_ms = (policy or {}).get(f"{prefix}_snapshot_ms")
    if not frozen_version or not isinstance(snapshot_ms, int):
        return [], policy, frozen_version or LEGACY_PERFORMANCE_BASIS, None
    try:
        records = db.execute(
            "SELECT e.payload,p.payload FROM events e JOIN performance_versions p ON p.event_id=e.id "
            "WHERE e.kind='joint' AND p.version=? AND p.saved_ms=?", (frozen_version, snapshot_ms)).fetchall()
    except sqlite3.OperationalError:
        # Pre-migration books are readable as current but have no honest baseline.
        records = []
    rows = []
    for event_payload, performance_payload in records:
        event = json.loads(event_payload)
        performance = json.loads(performance_payload)
        performance.setdefault("basis", frozen_version)
        event["performance"] = performance
        rows.append(event)
    return rows, policy, frozen_version, snapshot_ms


def _projection_versions(rows: list[dict], fallback: str) -> tuple[list[str], dict[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("kind") != "joint":
            continue
        performance = row.get("performance") or {}
        basis = performance.get("basis") if isinstance(performance, dict) else None
        basis = basis if isinstance(basis, str) and basis else fallback
        counts[basis] = counts.get(basis, 0) + 1
    return sorted(counts), dict(sorted(counts.items()))


def ledger(path: Path, *, kind: str, now_ms: int, period: str = "all", timeframe: str | None = None,
           scope: str = "all", search: str = "", outcome: str = "all", sort: str = "newest",
           limit: int = 1000, performance_version: str = "current") -> dict:
    """The 信号中心 ledger for joint positions: same summarize/performance rules, same periods."""
    from yoyo.monitor.signal_analytics import OUTCOMES, performance, period_start, search_key, summarize
    if (kind not in KINDS or period not in ("all", "today", "week") or scope not in ("all", "live")
            or (timeframe is not None and timeframe not in TIMEFRAMES[kind])
            or outcome not in ("all", *OUTCOMES) or sort not in ("newest", "oldest", "r_desc", "r_asc")
            or performance_version not in ("current", "legacy", "baseline", "previous")):
        raise ValueError("unsupported ledger filter")
    try:
        db = _connect(path)
        try:
            raw_rows, policy, basis, snapshot_ms = _versioned_rows(
                db, kind=kind, performance_version=performance_version)
            activation = db.execute("SELECT payload FROM meta WHERE key=?", ("activation:v128" if performance_version == "current" else "activation",)).fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LinesUnavailable("lines_book_invalid") from error
    activated = json.loads(activation[0]).get("activated_ms") if activation else None
    frozen = performance_version in ("baseline", "previous", "legacy")
    as_of_ms = snapshot_ms if frozen and snapshot_ms is not None else now_ms
    rows = [classify(row, activated, as_of_ms) for row in raw_rows]
    if frozen or performance_version == "legacy":
        for row in rows:
            row["is_fresh"] = False
    projection_versions, projection_version_counts = _projection_versions(rows, basis)
    start, query = period_start(as_of_ms, period), search_key(search)
    items = []
    for row in rows:
        if start is not None and row["bar_close_ms"] < start:
            continue
        if (timeframe and row["timeframe"] != timeframe) or (scope == "live" and row["display_state"] != "live"):
            continue
        if query and query not in search_key(row["symbol"]):
            continue
        row.setdefault("side", "long")
        status, value = performance(row)
        if outcome != "all" and status != outcome:
            continue
        row.update(outcome_status=status, sort_r=value)
        items.append(row)
    stats = summarize(items)
    by_timeframe = [dict(timeframe=tf, **summarize([r for r in items if r["timeframe"] == tf]))
                    for tf in TIMEFRAMES[kind]]
    if sort in ("r_desc", "r_asc"):
        sign = -1 if sort == "r_desc" else 1
        items.sort(key=lambda r: (r["sort_r"] is None, sign * (r["sort_r"] or 0), -r["bar_close_ms"], r["id"]))
    else:
        items.sort(key=lambda r: (r["bar_close_ms"], r["id"]), reverse=sort == "newest")
    available = [{"value": "current", "label": "V12.8 · 原价格退出"},
                 {"value": "legacy", "label": "V11.2 · 升级前旧版记录"}]
    for alias, label in (("baseline", "原价格退出 · 初始快照"), ("previous", "上一版退出 · 固定快照")):
        frozen_version = (policy or {}).get(f"{alias}_version")
        frozen_snapshot_ms = (policy or {}).get(f"{alias}_snapshot_ms")
        if not (kind == "joint" and policy and frozen_version and isinstance(frozen_snapshot_ms, int)):
            continue
        try:
            db = _connect(path)
            try:
                archived = db.execute("SELECT 1 FROM performance_versions WHERE version=? AND saved_ms=? LIMIT 1",
                                      (frozen_version, frozen_snapshot_ms)).fetchone()
            finally:
                db.close()
        except sqlite3.OperationalError:
            archived = None
        except sqlite3.Error as error:
            raise LinesUnavailable("lines_book_invalid") from error
        if archived:
            available.append({"value": alias, "label": label})
    return {"items": items[:limit], "total": len(items), "stats": stats, "by_timeframe": by_timeframe,
            "as_of_ms": as_of_ms, "period": period, "scope": scope, "basis": basis,
            "selected_performance_version": performance_version,
            "available_performance_versions": available,
            "performance_snapshot_ms": snapshot_ms if frozen else None,
            "performance_policy": policy,
            "projection_versions": projection_versions, "projection_version_counts": projection_version_counts}
