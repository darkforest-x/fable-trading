"""Read-only monitor input and the frozen SPIKE forward-paper replay seam.

This adapter opens existing monitor databases with SQLite ``mode=ro`` and
never consults display performance as an entry or fill. Paper entries must map
to an exact closed candle at the scheduled future bar open. Exits reuse the
existing fixed V8 replay with its unchanged initial stop, 20bp round-trip cost,
2R/4ATR trail and raw opposite V6 next-open rule.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any
from urllib.parse import quote

from yoyo.research_workspace.strategies import Plugin, get_plugin


TIMEFRAME_MS = {"15m": 900_000, "30m": 1_800_000, "1H": 3_600_000, "4H": 14_400_000}
_MAX_EVENTS = 2_000
_MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_MANIFEST_REQUIRED = (
    "yoyo/research_workspace/strategies.py",
    "yoyo/research_workspace/paper_source.py",
    "yoyo/evaluation/spike_v1_v8_be05.py",
    "yoyo/evaluation/spike_exit_policy_study.py",
    "yoyo/evaluation/spike_v6_wvf_study.py",
    "yoyo/evaluation/spike_v7_fast.py",
    "yoyo/evaluation/spike_v128_recent.py",
    "yoyo/evaluation/spike_burst_replay.py",
    "yoyo/monitor/v128_signals.py",
    "yoyo/monitor/v128_lines.py",
    "yoyo/monitor/store.py",
    "yoyo/monitor/joint_notifications.py",
    "yoyo/monitor/spike_lines_worker.py",
    "requirements.txt",
    "constraints-ci.txt",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def source_manifest(root: Path) -> dict[str, Any]:
    """Hash the active Python/Pine tree and paper modules for run pinning.

    Hashing the complete in-repository ``yoyo`` implementation tree is
    intentionally conservative: it covers the recursive local dependencies of
    the fixed exits, V12.8 feature builder, signal store and line adapter. No
    data, runtime state, settings, weights, or secrets are read.
    """
    base = Path(root).expanduser().resolve(strict=True)
    if not base.is_dir():
        raise ValueError("repository root is not a directory")
    missing = [name for name in _MANIFEST_REQUIRED if not (base / name).is_file()]
    if missing:
        raise ValueError("paper source files are missing: " + ", ".join(missing))

    yoyo = base / "yoyo"
    if not yoyo.is_dir() or yoyo.is_symlink():
        raise ValueError("paper implementation tree is missing or unsafe")
    files: dict[str, str] = {}
    for current, dirs, names in os.walk(yoyo, topdown=True, followlinks=False):
        here = Path(current)
        symlink_dirs = [name for name in dirs if (here / name).is_symlink()]
        if symlink_dirs:
            raise ValueError(f"symlinked source directory is not allowed: {here / symlink_dirs[0]}")
        dirs[:] = sorted(dirs)
        for name in sorted(names):
            path = here / name
            if path.suffix not in {".py", ".pine"}:
                continue
            if path.is_symlink():
                raise ValueError(f"symlinked paper source is not allowed: {path.relative_to(base)}")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(base):
                raise ValueError(f"paper source escapes repository: {path.relative_to(base)}")
            rel = path.relative_to(base).as_posix()
            files[rel] = _sha256_file(path)

    for name in ("requirements.txt", "constraints-ci.txt"):
        path = base / name
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(base):
            raise ValueError(f"unsafe dependency lock file: {name}")
        files[name] = _sha256_file(path)

    required_in_hash = set(_MANIFEST_REQUIRED)
    if not required_in_hash.issubset(files):
        raise ValueError("required paper source is outside the hashed implementation tree")
    packages = {}
    for package in ("numpy", "pandas", "numba"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    environment = {"python": sys.version, "packages": packages}
    dependency_files = {key: files[key] for key in ("requirements.txt", "constraints-ci.txt")}
    dependency_hash = _canonical_hash({"files": dependency_files, "environment": environment})
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=base, check=True,
                                 capture_output=True, text=True, timeout=5)
        commit = result.stdout.strip()
        if not commit:
            commit = None
    except (OSError, subprocess.SubprocessError):
        commit = None
    # Keep source identity independent from Git metadata. A UI/docs-only commit
    # must not look like an execution-source change when every hashed file is
    # byte-identical.
    manifest_hash = _canonical_hash({"files": files, "environment": environment})
    return {
        "files": dict(sorted(files.items())),
        "hash": manifest_hash,
        "commit": commit,
        "dependency_hash": dependency_hash,
        "environment": environment,
    }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"monitor database missing or symlinked: {path.name}")
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error as exc:
        raise ValueError(f"monitor database unavailable: {path.name}") from exc


def _json_object(raw: object, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {what} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {what} shape")
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _valid_tick(value: object) -> float | None:
    result = _number(value)
    return result if result is not None and result > 0 else None


class MonitorSource:
    """Read normalized V12.8 events and closed raw bars without monitor writes."""

    def __init__(self, runtime: Path):
        self.runtime = Path(runtime).expanduser()

    def _database(self, plugin: Plugin) -> Path:
        if plugin.source_database not in {"monitor.sqlite3", "spike-lines-v1.sqlite3"}:
            raise ValueError("strategy has no paper signal source")
        return self.runtime / plugin.source_database

    @staticmethod
    def _after_clause(after_ms: int | tuple[int, str], detected_column: str) -> tuple[str, list[object]]:
        if detected_column not in {"detected_ms", "detected_at_ms"}:
            raise ValueError("invalid event clock column")
        if isinstance(after_ms, tuple) and len(after_ms) == 2:
            stamp, event_id = after_ms
            if type(stamp) is not int or stamp < 0 or not isinstance(event_id, str) or not event_id:
                raise ValueError("invalid event cursor")
            return (f"({detected_column}>? OR ({detected_column}=? AND id>?))",
                    [stamp, stamp, event_id])
        if type(after_ms) is not int or after_ms < 0:
            raise ValueError("after_ms must be a nonnegative integer or keyset cursor")
        # An integer is an inclusive observation-time lower bound. Returned
        # event cursors are exclusive keysets and preserve ties at 2,000 rows.
        return f"{detected_column}>=?", [after_ms]

    @staticmethod
    def _filters(plugin: Plugin, symbols, timeframes):
        symbols = None if symbols is None else tuple(dict.fromkeys(symbols))
        timeframes = None if timeframes is None else tuple(dict.fromkeys(timeframes))
        if symbols is not None and (not symbols or any(not isinstance(x, str) or not x for x in symbols)):
            return None, None
        if timeframes is not None and (not timeframes or any(x not in TIMEFRAME_MS for x in timeframes)):
            return None, None
        if timeframes is None:
            timeframes = plugin.timeframes
        if any(tf not in plugin.timeframes for tf in timeframes):
            raise ValueError("unsupported timeframe for strategy")
        return symbols, timeframes

    def events(self, plugin: Plugin, after_ms: int | tuple[int, str] = 0,
               symbols=None, timeframes=None) -> list[dict[str, Any]]:
        """Read at most 2,000 fresh closed signal rows in keyset order.

        Pass the final event's ``cursor`` back as ``after_ms`` to continue a
        page. No outcome/performance field participates in admission or fill.
        With symbols=None, include the monitor's whole USDT perpetual universe,
        including new symbols as they appear. An empty list selects nothing.
        """
        if isinstance(plugin, str):
            plugin = get_plugin(plugin)
        if not isinstance(plugin, Plugin) or not plugin.accepts():
            raise ValueError("strategy has no supported monitor event contract")
        selected, frames = self._filters(plugin, symbols, timeframes)
        if selected is None and symbols is not None:
            return []
        if frames is None:
            return []
        now = time.time_ns() // 1_000_000
        is_joint = plugin.id == "spike-v128-joint"
        detected_column = "detected_at_ms" if is_joint else "detected_ms"
        close_column = "bar_close_ms" if is_joint else "close_ms"
        side_column = "json_extract(payload,'$.side')" if is_joint else "side"
        after_sql, after_values = self._after_clause(after_ms, detected_column)
        where = ["kind=?", "json_extract(payload,'$.protocol')=?", "json_extract(payload,'$.kind')=?",
                 after_sql, f"{close_column}<={detected_column}", f"{detected_column}<=?",
                 f"{side_column} IN ('long','short')"]
        values: list[object] = [plugin.kind, plugin.protocol, plugin.kind, *after_values, now]
        timeframe_clauses = []
        for tf in frames:
            timeframe_clauses.append("timeframe=?")
            values.append(tf)
        where.append("(" + " OR ".join(timeframe_clauses) + ")")
        if selected is not None:
            # One JSON parameter avoids SQLite variable limits for a large
            # explicit universe. This reader already requires SQLite JSON1.
            where.append("symbol IN (SELECT value FROM json_each(?))")
            values.append(json.dumps(selected))
        else:
            where.append("symbol LIKE '%-USDT-SWAP'")
        if plugin.id == "spike-v128":
            where.extend(("json_extract(payload,'$.source')='live'",
                          "json_extract(payload,'$.confirmation')='raw'",
                          "json_extract(payload,'$.is_closed')=1"))
        elif is_joint:
            pass
        else:
            return []

        path = self._database(plugin)
        db = _connect_readonly(path)
        try:
            if is_joint:
                select = ("SELECT id,symbol,timeframe,kind,json_extract(payload,'$.side') AS side,"
                          "bar_close_ms AS close_ms,detected_at_ms AS detected_ms,payload FROM events")
            else:
                select = ("SELECT id,symbol,timeframe,kind,side,close_ms,detected_ms,payload FROM events")
            query = (select + " WHERE " + " AND ".join(where)
                     + f" ORDER BY {detected_column} ASC,id ASC LIMIT ?")
            rows = db.execute(query, [*values, _MAX_EVENTS]).fetchall()
            market_ticks = {}
            if plugin.id == "spike-v128":
                for row in rows:
                    key = (row["symbol"], row["timeframe"])
                    if key in market_ticks:
                        continue
                    market = db.execute("SELECT payload FROM markets WHERE symbol=? AND timeframe=?", key).fetchone()
                    if market is None:
                        market_ticks[key] = None
                    else:
                        market_ticks[key] = _valid_tick(_json_object(market[0], "market").get("tick_size"))
        except sqlite3.Error as exc:
            raise ValueError("monitor event schema unavailable") from exc
        finally:
            db.close()

        normalized: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_object(row["payload"], "event")
            # Columns are the journal's identity; reject inconsistent payloads
            # instead of silently rewriting their lineage.
            if (payload.get("protocol") != plugin.protocol or payload.get("kind") != plugin.kind
                    or payload.get("symbol", row["symbol"]) != row["symbol"]
                    or payload.get("timeframe") != row["timeframe"]
                    or payload.get("side", row["side"]) != row["side"]
                    or payload.get("bar_close_ms") != int(row["close_ms"])
                    or payload.get("detected_at_ms") != int(row["detected_ms"])):
                raise ValueError("monitor event identity mismatch")
            payload["id"] = row["id"]
            payload["symbol"] = row["symbol"]
            payload["timeframe"] = row["timeframe"]
            payload["side"] = row["side"]
            payload["bar_close_ms"] = int(row["close_ms"])
            payload["detected_at_ms"] = int(row["detected_ms"])
            if plugin.id == "spike-v128-joint":
                from yoyo.monitor.joint_notifications import is_joint_event

                if not is_joint_event(payload):
                    raise ValueError("invalid joint monitor event")
                tick = _valid_tick(payload.get("tick"))
                stop = payload.get("reference_stop")
            else:
                if payload.get("is_closed") is not True or not plugin.accepts(payload):
                    raise ValueError("invalid raw monitor event")
                tick = market_ticks.get((row["symbol"], row["timeframe"]))
                stop = payload.get("initial_stop")
            if tick is None:
                raise ValueError("signal tick is unavailable")
            stop_value = _number(stop)
            normalized.append({
                "id": row["id"], "symbol": row["symbol"], "timeframe": row["timeframe"],
                "side": row["side"], "bar_close_ms": int(row["close_ms"]),
                "detected_at_ms": int(row["detected_ms"]),
                "initial_stop": stop_value,
                "tick": tick,
                "source_payload": payload,
                "cursor": (int(row["detected_ms"]), row["id"]),
            })
        return normalized

    def checkpoint(self, symbol: str, timeframe: str, now_ms: int) -> dict[str, Any] | None:
        """Read the primary monitor's compressed, closed raw candle checkpoint."""
        if not isinstance(symbol, str) or not symbol or timeframe not in TIMEFRAME_MS:
            raise ValueError("invalid checkpoint key")
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("invalid checkpoint clock")
        path = self.runtime / "monitor.sqlite3"
        db = _connect_readonly(path)
        try:
            row = db.execute("SELECT payload,updated_ms FROM candle_checkpoints WHERE symbol=? AND timeframe=?",
                             (symbol, timeframe)).fetchone()
            if row is None:
                return None
            market = db.execute("SELECT payload FROM markets WHERE symbol=? AND timeframe=?",
                                (symbol, timeframe)).fetchone()
            if market is None:
                raise ValueError("checkpoint market metadata is unavailable")
            market_payload = _json_object(market[0], "market")
            tick = _valid_tick(market_payload.get("tick_size"))
            if tick is None:
                raise ValueError("checkpoint tick size is unavailable")
            compressed = row[0]
            if not isinstance(compressed, bytes) or len(compressed) > _MAX_CHECKPOINT_BYTES:
                raise ValueError("checkpoint payload exceeds safe size")
            try:
                with gzip.GzipFile(fileobj=__import__("io").BytesIO(compressed), mode="rb") as stream:
                    raw = stream.read(_MAX_CHECKPOINT_BYTES + 1)
                if len(raw) > _MAX_CHECKPOINT_BYTES:
                    raise ValueError("checkpoint payload exceeds safe size")
                candles = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("checkpoint payload is corrupt") from exc
        except sqlite3.Error as exc:
            raise ValueError("monitor checkpoint schema unavailable") from exc
        finally:
            db.close()
        if not isinstance(candles, list):
            raise ValueError("checkpoint candle payload is not a list")
        period = TIMEFRAME_MS[timeframe]
        unique: dict[int, dict[str, Any]] = {}
        for candle in candles:
            if not isinstance(candle, dict):
                raise ValueError("invalid raw candle")
            stamp = candle.get("t")
            if type(stamp) is not int or stamp < 0 or stamp % period:
                raise ValueError("invalid raw candle timestamp")
            if stamp + period > now_ms:
                continue
            try:
                values = {key: float(candle[key]) for key in ("o", "h", "l", "c", "v")}
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid raw candle values") from exc
            o, h, low, close, volume = values.values()
            if (not all(math.isfinite(x) for x in values.values()) or min(o, h, low, close) <= 0
                    or volume < 0 or h < max(o, low, close) or low > min(o, h, close)):
                raise ValueError("invalid raw candle geometry")
            # A duplicate clock is resolved deterministically to the first
            # immutable row, never by trusting a later duplicate silently.
            normalized = {"t": stamp, **values}
            if stamp in unique and unique[stamp] != normalized:
                raise ValueError("duplicate checkpoint timestamp has conflicting values")
            unique[stamp] = normalized
        return {"candles": [unique[key] for key in sorted(unique)], "tick": tick,
                "updated_ms": int(row[1])}


