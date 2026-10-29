"""Map already-saved strategy fills to their booklet account paths.

This module reuses the original allocators and trace format from
``strategy_booklet_paths``. It does not replay bars, generate fills, or write
artifacts. Input rows must already represent the candidate fills for one
strategy, before capital allocation.
"""
from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_integer_dtype

from yoyo.evaluation import monday_limit_return_opt as capital
from yoyo.evaluation.monday_limit_langlang import path_run
from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
from yoyo.evaluation import strategy_booklet_paths as source


TRACKS = ("60", "5", "v3", "v3lock", "v3add", "v3add20", "allin3")
_OUT = source.OUT


def _read_manifest() -> dict[str, Any]:
    return json.loads((_OUT / "manifest.json").read_text())


def _to_ns(values: pd.Series, column: str) -> pd.Series:
    """Return UTC epoch nanoseconds without routing integer epochs through float."""
    if values.isna().any():
        raise ValueError(f"{column} contains missing timestamps")
    if is_datetime64_any_dtype(values.dtype):
        if getattr(values.dt, "tz", None) is None:
            values = values.dt.tz_localize("UTC")
        else:
            values = values.dt.tz_convert("UTC")
        # Datetime units vary by pandas version/input (s, ms, us, ns).
        # Force the storage unit before viewing the integer representation;
        # Series.astype works in both the bundled old and current pandas.
        values = values.astype("datetime64[ns, UTC]")
        return values.astype("int64")
    if is_integer_dtype(values.dtype):
        return values.astype("int64")
    if pd.api.types.is_numeric_dtype(values.dtype):
        raise TypeError(f"{column} must be integer epoch nanoseconds, not floating point")
    # Numeric strings are valid exact nanosecond values; otherwise parse dates
    # explicitly as UTC.
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        if not np.equal(numeric.to_numpy(), np.floor(numeric.to_numpy())).all():
            raise TypeError(f"{column} contains non-integer epoch nanoseconds")
        return numeric.astype("int64")
    utc = pd.to_datetime(values, utc=True, errors="raise")
    utc = utc.astype("datetime64[ns, UTC]")
    return utc.astype("int64")


def _with_ns(rows: pd.DataFrame) -> pd.DataFrame:
    """Copy rows and ensure entry_ns/exit_ns plus allocator-friendly aliases."""
    frame = rows.copy()
    for prefix in ("entry", "exit"):
        ns_col = f"{prefix}_ns"
        if ns_col in frame:
            frame[ns_col] = _to_ns(frame[ns_col], ns_col)
        else:
            choices = (f"{prefix}_t", prefix)
            found = next((col for col in choices if col in frame), None)
            if found is None:
                raise KeyError(f"rows require {ns_col} or one of {choices}")
            frame[ns_col] = _to_ns(frame[found], found)
    # replay_w expects these epoch-nanosecond field names. Canonicalize aliases
    # too, so datetime-looking convenience columns cannot alter event timing.
    for prefix in ("entry", "exit"):
        frame[prefix] = frame[f"{prefix}_ns"]
    return frame


def _start_filter(track: str, rows: pd.DataFrame) -> pd.DataFrame:
    start = _read_manifest()["accounts"][track]["start"]
    start_ns = pd.Timestamp(start, tz="UTC").value
    frame = _with_ns(rows)
    return frame.loc[frame.entry_ns >= start_ns].copy()


