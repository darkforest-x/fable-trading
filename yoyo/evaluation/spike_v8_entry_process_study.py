"""Frozen same-entry diagnostics for two SPIKE V8 entry-process hypotheses.

The study reads the authenticated V7 cache and the completed V8 serial replay;
it never changes an entry, exit, account, monitor, or order.  Entry labels use
only bars at or before the V8 confirmation close.  Future trade outcomes are
joined afterwards solely to score the already-frozen labels.

H1 labels a V8 confirmation as stale when the exact V6 state-machine shape
evidence bar is at least two bars old and closing prices made no further directional
advance (>0.25 evidence-bar ATR) through that confirmation.  H2 labels an
independent reversal setup: an opposite, high-relative-volume breakout of its
own preceding-12-bar range failed back into that frozen range, then the V8
confirmation closed through its opposite boundary.  H2 is a setup comparison,
not a replacement admission rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import END, SPLIT, START, load_verified_stream, sha256
from yoyo.evaluation.spike_v7_fast import _data_gap, _legacy_v4, _shape, assert_reference_sources_unchanged
from yoyo.evaluation.spike_v8_replay import v8_admissions


EXP = Path("experiments/active/exp-spike-v8-entry-process-20260913-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
H1_MIN_LAG_BARS = 2
H1_MAX_CLOSE_ADVANCE_ATR = 0.25
H2_LOOKBACK_BARS = 12
H2_MIN_AGE_BARS = 2
H2_MIN_RV = 1.5


def _period(stamp: pd.Series) -> pd.Series:
    """Classify a confirmation timestamp under the inherited fixed time split."""
    return pd.Series(np.where(pd.to_datetime(stamp, utc=True) < SPLIT, "development", "validation"), index=stamp.index)


def v6_evidence_anchors(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Reconstruct exact V6 evidence anchors from closed bars only.

    The implementation mirrors ``spike_v7_fast._detect_confirmed_fast`` while
    retaining its otherwise-local ``evidence_i`` at each confirmation.  It uses
    the V6 legacy parent state, current/previous OHLC and causal progressive
    fields; no bar after a returned confirmation is read for that row.
    """
    from yoyo.evaluation.spike_burst_progressive import progressive_fields

    required = {"open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError("missing V6 anchor columns: " + ", ".join(sorted(missing)))
    progress, gap = progressive_fields(bars), _data_gap(bars, minutes)
    out = pd.DataFrame(index=bars.index)
    for side, label in ((1, "long"), (-1, "short")):
        legacy = _legacy_v4(bars, minutes, side)
        rope_name = "ropeHigh" if side == 1 else "ropeLow"
        a = {name: bars[name].to_numpy(float) for name in ("open", "high", "low", "close", "md", "sb", "atr", rope_name)}
        parent_high = legacy.legacy_parent_high.to_numpy(float)
        parent_low = legacy.legacy_parent_low.to_numpy(float)
        legacy_confirmed = legacy.legacy_confirmed.to_numpy(bool)
        advance = progress.prog_advance.to_numpy(float)
        volume = progress.prog_volume_ratio.to_numpy(float)
        ready, gaps = bars.ready.to_numpy(bool), gap.to_numpy(bool)
        confirmed = np.zeros(len(bars), dtype=bool)
        anchors = np.full(len(bars), -1, dtype=int)
        body_i: int | None = None
        frozen_high: float | None = None
        frozen_low: float | None = None
        pending = evidence = False
        evidence_i: int | None = None
        prior_md: float | None = None
        segment_start = 0
        for i in range(len(bars)):
            o, high, low, close, md, sb, atr, rope = (a[k][i] for k in ("open", "high", "low", "close", "md", "sb", "atr", rope_name))
            valid_ohlc = np.isfinite((o, high, low, close)).all() and low > 0 and high >= max(o, close, low) and low <= min(o, close, high)
            valid = not gaps[i] and ready[i] and valid_ohlc and np.isfinite((md, sb, atr, rope)).all() and atr > 0
            if not valid:
                body_i = None; frozen_high = frozen_low = None; pending = evidence = False; evidence_i = None
                segment_start = i + 1; prior_md = float(md) if math.isfinite(md) else None
                continue
            full_body = min(o, close) > rope if side == 1 else max(o, close) < rope
            if side * (close - rope) <= 0:
                body_i = None
            elif full_body:
                body_i = i
            parent_valid = legacy_confirmed[i] and np.isfinite((parent_high[i], parent_low[i])).all() and parent_low[i] <= parent_high[i]
            if parent_valid:
                frozen_high, frozen_low, pending = float(parent_high[i]), float(parent_low[i]), True
                window_start = max(segment_start, i - 2)
                shape_i = next((j for j in range(i, window_start - 1, -1)
                                if _shape(a["open"], a["high"], a["low"], a["close"], j, side)), None)
                envelope = np.isfinite((advance[i], volume[i])).all() and side * advance[i] >= 1.5 and volume[i] >= 1.5
                evidence, evidence_i = bool(envelope and shape_i is not None), shape_i if envelope and shape_i is not None else None
            elif pending and _shape(a["open"], a["high"], a["low"], a["close"], i, side):
                envelope = np.isfinite((advance[i], volume[i])).all() and side * advance[i] >= 1.5 and volume[i] >= 1.5
                if envelope:
                    evidence, evidence_i = True, i
            invalidated = pending and (close < float(frozen_low) if side == 1 else close > float(frozen_high))
            if invalidated:
                frozen_high = frozen_low = None; pending = evidence = False; evidence_i = None
            elif (pending and evidence and body_i is not None and frozen_high is not None and frozen_low is not None
                  and prior_md is not None and side * (close - rope) > 0
                  and (close > frozen_high if side == 1 else close < frozen_low)
                  and side * (md - sb) > 0 and side * (md - prior_md) > 0):
                confirmed[i], anchors[i] = True, int(evidence_i) if evidence_i is not None else -1
                pending = evidence = False; evidence_i = None
            prior_md = float(md)
        out[f"{label}_signal"] = confirmed
        out[f"{label}_evidence_i"] = anchors
    if (out.long_signal & out.short_signal).any():
        raise ValueError("reconstructed V6 anchors are ambiguous")
    return out


def h1_stale_no_progress(bars: pd.DataFrame, *, confirm_i: int, evidence_i: int, side: int) -> dict[str, object]:
    """Return the causal H1 label using evidence-through-confirmation closes."""
    if evidence_i < 0 or evidence_i > confirm_i or side not in (-1, 1):
        return {"h1_anchor_available": False, "h1_stale_no_progress": False, "h1_confirmation_no_progress": False,
                "h1_anchor_i": np.nan, "h1_lag_bars": np.nan, "h1_close_advance_atr": np.nan,
                "h1_confirmation_close_advance_atr": np.nan}
    closes = pd.to_numeric(bars.close.iloc[evidence_i:confirm_i + 1], errors="coerce").to_numpy(float)
    atr = float(bars.atr.iloc[evidence_i])
    if not np.isfinite(closes).all() or not math.isfinite(atr) or atr <= 0:
        return {"h1_anchor_available": False, "h1_stale_no_progress": False, "h1_confirmation_no_progress": False,
                "h1_anchor_i": evidence_i, "h1_lag_bars": confirm_i - evidence_i, "h1_close_advance_atr": np.nan,
                "h1_confirmation_close_advance_atr": np.nan}
    advance = side * ((np.max(closes) if side == 1 else np.min(closes)) - closes[0]) / atr
    confirmation_advance = side * (closes[-1] - closes[0]) / atr
    lag = confirm_i - evidence_i
    return {"h1_anchor_available": True, "h1_stale_no_progress": bool(lag >= H1_MIN_LAG_BARS and advance <= H1_MAX_CLOSE_ADVANCE_ATR),
            "h1_confirmation_no_progress": bool(lag >= H1_MIN_LAG_BARS and confirmation_advance <= H1_MAX_CLOSE_ADVANCE_ATR),
            "h1_anchor_i": evidence_i, "h1_lag_bars": lag, "h1_close_advance_atr": float(advance),
            "h1_confirmation_close_advance_atr": float(confirmation_advance)}


def _valid_h2_window(bars: pd.DataFrame, data_gap: pd.Series | None, *, start: int, end: int, minutes: int) -> bool:
    """Require a contiguous, valid OHLC window before linking an H2 sequence."""
    window = bars.iloc[start:end + 1]
    step = pd.Timedelta(minutes=minutes)
    expected = pd.date_range(window.index[0], window.index[-1], freq=step)
    if not window.index.equals(expected) or not np.isfinite(window[["open", "high", "low", "close"]].to_numpy(float)).all():
        return False
    if (window.high < window[["open", "close", "low"]].max(axis=1)).any() or (window.low > window[["open", "close", "high"]].min(axis=1)).any():
        return False
    return data_gap is None or not pd.Series(data_gap, index=bars.index).iloc[start:end + 1].fillna(True).astype(bool).any()


def h2_failed_break_reversal(bars: pd.DataFrame, *, confirm_i: int, side: int, minutes: int = 60,
                             data_gap: pd.Series | None = None) -> dict[str, object]:
    """Return an H2 label with a pre-breakout frozen range and causal volume.

    The returned breakout is always opposite the final V8 side.  Its 12-bar
    range is measured before the breakout bar; a later close inside that range
    must occur before the final confirmation closes through the other boundary.
    """
    result = {"h2_failed_break_reversal": False, "h2_breakout_i": np.nan, "h2_failback_i": np.nan, "h2_age_bars": np.nan,
              "h2_range_low": np.nan, "h2_range_high": np.nan, "h2_breakout_rv": np.nan,
              "h2_discontinuous_candidate_windows": 0}
    if side not in (-1, 1) or confirm_i < H2_LOOKBACK_BARS + H2_MIN_AGE_BARS:
        return result
    close = pd.to_numeric(bars.close, errors="coerce").to_numpy(float)
    high = pd.to_numeric(bars.high, errors="coerce").to_numpy(float)
    low = pd.to_numeric(bars.low, errors="coerce").to_numpy(float)
    rv = pd.to_numeric(bars.rv, errors="coerce").to_numpy(float)
    if not np.isfinite(close[confirm_i]):
        return result
    opposite = -side
    first = max(H2_LOOKBACK_BARS, confirm_i - H2_LOOKBACK_BARS)
    for q in range(confirm_i - H2_MIN_AGE_BARS, first - 1, -1):
        upper, lower = float(np.max(high[q - H2_LOOKBACK_BARS:q])), float(np.min(low[q - H2_LOOKBACK_BARS:q]))
        breakout = close[q] > upper if opposite == 1 else close[q] < lower
        final_reverse = close[confirm_i] < lower if side == -1 else close[confirm_i] > upper
        inside = (close[q + 1:confirm_i] >= lower) & (close[q + 1:confirm_i] <= upper)
        returned = np.any(inside)
        if np.isfinite((upper, lower, close[q], rv[q])).all() and upper >= lower and breakout and rv[q] >= H2_MIN_RV and returned and final_reverse:
            if not _valid_h2_window(bars, data_gap, start=q - H2_LOOKBACK_BARS, end=confirm_i, minutes=minutes):
                result["h2_discontinuous_candidate_windows"] = int(result["h2_discontinuous_candidate_windows"]) + 1
                continue
            result.update({"h2_failed_break_reversal": True, "h2_breakout_i": q, "h2_failback_i": q + 1 + int(np.flatnonzero(inside)[0]), "h2_age_bars": confirm_i - q,
                           "h2_range_low": lower, "h2_range_high": upper, "h2_breakout_rv": float(rv[q])})
            return result
    return result


def _stream_events(folder: Path, replay_streams: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build V8 rows and attach only their precomputed serial V8 outcomes."""
    context = load_verified_stream(folder)
    anchors = v6_evidence_anchors(context.cache["bars"], context.minutes)
    raw = context.cache["signals"]
    # The frozen cache intentionally zeroes raw V6 admissions outside the
    # evaluation close clock after computing the state machine on its full
    # warm-up prefix.  Apply that same admission boundary before parity.
    in_window = (anchors.index + pd.Timedelta(minutes=context.minutes) >= START) & (anchors.index + pd.Timedelta(minutes=context.minutes) < END)
    anchors.loc[~in_window, ["long_signal", "short_signal"]] = False
    anchors.loc[~in_window, ["long_evidence_i", "short_evidence_i"]] = -1
    if not anchors[["long_signal", "short_signal"]].equals(raw[["long_signal", "short_signal"]]):
        raise ValueError(f"V6 anchor reconstruction parity failed: {context.key}")
    gates = v8_admissions(context)
    local = gates.loc[gates.v8].copy()
    local["signal_bar_open"] = local.index
    local["signal_confirm_time"] = local.index + pd.Timedelta(minutes=context.minutes)
    local = local.loc[(local.signal_confirm_time >= START) & (local.signal_confirm_time < END)].copy()
    rows: list[dict[str, object]] = []
    bars = context.cache["bars"]
    indexer = {stamp: i for i, stamp in enumerate(bars.index)}
    for r in local.itertuples():
        i, side = indexer[r.Index], int(r.side)
        evidence_i = int(anchors.iloc[i]["long_evidence_i" if side == 1 else "short_evidence_i"])
        h1 = h1_stale_no_progress(bars, confirm_i=i, evidence_i=evidence_i, side=side)
        h2 = h2_failed_break_reversal(bars, confirm_i=i, side=side, minutes=context.minutes, data_gap=context.cache["data_gap"])
        rows.append({"stream_key": context.key, **context.identity, "signal_bar_open": r.signal_bar_open,
                     "signal_confirm_time": r.signal_confirm_time, "period": "development" if r.signal_confirm_time < SPLIT else "validation",
                     "side": side, "rope_distance_atr": float(r.rope_distance_atr), "signal_i_local": i,
                     "h1_anchor_time": bars.index[evidence_i] if evidence_i >= 0 else pd.NaT,
                     "h1_confirmation_time": bars.index[i] + pd.Timedelta(minutes=context.minutes),
                     "h2_breakout_time": bars.index[int(h2["h2_breakout_i"])] if bool(h2["h2_failed_break_reversal"]) else pd.NaT,
                     "h2_failback_time": bars.index[int(h2["h2_failback_i"])] if bool(h2["h2_failed_break_reversal"]) else pd.NaT,
                     "h2_confirmation_time": bars.index[i] + pd.Timedelta(minutes=context.minutes), **h1, **h2})
    events = pd.DataFrame(rows)
    controls_path = replay_streams / f"{context.key}.controls.csv.gz"
    controls = pd.read_csv(controls_path).loc[lambda x: x.arm.eq("v8")].copy()
    if len(controls):
        controls["signal_bar_open"] = pd.to_datetime(controls.signal_bar_open, utc=True)
    else:
        controls = pd.DataFrame(columns=["signal_bar_open", "side", "arm", "period", "stream_key"])
    controls["stream_key"] = context.key
    if events.empty:
        return events, controls
    trade_path = replay_streams / f"{context.key}.trades.csv.gz"
    if not trade_path.is_file():
        raise FileNotFoundError(f"missing completed V8 replay trades: {trade_path}")
    trades = pd.read_csv(trade_path)
    trades = trades.loc[trades.arm.eq("v8")].copy()
    trades["signal_bar_open"] = pd.to_datetime(trades.signal_bar_open, utc=True)
    if trades.duplicated(["signal_bar_open", "side"]).any():
        raise ValueError(f"ambiguous V8 same-entry outcomes: {context.key}")
    keep = ["signal_bar_open", "side", "trade_id", "entry_time", "exit_time", "exit_reason", "net_r", "mfe_r", "net_return", "gross_r", "gross_return", "censored", "holding_bars"]
    events = events.merge(trades.reindex(columns=keep), on=["signal_bar_open", "side"], how="left", validate="one_to_one")
    events["executed"] = events.trade_id.notna()
    events["closed"] = events.executed & ~events.censored.eq(True)
    events["development_cross_split_purged"] = False
    development = events.period.eq("development") & events.closed
    exits = pd.to_datetime(events.exit_time, utc=True, errors="coerce")
    events.loc[development & exits.ge(SPLIT), "development_cross_split_purged"] = True
    events["scoring_closed"] = events.closed & ~events.development_cross_split_purged
    return events, controls


def _metrics(frame: pd.DataFrame) -> dict[str, object]:
    closed = frame.loc[frame.scoring_closed].copy()
    values = pd.to_numeric(closed.net_r, errors="coerce").dropna()
    gains, losses = values.clip(lower=0).sum(), -values.clip(upper=0).sum()
    return {"signals": len(frame), "executed": int(frame.executed.sum()), "closed": len(values), "censored": int(frame.executed.sum()) - int(frame.closed.sum()),
            "development_cross_split_purged": int(frame.development_cross_split_purged.sum()),
            "wins": int((values > 0).sum()), "losses": int((values < 0).sum()), "win_rate": float((values > 0).mean()) if len(values) else math.nan,
            "net_r_sum": float(values.sum()), "average_net_r": float(values.mean()) if len(values) else math.nan,
            "profit_factor": float(gains / losses) if losses > 0 else math.nan,
            "realized_10r": int((values >= 10).sum()), "mfe_10r": int((pd.to_numeric(closed.mfe_r, errors="coerce") >= 10).sum()),
            "h2_discontinuous_candidate_windows": int(pd.to_numeric(frame.h2_discontinuous_candidate_windows, errors="coerce").fillna(0).sum())}


def summary_table(events: pd.DataFrame) -> pd.DataFrame:
    """Report baseline, H1 retained/removed, and H2 setup/non-setup separately."""
    arms = {"baseline_v8": pd.Series(True, index=events.index), "h1_max_retained": ~events.h1_stale_no_progress,
            "h1_max_removed": events.h1_stale_no_progress,
            "h1_confirm_retained": ~events.h1_confirmation_no_progress,
            "h1_confirm_removed": events.h1_confirmation_no_progress, "h2_setup": events.h2_failed_break_reversal,
            "h2_non_setup": ~events.h2_failed_break_reversal}
    rows = []
    for name, mask in arms.items():
        for keys, part in events.loc[mask].groupby(["period", "timeframe_min", "side"], dropna=False):
            rows.append({"cohort": name, "period": keys[0], "timeframe_min": keys[1], "side": keys[2], **_metrics(part)})
        rows.append({"cohort": name, "period": "all", "timeframe_min": "all", "side": "all", **_metrics(events.loc[mask])})
    return pd.DataFrame(rows)


def month_table(events: pd.DataFrame) -> pd.DataFrame:
    """Expose per-month stability for each independently defined label."""
    source = events.copy()
    source["month"] = pd.to_datetime(source.signal_confirm_time, utc=True).dt.strftime("%Y-%m")
    rows = []
    for name, mask in {"h1_max_removed": source.h1_stale_no_progress,
                       "h1_confirm_removed": source.h1_confirmation_no_progress,
                       "h2_setup": source.h2_failed_break_reversal}.items():
        for keys, part in source.loc[mask].groupby(["month", "period", "timeframe_min", "side"], dropna=False):
            rows.append({"cohort": name, "month": keys[0], "period": keys[1], "timeframe_min": keys[2], "side": keys[3], **_metrics(part)})
    return pd.DataFrame(rows)


def matched_pairs(events: pd.DataFrame, *, flag: str, cohort: str) -> pd.DataFrame:
    """Pair flagged and unflagged closed entries in the same stream/period/month/side.

    This is a deterministic near-time descriptive comparator, not the existing
    matched-random-market control and not a volatility-bucket match.
    """
    source = events.loc[events.scoring_closed].copy()
    source["month"] = pd.to_datetime(source.signal_confirm_time, utc=True).dt.strftime("%Y-%m")
    flagged = source.loc[source[flag]].copy()
    controls = source.loc[~source[flag]].copy()
    rows = []
    for keys, target in flagged.groupby(["stream_key", "period", "month", "side"], sort=True):
        pool = controls.loc[(controls.stream_key == keys[0]) & (controls.period == keys[1]) & (controls.month == keys[2]) & (controls.side == keys[3])].sort_values("signal_confirm_time")
        unused = set(pool.index)
        for t in target.sort_values("signal_confirm_time").itertuples():
            if not unused:
                rows.append({"cohort": cohort, "matched": False, "stream_key": keys[0], "period": keys[1], "month": keys[2], "side": keys[3], "target_trade_id": t.trade_id})
                continue
            pick = min(unused, key=lambda j: (abs(pd.Timestamp(pool.loc[j, "signal_confirm_time"]) - pd.Timestamp(t.signal_confirm_time)), str(pool.loc[j, "trade_id"])))
            unused.remove(pick); c = pool.loc[pick]
            rows.append({"cohort": cohort, "matched": True, "stream_key": keys[0], "period": keys[1], "month": keys[2], "side": keys[3],
                         "target_trade_id": t.trade_id, "control_trade_id": c.trade_id, "target_net_r": t.net_r,
                         "control_net_r": c.net_r, "net_r_difference": float(t.net_r) - float(c.net_r)})
    return pd.DataFrame(rows)


def _repo_relative(path: Path | str) -> str:
    """Return a repository-relative git path, including for ``__file__``."""
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True,
                               text=True).stdout.strip()).resolve()
    return str(Path(path).resolve().relative_to(root))


def _committed(paths: Iterable[Path]) -> bool:
    """Require immutable source files before official experiment output is written."""
    for path in paths:
        try:
            rel = _repo_relative(path)
        except (ValueError, subprocess.CalledProcessError):
            return False
        tracked = subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel}"], check=False, capture_output=True).returncode == 0
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], check=False).returncode == 0
        if not tracked or not clean:
            return False
    return True


def run(output: Path, *, official: bool = False, limit: int | None = None) -> None:
    """Materialize immutable evidence; official outputs require committed sources."""
    config = json.loads(CONFIG.read_text())
    replay = Path(str(config["v8_replay"]))
    if sha256(replay / "manifest.json") != str(config["v8_replay_manifest_sha256"]):
        raise ValueError("completed V8 replay manifest changed")
    if int(json.loads((replay / "manifest.json").read_text())["streams"]) != int(config["expected_streams"]):
        raise ValueError("unexpected V8 replay stream count")
    if official:
        if not _committed((Path(__file__), CONFIG, PLAN)):
            raise ValueError("official output requires the exact study source/config/plan committed to HEAD")
        if EXP not in output.parents:
            raise ValueError("official output must remain under this experiment directory")
    elif EXP in output.parents:
        raise ValueError("experiment-directory output is reserved for a committed official run; use /tmp for smoke")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output exists; immutable evidence requires a new directory")
    assert_reference_sources_unchanged()
    folders = sorted(p for p in Path(str(config["raw"])).joinpath("streams").iterdir() if (p / "completion.json").exists())
    if len(folders) != int(config["expected_streams"]):
        raise ValueError("unexpected authenticated cache stream count")
    if limit is not None:
        folders = folders[:limit]
    pieces, control_pieces = [], []
    for number, folder in enumerate(folders, 1):
        event_piece, control_piece = _stream_events(folder, replay / "streams")
        pieces.append(event_piece)
        if len(control_piece):
            control_pieces.append(control_piece)
        if number % 250 == 0 or number == len(folders):
            print(json.dumps({"streams": number, "scope": len(folders)}), flush=True)
    events = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if limit is None and len(events) != int(config["expected_v8_signals"]):
        raise ValueError(f"expected {config['expected_v8_signals']} V8 signals, found {len(events)}")
    output.mkdir(parents=True, exist_ok=False)
    events.to_csv(output / "same_entry_evidence.csv.gz", index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0})
    # Materialize development scoring before validation scoring.  The thresholds
    # were committed in config beforehand; validation is descriptive reuse only.
    summary_table(events.loc[events.period.eq("development")]).to_csv(output / "development_summary.csv", index=False)
    summary_table(events.loc[events.period.eq("validation")]).to_csv(output / "validation_summary.csv", index=False)
    summary_table(events).to_csv(output / "summary.csv", index=False)
    month_table(events).to_csv(output / "monthly.csv", index=False)
    pairs = pd.concat([matched_pairs(events, flag="h1_stale_no_progress", cohort="h1_max_stale_vs_fresh"),
                       matched_pairs(events, flag="h1_confirmation_no_progress", cohort="h1_confirm_stale_vs_fresh"),
                       matched_pairs(events, flag="h2_failed_break_reversal", cohort="h2_setup_vs_non_setup")], ignore_index=True)
    pairs.to_csv(output / "same_stream_month_pairs.csv", index=False)
    controls = pd.concat(control_pieces, ignore_index=True) if control_pieces else pd.DataFrame()
    if len(controls):
        labels = events[["stream_key", "signal_bar_open", "side", "period", "h1_stale_no_progress", "h1_confirmation_no_progress", "h2_failed_break_reversal", "development_cross_split_purged"]].copy()
        controls = controls.merge(labels, on=["stream_key", "signal_bar_open", "side", "period"], how="left", validate="one_to_one")
        controls["reusable_for_development_selection"] = False
        controls["reuse_limitation"] = "existing random-control artifact lacks the control exit timestamp; retained for descriptive context only, never development selection"
    controls.to_csv(output / "existing_random_controls.csv", index=False)
    manifest = {"complete": limit is None, "official": official, "streams": len(folders), "signals": len(events),
                "execution_contract": config["execution_contract"], "h1": config["h1"], "h2": config["h2"],
                "source": {str(p): sha256(p) for p in (Path(__file__), CONFIG, PLAN, replay / "manifest.json")}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, official=args.official, limit=args.limit)