def _pending(reason: str, decision: dict[str, Any], latest_close_ms: int | None) -> dict[str, Any]:
    return {
        "status": "pending", "reason": reason, "signal_close_ms": decision.get("signal_close_ms"),
        "scheduled_entry_ms": decision.get("scheduled_entry_ms"), "entry_price": None,
        "entry_time_ms": None, "initial_stop": _number(decision.get("initial_stop")),
        "initial_risk": None, "exit_price": None, "exit_time_ms": None,
        "exit_bar_end_ms": None, "exit_time_precision": None, "kernel_exit_time_precision": None,
        "mark_time_ms": None,
        "net_r": None, "unrealized_r": None, "protection": _number(decision.get("initial_stop")),
        "bar_close_ms": latest_close_ms, "cost_bp": 20, "production_eligible": False,
    }


def _rejected(reason: str, decision: dict[str, Any], latest_close_ms: int | None) -> dict[str, Any]:
    value = _pending(reason, decision, latest_close_ms)
    value["status"] = "rejected"
    return value


def evaluate_trade(candles: list[dict], timeframe: str, symbol: str, tick: float,
                   decision: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """Evaluate one paper intent through the original fixed-exit kernel.

    The supplied stop is immutable evidence from the signal. The adapter does
    not recalculate or move it. ``scheduled_entry_ms`` must identify an exact
    closed candle; if that candle is missing but a later candle exists, the
    run is censored instead of inventing a later fill.
    """
    if not isinstance(decision, dict):
        raise ValueError("decision must be an object")
    if timeframe not in TIMEFRAME_MS or not isinstance(symbol, str) or not symbol:
        return _rejected("invalid_market_identity", decision, None)
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("invalid replay clock")
    period = TIMEFRAME_MS[timeframe]
    side_text = decision.get("side")
    if side_text not in {"long", "short"}:
        return _rejected("invalid_side", decision, None)
    side = 1 if side_text == "long" else -1
    signal_close_ms = decision.get("signal_close_ms")
    scheduled = decision.get("scheduled_entry_ms")
    if (type(signal_close_ms) is not int or signal_close_ms < 0
            or type(scheduled) is not int or scheduled < signal_close_ms
            or scheduled % period):
        return _rejected("invalid_entry_clock", decision, None)
    supplied_stop = _number(decision.get("initial_stop"))
    if supplied_stop is None or supplied_stop <= 0:
        return _rejected("missing_initial_stop", decision, None)
    supplied_tick = _valid_tick(tick)
    if supplied_tick is None:
        return _rejected("invalid_tick", decision, None)
    if not isinstance(candles, list):
        raise ValueError("candles must be a list")

    # Keep only whole bars available at this evaluation clock, then require a
    # strictly ordered unique source sequence rather than repairing bad data.
    closed = []
    seen = set()
    last_stamp = None
    for raw in candles:
        if not isinstance(raw, dict):
            raise ValueError("invalid candle row")
        stamp = raw.get("t")
        if type(stamp) is not int or stamp < 0 or stamp % period:
            raise ValueError("invalid candle timestamp")
        if stamp in seen or (last_stamp is not None and stamp <= last_stamp):
            raise ValueError("candle timestamps must be unique and ordered")
        seen.add(stamp)
        last_stamp = stamp
        if stamp + period > now_ms:
            continue
        try:
            item = {key: float(raw[key]) for key in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid candle values") from exc
        o, h, low, close, volume = item.values()
        if (not all(math.isfinite(x) for x in item.values()) or min(o, h, low, close) <= 0
                or volume < 0 or h < max(o, low, close) or low > min(o, h, close)):
            raise ValueError("invalid candle geometry")
        closed.append({"t": stamp, **item})

    latest_close_ms = closed[-1]["t"] + period if closed else None
    target_index = next((i for i, bar in enumerate(closed) if bar["t"] == scheduled), None)
    if target_index is None:
        if scheduled > now_ms:
            return _pending("entry_not_open", decision, latest_close_ms)
        if now_ms < scheduled + period:
            return _pending("awaiting_closed_entry_bar", decision, latest_close_ms)
        if any(bar["t"] > scheduled for bar in closed):
            result = _pending("scheduled_entry_bar_missing", decision, latest_close_ms)
            result["status"] = "censored"
            return result
        return _pending("awaiting_closed_entry_bar", decision, latest_close_ms)

    if not closed:
        return _pending("awaiting_closed_entry_bar", decision, latest_close_ms)

    # Lazy imports keep catalog/API startup independent from pandas and numpy.
    import numpy as np
    import pandas as pd
    from yoyo.evaluation import spike_v1_v8_be05 as fixed
    from yoyo.evaluation import spike_v128_recent as recent

    raw_frame = pd.DataFrame(closed)
    frame = pd.DataFrame({"open": raw_frame.o.astype(float), "high": raw_frame.h.astype(float),
                          "low": raw_frame.l.astype(float), "close": raw_frame.c.astype(float),
                          "volume": raw_frame.v.astype(float)})
    frame.index = pd.DatetimeIndex(pd.to_datetime(raw_frame.t.astype("int64"), unit="ms", utc=True))
    minutes = period // 60_000
    asset = symbol.split("-", 1)[0].split("/", 1)[0]
    facts = recent._generic_facts(frame, asset, supplied_tick, minutes)
    execution_frame = facts["frame"]
    entry_i = target_index
    entry_price = float(closed[entry_i]["o"])
    initial_risk = side * (entry_price - supplied_stop)
    if (not math.isfinite(entry_price) or entry_price <= 0 or not math.isfinite(initial_risk)
            or initial_risk <= 0):
        return _rejected("invalid_initial_risk", decision, latest_close_ms)
    gaps = (execution_frame.index.to_series().diff().ne(pd.Timedelta(minutes=minutes)).to_numpy(bool))
    if len(gaps):
        gaps[0] = False
    raw_side = np.asarray(facts["side"], dtype=int)
    arrays = {name: execution_frame[name].to_numpy(float)
              for name in ("open", "high", "low", "close", "atr")}
    cache = {"bars": execution_frame, "tick": supplied_tick,
             "data_gap": pd.Series(gaps, index=execution_frame.index)}
    base_context = fixed.base.StreamContext(
        path=Path("."), key=f"paper_{symbol.replace('/', '_')}_{timeframe}", receipt={},
        cache=cache,
        signals_ledger=pd.DataFrame({"signal_bar_open": [execution_frame.index[max(0, entry_i - 1)]],
                                     "signal_i": [max(0, entry_i - 1)]}),
        minutes=minutes,
        identity={"venue": "okx", "symbol": symbol, "asset": asset, "timeframe_min": minutes},
    )
    spec = fixed.base.ExecutionSpec(tick=supplied_tick)
    prepared = fixed.PreparedArm(
        context=base_context, arm="v8", cohort="v7_both", frame=execution_frame,
        gap=gaps, allowed=np.ones(len(execution_frame), dtype=bool), raw_side=raw_side,
        open=arrays["open"], high=arrays["high"], low=arrays["low"], close=arrays["close"],
        atr=arrays["atr"], ordinal={stamp: i for i, stamp in enumerate(execution_frame.index)}, spec=spec,
    )
    signal_open = signal_close_ms - period
    row = pd.Series({
        "signal_i": max(0, entry_i - 1),
        "signal_bar_open": pd.to_datetime(signal_open, unit="ms", utc=True),
        "entry_i": entry_i,
        "entry_time": execution_frame.index[entry_i],
        "side": side,
        "entry_price": entry_price,
        "initial_stop": supplied_stop,
        "initial_risk": initial_risk,
        "initial_risk_frac": initial_risk / entry_price,
    })
    result = fixed.replay_fixed_entry(base_context, row, arm="v8", enable_be=False, prepared=prepared)
    kernel_reason = str(result.get("exit_reason") or "unknown")
    censored = bool(result.get("censored"))
    is_open = censored and kernel_reason == "boundary_mark"
    if is_open:
        status = "open"
        reason = "position_open_at_latest_close"
    elif censored:
        status = "censored"
        reason = kernel_reason
    else:
        status = "closed"
        reason = kernel_reason
    raw_exit_time = result.get("exit_time")
    exit_open_ms = None
    if raw_exit_time is not None and not (isinstance(raw_exit_time, float) and math.isnan(raw_exit_time)):
        exit_open_ms = int(pd.Timestamp(raw_exit_time).value // 1_000_000)
    if is_open:
        exit_precision = "last_complete_close"
        exit_time_ms = None
        exit_bar_end_ms = None
        mark_time_ms = latest_close_ms
    elif not censored and kernel_reason in {"initial_stop", "trailing_stop"}:
        exit_precision = "within_bar"
        exit_time_ms = exit_open_ms
        exit_bar_end_ms = None if exit_open_ms is None else exit_open_ms + period
        mark_time_ms = None
    elif not censored:
        exit_precision = "bar_open"
        exit_time_ms = exit_open_ms
        exit_bar_end_ms = exit_open_ms
        mark_time_ms = None
    else:
        exit_precision = str(result.get("exit_time_precision") or "unknown_gap")
        exit_time_ms = exit_open_ms
        exit_bar_end_ms = None
        mark_time_ms = None

    current_close = float(execution_frame.close.iloc[-1])
    risk_frac = initial_risk / entry_price
    unrealized_r = None
    if is_open:
        unrealized_r = side * (current_close - entry_price) / initial_risk - 0.002 / risk_frac

    def safe(value):
        number = _number(value)
        return number

    return {
        "status": status,
        "reason": reason,
        "kernel_reason": kernel_reason,
        "signal_close_ms": signal_close_ms,
        "scheduled_entry_ms": scheduled,
        "entry_price": entry_price,
        "entry_time_ms": scheduled,
        "initial_stop": supplied_stop,
        "initial_risk": initial_risk,
        "exit_price": None if is_open else safe(result.get("exit_price")),
        "exit_time_ms": exit_time_ms,
        "exit_bar_open_ms": exit_open_ms,
        "exit_bar_end_ms": exit_bar_end_ms,
        "exit_time_precision": exit_precision,
        "kernel_exit_time_precision": result.get("exit_time_precision"),
        "mark_time_ms": mark_time_ms,
        "mark_price": current_close if is_open else None,
        "net_r": None if is_open or censored else safe(result.get("net_r")),
        "unrealized_r": safe(unrealized_r),
        "protection": safe(result.get("protection", supplied_stop)),
        "bar_close_ms": latest_close_ms,
        "cost_bp": 20,
        "production_eligible": False,
    }
