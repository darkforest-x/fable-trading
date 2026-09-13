"""Forward-only V7/V8 shadow observer over the monitor's closed-bar cache.

The observer reads the primary monitor's already-fetched OKX candle checkpoints;
it never calls an exchange, sends a notification, creates a model candidate, or
touches an execution path.  V7 is the frozen V6 confirmation plus prior BB200
compression.  V8 changes only admission by requiring the signal close to be no
farther than three current ATR beyond the directional six-MA rope edge.

Every derived row uses OHLCV at or before ``bar_close_ms``.  Forward path bars
are written to an isolated SQLite book only after activation, and are physically
separate from the immutable signal row.  They are future evaluation evidence,
not inputs to the signal decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_progressive import features
from yoyo.evaluation.spike_v6_wvf_study import _data_gap
from yoyo.evaluation.spike_v7_fast import v6_signals, v7_diagnostics
from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.shadow_api import SHADOW_DATABASE
from yoyo.monitor.store import Store, now_ms
from yoyo.monitor.v1_worker import _valid_checkpoint


PROTOCOL = "spike-v7-v8-forward-shadow-v2"
TIMEFRAME_SCOPE = ("30m", "1H", "4H")
V8_MAX_ROPE_DISTANCE_ATR = 3.0
PATH_HORIZON_BARS = 240
# This is a research-only ledger. Five-minute polling keeps the Mac quiet while
# remaining far inside the smallest 30-minute closed-bar cadence.
POLL_SECONDS = 300
DEFAULT_RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"
PRIMARY_DATABASE = "monitor.sqlite3"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _clean(value: object) -> object:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candle_prefix_hashes(candles: list[dict]) -> list[str]:
    """Hash each causal prefix with unambiguous length-delimited candle JSON."""
    digest = hashlib.sha256()
    output: list[str] = []
    for row in candles:
        raw = _json(row).encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        output.append(digest.hexdigest())
    return output


def source_identity() -> dict[str, str]:
    """Bind the shadow contract to every executable signal source."""
    root = Path(__file__).resolve().parents[2]
    paths = (
        Path(__file__),
        root / "yoyo/evaluation/spike_burst_replay.py",
        root / "yoyo/evaluation/spike_burst_progressive.py",
        root / "yoyo/evaluation/spike_burst_v6_structure.py",
        root / "yoyo/evaluation/spike_v7_fast.py",
        root / "yoyo/evaluation/spike_v8_replay.py",
    )
    return {str(path.relative_to(root)): sha256_bytes(path.read_bytes()) for path in paths}


def contract() -> dict[str, object]:
    sources = source_identity()
    fixed = {
        "protocol": PROTOCOL,
        "timeframes": list(TIMEFRAME_SCOPE),
        "v8_max_rope_distance_atr": V8_MAX_ROPE_DISTANCE_ATR,
        "path_horizon_bars": PATH_HORIZON_BARS,
        "entry_semantics": "observation_only_no_fill",
        "market_state_semantics": "descriptive_only_no_gate",
        "sources": sources,
    }
    fixed["config_sha256"] = sha256_bytes(_json(fixed).encode("utf-8"))
    return fixed


def checkpoint_frame(candles: object, timeframe: str) -> pd.DataFrame:
    """Convert one validated closed-bar checkpoint to the frozen feature input."""
    if timeframe not in TIMEFRAME_SCOPE or not _valid_checkpoint(candles, timeframe):
        raise ValueError("invalid_or_out_of_scope_checkpoint")
    assert isinstance(candles, list)
    frame = pd.DataFrame(candles).rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    frame.index = pd.to_datetime(frame.pop("t"), unit="ms", utc=True)
    return frame[["open", "high", "low", "close", "volume"]]


def analyze_checkpoint(candles: object, timeframe: str, *, tick: float) -> pd.DataFrame:
    """Return causal V7/V8 diagnostics for every bar in one closed checkpoint.

    Columns read: raw ``t/o/h/l/c/v`` for the current and preceding bars.  The
    longest decision window is V7's current BB200 width against the preceding
    500 widths, followed by the fully preceding twelve-bar squeeze run.  No
    column from a later bar is shifted backward or joined to a decision row.
    """
    if isinstance(tick, bool) or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick_must_be_positive")
    raw = checkpoint_frame(candles, timeframe)
    minutes = TIMEFRAMES[timeframe] // 60_000
    frame = features(raw)
    gap = _data_gap(frame, minutes)
    signals = v6_signals(frame, minutes)
    bb = v7_diagnostics(frame, data_gap=gap)
    long = signals.long_signal.fillna(False).astype(bool)
    short = signals.short_signal.fillna(False).astype(bool)
    if (long & short).any():
        raise ValueError("ambiguous_v6_side")
    side = pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=frame.index, dtype=int)
    v7 = ((long | short) & bb.v7_ready.fillna(False).astype(bool)
          & bb.prior_squeeze_run3.fillna(False).astype(bool))
    edge = pd.Series(np.where(side.eq(1), frame.ropeHigh,
                              np.where(side.eq(-1), frame.ropeLow, np.nan)), index=frame.index)
    distance = side * (frame.close - edge) / frame.atr.where(frame.atr.gt(0))
    v8 = v7 & distance.le(V8_MAX_ROPE_DISTANCE_ATR).fillna(False)
    reason = np.where(~v7, "not_v7", np.where(v8, "within_3atr", "overheated_gt_3atr"))
    output = frame[["open", "high", "low", "close", "volume", "atr", "ropeHigh", "ropeLow",
                    "md", "sb", "pastWidth", "pastCrosses"]].copy()
    output["bar_open_ms"] = (output.index.view("int64") // 1_000_000).astype(np.int64)
    output["bar_close_ms"] = output.bar_open_ms + TIMEFRAMES[timeframe]
    output["v6_side"] = side
    output["v7"] = v7
    output["v8"] = v8
    output["rope_distance_atr"] = distance
    output["v8_reason"] = reason
    output["bb_ready"] = bb.v7_ready.astype(bool)
    output["ma_ready"] = frame.ropeHigh.notna() & frame.ropeLow.notna()
    output["prior_squeeze_run3"] = bb.prior_squeeze_run3.astype(bool)
    output["bb_width"] = bb.bb_width
    output["bb_width_p10_prior500"] = bb.bb_width_p10_prior500
    output["up"] = frame.close.gt(frame.close.shift(1))
    output["down"] = frame.close.lt(frame.close.shift(1))
    output["body_above_six"] = frame[["open", "close"]].min(axis=1).gt(frame.ropeHigh)
    output["body_below_six"] = frame[["open", "close"]].max(axis=1).lt(frame.ropeLow)
    prefix = candle_prefix_hashes(candles)  # type: ignore[arg-type]
    output["input_prefix_sha256"] = prefix
    return output


class ShadowStore:
    """Isolated append-only decision book; it has no delivery or order tables."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS shadow_events (
                    id TEXT PRIMARY KEY, protocol TEXT NOT NULL, symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL, side INTEGER NOT NULL,
                    bar_open_ms INTEGER NOT NULL, bar_close_ms INTEGER NOT NULL,
                    detected_ms INTEGER NOT NULL, v8_admitted INTEGER NOT NULL,
                    rope_distance_atr REAL NOT NULL, input_prefix_sha256 TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS shadow_event_time ON shadow_events(bar_close_ms DESC);
                CREATE TABLE IF NOT EXISTS shadow_cells (
                    symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                    checkpoint_updated_ms INTEGER NOT NULL, bar_open_ms INTEGER,
                    bar_close_ms INTEGER, status TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(symbol,timeframe));
                CREATE TABLE IF NOT EXISTS shadow_bars (
                    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_open_ms INTEGER NOT NULL,
                    bar_close_ms INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
                    low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                    input_prefix_sha256 TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(symbol,timeframe,bar_open_ms));
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    timeframe TEXT NOT NULL, bar_close_ms INTEGER NOT NULL,
                    coverage INTEGER NOT NULL, expected INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(timeframe,bar_close_ms));
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_meta(self, key: str, default: object = None) -> object:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_meta(self, key: str, value: object) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, _json(value)))

    @staticmethod
    def event_id(event: dict[str, object]) -> str:
        key = "|".join(str(event[name]) for name in
                       ("protocol", "symbol", "timeframe", "bar_close_ms", "side"))
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    def insert_event(self, event: dict[str, object]) -> tuple[str, bool]:
        event = {key: _clean(value) for key, value in event.items()}
        event_id = self.event_id(event)
        event["id"] = event_id
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO shadow_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, event["protocol"], event["symbol"], event["timeframe"], event["side"],
                 event["bar_open_ms"], event["bar_close_ms"], event["detected_ms"],
                 int(bool(event["v8_admitted"])), event["rope_distance_atr"],
                 event["input_prefix_sha256"], event["config_sha256"], _json(event)),
            )
        return event_id, cursor.rowcount == 1

    def cell(self, symbol: str, timeframe: str) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM shadow_cells WHERE symbol=? AND timeframe=?",
                             (symbol, timeframe)).fetchone()
        return None if row is None else dict(row) | {"payload": json.loads(row["payload"])}

    def upsert_cell(self, symbol: str, timeframe: str, checkpoint_updated_ms: int,
                    status: str, payload: dict[str, object]) -> None:
        clean = {key: _clean(value) for key, value in payload.items()}
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO shadow_cells VALUES (?,?,?,?,?,?,?)", (
                symbol, timeframe, checkpoint_updated_ms, clean.get("bar_open_ms"),
                clean.get("bar_close_ms"), status, _json(clean)))

    def has_recent_event(self, symbol: str, timeframe: str, close_ms: int) -> bool:
        horizon = PATH_HORIZON_BARS * TIMEFRAMES[timeframe]
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM shadow_events WHERE symbol=? AND timeframe=? "
                "AND bar_close_ms<=? AND bar_close_ms>? LIMIT 1",
                (symbol, timeframe, close_ms, close_ms - horizon),
            ).fetchone()
        return row is not None

    def insert_bar(self, symbol: str, timeframe: str, row: pd.Series) -> None:
        payload = {key: _clean(value) for key, value in row.to_dict().items()}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO shadow_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                symbol, timeframe, payload["bar_open_ms"], payload["bar_close_ms"],
                payload["open"], payload["high"], payload["low"], payload["close"],
                payload["volume"], payload["input_prefix_sha256"], _json(payload)))

    def list_events(self, limit: int = 200) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM shadow_events ORDER BY bar_close_ms DESC,id LIMIT ?",
                              (int(limit),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def status(self) -> dict[str, object]:
        with self.connect() as db:
            events = db.execute("SELECT COUNT(*),SUM(v8_admitted) FROM shadow_events").fetchone()
            cells = db.execute("SELECT status,COUNT(*) n FROM shadow_cells GROUP BY status").fetchall()
            bars = db.execute("SELECT COUNT(*) FROM shadow_bars").fetchone()[0]
            snapshots = db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        return {"protocol": PROTOCOL, "events": int(events[0]),
                "v8_admitted": int(events[1] or 0), "path_bars": int(bars),
                "market_snapshots": int(snapshots),
                "cells": {str(row[0]): int(row[1]) for row in cells},
                "activation": self.get_meta("activation"), "scan": self.get_meta("scan")}

    def record_market_snapshot(self, timeframe: str, expected: int,
                               *, after_ms: int) -> dict[str, object] | None:
        """Persist post-activation breadth only; it is not an admission gate."""
        with self.connect() as db:
            grouped = db.execute(
                "SELECT bar_close_ms,COUNT(*) n FROM shadow_cells "
                "WHERE timeframe=? AND status IN ('ready','warming') AND bar_close_ms IS NOT NULL "
                "AND bar_close_ms>? GROUP BY bar_close_ms ORDER BY bar_close_ms DESC",
                (timeframe, int(after_ms))).fetchall()
            chosen = next((row for row in grouped if int(row[1]) >= max(1, math.ceil(expected * .80))), None)
            if chosen is None:
                return None
            close_ms, coverage = int(chosen[0]), int(chosen[1])
            candidates = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM shadow_cells WHERE timeframe=? AND status IN ('ready','warming') "
                "AND bar_close_ms=?", (timeframe, close_ms))]
            payloads = [row for row in candidates if bool(row.get("ma_ready"))]
            previous_60m = db.execute(
                "SELECT payload FROM market_snapshots WHERE timeframe=? AND bar_close_ms=?",
                (timeframe, close_ms - 3_600_000),
            ).fetchone()
            previous_bar = db.execute(
                "SELECT payload FROM market_snapshots WHERE timeframe=? AND bar_close_ms=?",
                (timeframe, close_ms - TIMEFRAMES[timeframe]),
            ).fetchone()
        if len(payloads) < max(1, math.ceil(expected * .80)):
            return None
        coverage = len(payloads)
        def share(name: str) -> float:
            return float(sum(bool(row.get(name)) for row in payloads) / len(payloads))
        snapshot: dict[str, object] = {
            "protocol": PROTOCOL, "semantics": "descriptive_only_no_gate",
            "timeframe": timeframe, "bar_close_ms": close_ms,
            "coverage": coverage, "expected": expected,
            "up_share": share("up"), "down_share": share("down"),
            "body_above_six_share": share("body_above_six"),
            "body_below_six_share": share("body_below_six"),
            "joint_up_share": float(sum(bool(row.get("up")) and bool(row.get("body_above_six"))
                                         for row in payloads) / len(payloads)),
            "joint_down_share": float(sum(bool(row.get("down")) and bool(row.get("body_below_six"))
                                           for row in payloads) / len(payloads)),
            "v7_signal_count": int(sum(bool(row.get("v7")) for row in payloads)),
            "v8_signal_count": int(sum(bool(row.get("v8")) for row in payloads)),
        }
        if previous_60m is not None:
            old = json.loads(previous_60m[0])
            snapshot["joint_up_delta_60m"] = snapshot["joint_up_share"] - float(old["joint_up_share"])
            snapshot["joint_down_delta_60m"] = snapshot["joint_down_share"] - float(old["joint_down_share"])
        else:
            snapshot["joint_up_delta_60m"] = None
            snapshot["joint_down_delta_60m"] = None
        if previous_bar is not None:
            old_bar = json.loads(previous_bar[0])
            snapshot["joint_up_delta_previous_bar"] = (
                snapshot["joint_up_share"] - float(old_bar["joint_up_share"])
            )
            snapshot["joint_down_delta_previous_bar"] = (
                snapshot["joint_down_share"] - float(old_bar["joint_down_share"])
            )
        else:
            snapshot["joint_up_delta_previous_bar"] = None
            snapshot["joint_down_delta_previous_bar"] = None
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO market_snapshots VALUES (?,?,?,?,?)",
                       (timeframe, close_ms, coverage, expected, _json(snapshot)))
        return snapshot