def load_baselines() -> dict[str, pd.DataFrame]:
    """Load the seven saved baseline fill sets in their original row order.

    The committed fill projections are read directly from the existing cache
    files. No fallback builder is called, so this function never regenerates
    cached inputs as a side effect.
    """
    native_path = _OUT / "native-fills.csv.gz"
    five_path = _OUT / "five-minute-fills.csv.gz"
    if not native_path.exists() or not five_path.exists():
        raise FileNotFoundError(
            "Saved booklet projections are required: native-fills.csv.gz and "
            "five-minute-fills.csv.gz"
        )

    native = pd.read_csv(native_path)
    # Keep the exact integer ns values stored in the projection. Parsing the
    # convenience timestamp strings and recasting them can change their unit
    # on newer pandas versions.
    native["entry_ns"] = _to_ns(native["entry_ns"], "entry_ns")
    native["exit_ns"] = _to_ns(native["exit_ns"], "exit_ns")
    five = pd.read_csv(five_path)
    # The 5m projection stores its exact UTC epoch-ns values in entry/exit.
    five["entry_ns"] = _to_ns(five["entry"], "entry")
    five["exit_ns"] = _to_ns(five["exit"], "exit")

    # Preserve source order here. The original allocators use seeded random
    # tie ordering, so source row order is part of the replay contract.
    return {
        "60": native.loc[native.variant == "v2_be17"].copy(),
        "5": five.copy(),
        "v3": native.loc[native.variant == "v3_be17"].copy(),
        "v3lock": native.loc[native.variant == "v3_be50"].copy(),
        "v3add": native.loc[native.variant.isin(["v3_be0", "v3_add5"])].copy(),
        "v3add20": native.loc[native.variant.isin(["v3_be0", "v3_add5"])].copy(),
        "allin3": native.loc[(native.variant == "opp1618_P30") & (native.score >= 0.6)].copy(),
    }


def _linear_fraction(rows: pd.DataFrame) -> np.ndarray:
    cutoff = source.read(source.RUN.name + "/scores.json")["threshold"]
    mean_excess = source.read(
        "exp-monday-limit-v3-compare-20261025-v1/summary.json"
    )["results"]["linear_mean_excess"]
    return np.minimum(0.05 * (rows.score.to_numpy() - cutoff) / mean_excess, 0.5)


def _trace_key(row: pd.Series) -> str:
    # Match strategy_booklet_paths.Trace exactly.
    event = row.get("event", row.get("trade_id", row.get("tid", row.name)))
    return str(event)


def _bool_values(values: pd.Series) -> pd.Series:
    if is_bool_dtype(values.dtype):
        return values.fillna(False)
    if pd.api.types.is_numeric_dtype(values.dtype):
        return values.fillna(0).ne(0)
    return values.fillna("").astype(str).str.strip().str.lower().isin(
        {"1", "true", "yes", "y", "censored"}
    )


def _censored_pnl(rows: pd.DataFrame, ledger: pd.DataFrame) -> float | None:
    flag = next((c for c in ("censored", "is_censored", "right_censored") if c in rows), None)
    if flag is None:
        return None

    if not any(c in rows for c in ("event", "trade_id", "tid")):
        return None
    frame = rows.copy()
    frame["_trace_key"] = [_trace_key(row) for _, row in frame.iterrows()]
    frame["_censored"] = _bool_values(frame[flag]).to_numpy()
    # Trace currently keys records by event, so mixed censor labels for the
    # same event cannot be separated safely after allocation.
    states_per_key = frame.groupby("_trace_key", dropna=False)["_censored"].nunique()
    if states_per_key.gt(1).any():
        return None
    keys = set(frame.loc[frame._censored, "_trace_key"])
    if not keys:
        return 0.0
    funded = ledger.notional.gt(0) & ledger.trade_id.isin(keys)
    return float(ledger.loc[funded, "pnl"].sum())


def account(track: str, rows: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    """Replay existing fills through the original capital allocator.

    ``rows`` must already be filtered/projected to the chosen strategy's fill
    set and retain its original source order. Timestamps are UTC epoch ns in
    ``entry_ns``/``exit_ns`` (or parseable ``entry_t``/``exit_t``); all rows
    require ``net`` in basis points. The v3 family and allin3 also require
    ``score`` and ``mae_pct``. The allocator's trade key is taken from
    ``event``, then ``trade_id``, then ``tid``, matching the original Trace.
    """
    if track not in TRACKS:
        raise KeyError(f"Unknown track {track!r}; expected one of {TRACKS}")
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a pandas DataFrame")

    manifest = _read_manifest()
    meta = manifest["accounts"][track]
    frame = _start_filter(track, rows)
    if frame.empty:
        raise ValueError(f"No saved fills for {track} after {meta['start']}")
    if "net" not in frame:
        raise KeyError("rows require net (net return in basis points)")

    # Match the original exporter's one explicit entry sort for the 5m track.
    if track == "5":
        frame = frame.sort_values("entry").reset_index(drop=True)

    recorder = source.Trace(pd.Timestamp(meta["start"], tz="UTC").value)
    if track in ("60", "5"):
        result, _ = replay_w(
            frame,
            np.full(len(frame), 0.1),
            1.0,
            trace=recorder,
        )
    elif track in ("v3", "v3lock", "v3add"):
        result = capital.linear(
            frame,
            _linear_fraction(frame),
            1,
            int(meta["seed"]),
            trace=recorder,
        )
    elif track == "v3add20":
        result = capital.frac(frame, 20, 3, int(meta["seed"]), trace=recorder)
    else:
        result = path_run(frame, "allin", 3, int(meta["seed"]), trace=recorder)

    ledger = pd.DataFrame(recorder.rows)
    if not ledger.time_ms.is_monotonic_increasing:
        raise ValueError(f"Unordered events: {track}")

    event_rows = ledger.loc[ledger.notional.gt(0)]
    positive = event_rows.loc[event_rows.pnl.gt(0), "pnl"]
    negative = event_rows.loc[event_rows.pnl.lt(0), "pnl"]
    initial_equity = float(ledger.equity.iloc[0])
    ending_equity = float(ledger.equity.iloc[-1])
    cashflow = float(ledger.cashflow.sum())
    peak = ledger.equity.cummax()
    drawdown = (1.0 - ledger.equity / peak).max()
    gross_profit = float(positive.sum())
    gross_loss = float(-negative.sum())
    censored_pnl = _censored_pnl(frame, ledger)
    metrics: dict[str, Any] = {
        "track": track,
        "start": meta["start"],
        "seed": meta["seed"],
        "initial_equity": initial_equity,
        "ending_equity": ending_equity,
        "net_trading_pnl": float(ledger.pnl.sum()),
        "cashflow": cashflow,
        "external_refill_cash": cashflow,
        "return_pct": None if cashflow != 0.0 else 100.0 * (ending_equity / initial_equity - 1.0),
        "max_realized_dd_pct": float(100.0 * drawdown),
        "max_drawdown_pct": float(100.0 * drawdown),
        "funded": int(ledger.notional.gt(0).sum()),
        "wins": int(event_rows.pnl.gt(0).sum()),
        "win_pct": float(100.0 * event_rows.pnl.gt(0).mean()) if len(event_rows) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "censored_pnl": censored_pnl,
        "allocator_result": result,
    }
    return metrics, ledger


def _csv_round_trip(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the saved CSV type and empty-value conventions before comparison."""
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    return pd.read_csv(buffer)


def validate_baselines() -> dict[str, dict[str, Any]]:
    """Assert each rebuilt account ledger equals its saved CSV row by row."""
    baselines = load_baselines()
    results: dict[str, dict[str, Any]] = {}
    for track in TRACKS:
        metrics, ledger = account(track, baselines[track])
        saved = pd.read_csv(_OUT / f"{track}-ledger.csv.gz")
        actual_csv = _csv_round_trip(ledger)
        saved_csv = _csv_round_trip(saved)
        try:
            pd.testing.assert_frame_equal(
                actual_csv.reset_index(drop=True),
                saved_csv.reset_index(drop=True),
                check_dtype=True,
                check_exact=False,
                check_like=False,
                rtol=0.0,
                atol=1e-12,
            )
        except AssertionError as exc:
            raise AssertionError(f"{track}: regenerated ledger differs from saved ledger") from exc
        print(json.dumps({
            "track": track,
            "row_by_row_match": True,
            "rows": len(ledger),
        }))
        results[track] = metrics
    return results