class V78ShadowScanner:
    """Process only checkpoint revisions and keep all side effects isolated."""

    def __init__(self, runtime: Path | str = DEFAULT_RUNTIME, *, clock: Callable[[], int] = now_ms):
        self.runtime = Path(runtime)
        self.primary = Store(self.runtime / PRIMARY_DATABASE)
        self.shadow = ShadowStore(self.runtime / SHADOW_DATABASE)
        self.clock = clock
        self.fixed = contract()

    def _activation(self) -> dict[str, object]:
        current = self.shadow.get_meta("activation")
        if current is None:
            current = {"protocol": PROTOCOL, "activated_ms": int(self.clock()),
                       "config_sha256": self.fixed["config_sha256"], "contract": self.fixed}
            self.shadow.set_meta("activation", current)
        if (not isinstance(current, dict) or current.get("protocol") != PROTOCOL
                or current.get("config_sha256") != self.fixed["config_sha256"]):
            raise RuntimeError("shadow_contract_changed_use_new_protocol_and_database")
        return current

    def _checkpoint_heads(self) -> list[tuple[str, str, int]]:
        marks = ",".join("?" for _ in TIMEFRAME_SCOPE)
        with self.primary.connect() as db:
            rows = db.execute(
                f"SELECT symbol,timeframe,updated_ms FROM candle_checkpoints WHERE timeframe IN ({marks}) "
                "ORDER BY timeframe,symbol", TIMEFRAME_SCOPE).fetchall()
        return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]

    def _tick(self, symbol: str, timeframe: str) -> float:
        market = self.primary.get_market(symbol, timeframe)
        if not isinstance(market, dict):
            raise ValueError("market_tick_unavailable")
        tick = float(market.get("tick_size"))
        if not math.isfinite(tick) or tick <= 0:
            raise ValueError("market_tick_invalid")
        return tick

    @staticmethod
    def _row_payload(row: pd.Series) -> dict[str, object]:
        names = ("bar_open_ms", "bar_close_ms", "open", "high", "low", "close", "volume",
                 "atr", "ropeHigh", "ropeLow", "md", "sb", "pastWidth", "pastCrosses",
                 "v6_side", "v7", "v8", "rope_distance_atr", "v8_reason", "bb_ready", "ma_ready",
                 "prior_squeeze_run3", "bb_width", "bb_width_p10_prior500", "up", "down",
                 "body_above_six", "body_below_six", "input_prefix_sha256")
        return {name: _clean(row[name]) for name in names}

    def run_once(self) -> dict[str, object]:
        activation = self._activation()
        started = int(self.clock())
        heads = self._checkpoint_heads()
        expected = {timeframe: sum(tf == timeframe for _, tf, _ in heads) for timeframe in TIMEFRAME_SCOPE}
        scan: dict[str, object] = {"status": "running", "started_ms": started, "total": len(heads),
                                  "processed": 0, "unchanged": 0, "errors": 0, "events_inserted": 0,
                                  "error_samples": []}
        self.shadow.set_meta("scan", scan)
        for symbol, timeframe, updated_ms in heads:
            saved = self.shadow.cell(symbol, timeframe)
            if saved is not None and int(saved["checkpoint_updated_ms"]) >= updated_ms:
                scan["unchanged"] = int(scan["unchanged"]) + 1
                continue
            try:
                candles = self.primary.load_candle_checkpoint(symbol, timeframe)
                diagnostic = analyze_checkpoint(candles, timeframe, tick=self._tick(symbol, timeframe))
                previous_open = -1 if saved is None or saved.get("bar_open_ms") is None else int(saved["bar_open_ms"])
                new_rows = diagnostic.loc[diagnostic.bar_open_ms.gt(previous_open)]
                for _, row in new_rows.iterrows():
                    close_ms = int(row.bar_close_ms)
                    # The activation instant itself is a boundary, not an
                    # observed post-activation close.  Requiring a strictly
                    # later close prevents an exact-clock historical row from
                    # being admitted on cold start.
                    if close_ms <= int(activation["activated_ms"]):
                        continue
                    if bool(row.v7):
                        event = self._row_payload(row) | {
                            "protocol": PROTOCOL, "symbol": symbol, "timeframe": timeframe,
                            "side": int(row.v6_side), "detected_ms": int(self.clock()),
                            "v8_admitted": bool(row.v8), "config_sha256": self.fixed["config_sha256"],
                            "causal_cutoff_ms": close_ms, "source": "forward_shadow",
                            "notification_eligible": False, "execution_eligible": False,
                        }
                        _, inserted = self.shadow.insert_event(event)
                        scan["events_inserted"] = int(scan["events_inserted"]) + int(inserted)
                        if inserted:
                            ordinal = diagnostic.index.get_loc(row.name)
                            for _, prefix_row in diagnostic.iloc[max(0, ordinal - 6):ordinal + 1].iterrows():
                                # Context is useful only when it was itself
                                # observed after activation.  Keeping earlier
                                # OHLC out of the path ledger makes the forward
                                # boundary physical rather than declarative.
                                if int(prefix_row.bar_close_ms) > int(activation["activated_ms"]):
                                    self.shadow.insert_bar(symbol, timeframe, prefix_row)
                    if self.shadow.has_recent_event(symbol, timeframe, close_ms):
                        self.shadow.insert_bar(symbol, timeframe, row)
                latest = self._row_payload(diagnostic.iloc[-1]) | {
                    "symbol": symbol, "timeframe": timeframe, "protocol": PROTOCOL,
                    "config_sha256": self.fixed["config_sha256"],
                }
                status = "ready" if bool(latest["bb_ready"]) else "warming"
                self.shadow.upsert_cell(symbol, timeframe, updated_ms, status, latest)
                scan["processed"] = int(scan["processed"]) + 1
            except Exception as error:  # fail one cell closed; do not hide its class
                scan["errors"] = int(scan["errors"]) + 1
                samples = scan["error_samples"]
                assert isinstance(samples, list)
                if len(samples) < 8:
                    samples.append({"symbol": symbol, "timeframe": timeframe, "error": type(error).__name__})
                # Preserve the previous checkpoint mark so a transient error is retried.
                prior_update = -1 if saved is None else int(saved["checkpoint_updated_ms"])
                payload = ({"bar_open_ms": saved.get("bar_open_ms"), "bar_close_ms": saved.get("bar_close_ms")}
                           if saved is not None else {"bar_open_ms": None, "bar_close_ms": None})
                self.shadow.upsert_cell(symbol, timeframe, prior_update, "error", payload | {
                    "symbol": symbol, "timeframe": timeframe, "error": type(error).__name__,
                    "protocol": PROTOCOL})
        snapshots = {}
        for timeframe in TIMEFRAME_SCOPE:
            if expected[timeframe]:
                snapshot = self.shadow.record_market_snapshot(
                    timeframe, expected[timeframe], after_ms=int(activation["activated_ms"])
                )
                if snapshot is not None:
                    snapshots[timeframe] = snapshot
        scan.update(status="degraded" if scan["errors"] else "idle",
                    finished_ms=int(self.clock()), snapshots=list(snapshots))
        self.shadow.set_meta("scan", scan)
        return scan


def run_forever(runtime: Path, interval_seconds: int = POLL_SECONDS) -> None:
    scanner = V78ShadowScanner(runtime)
    while True:
        try:
            scanner.run_once()
        except Exception as error:
            scanner.shadow.set_meta("scan", {"status": "error", "finished_ms": int(scanner.clock()),
                                             "error": type(error).__name__})
        time.sleep(interval_seconds)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--interval-seconds", type=int, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.interval_seconds < 30:
        raise SystemExit("interval must be at least 30 seconds")
    if args.once:
        print(_json(V78ShadowScanner(args.runtime).run_once()))
    else:
        run_forever(args.runtime, args.interval_seconds)


if __name__ == "__main__":
    main()
