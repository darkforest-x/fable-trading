"""One-variable ETH 3m V8 exit replay: 1R wick touch arms next-bar price BE.

The frozen low-timeframe V8 admission, raw opposite-event feed, next-open
entry, 2 ATR / five-bar initial stop, 2R / 4 ATR trail and 0.2% round-trip
cost are unchanged.  A favorable *high/low* touch of +1 initial R is observed
only after a bar closes; it can raise protection to entry for the next bar.
No within-bar ordering is inferred.  Consequently an old protection is always
tested before the new BE level, and a price-BE exit still includes the cost.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _close_trade, _data_gap, _gap_censor
from yoyo.evaluation.spike_v7_fast import _initial_position_fast, simulate_v6_variant
from yoyo.evaluation.spike_v8_lowtf_study import (
    HOLDOUT_START, ROUND_TRIP_COST, _finished, _fold_mask, metrics, read_ohlcv,
    stream_account_stats, v8_mask,
)

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-v8-eth3m-be-20260913-v1"
CONFIG = EXPERIMENT / "config.json"
FROZEN = ROOT / "experiments/active/exp-spike-v8-lowtf-20260913-v1/results/run_20260913_v3"
ETH_PATH = ROOT / "data/kline_deep/okx_ETH_USDT_SWAP_3m_525599.csv"
TICK = .01
MINUTES = 3
DEVELOPMENT_START = pd.Timestamp("2023-07-31T11:12:00Z")
END = pd.Timestamp("2026-07-30T11:09:00Z")
TRADE_KEY = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
FROZEN_BUILDER_SHA = "408bf101f8a77ad9eb496311eee99ea02330c73c90d2d944edb8034b128232cb"
FROZEN_CLOSED_SHA = "2606f302dad35626251090ee2be6f1688c34499b84b0fd562077dc3f9469753f"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stop_hit(side: int, high: float, low: float, protection: float) -> bool:
    return low <= protection if side == 1 else high >= protection


def _gap_stop(side: int, opening: float, protection: float) -> bool:
    return opening <= protection if side == 1 else opening >= protection


def _be_touched(side: int, entry: float, risk: float, high: float, low: float) -> bool:
    """Causal wick criterion; the caller applies it only at this bar's close."""
    return high >= entry + risk if side == 1 else low <= entry - risk


def _entry_retest_same_bar(side: int, entry: float, high: float, low: float) -> bool:
    return low <= entry if side == 1 else high >= entry


@dataclass(frozen=True)
class FixedPrepared:
    """Arrays shared by all fixed-entry paths in one fold, not a data change."""
    index: pd.DatetimeIndex
    gap: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    raw_side: np.ndarray


def prepare_fixed(frame: pd.DataFrame, signals: pd.DataFrame) -> FixedPrepared:
    """Precompute immutable per-bar inputs once; each paired path then scans its life only."""
    if not frame.index.equals(signals.index):
        raise ValueError("fixed replay needs aligned bars and raw signals")
    long, short = signals.long_signal.fillna(False).to_numpy(bool), signals.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("ambiguous raw side")
    return FixedPrepared(frame.index, _data_gap(frame, MINUTES).to_numpy(bool),
                         *(frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")),
                         np.where(long, 1, np.where(short, -1, 0)))


def _apply_close_updates(position: dict[str, object], *, side: int, high: float, low: float,
                         close: float, atr: float, spec: ExecutionSpec, ambiguity: list[int]) -> None:
    """Register the close-confirmed BE and preserve the frozen trailing rule."""
    entry, risk = float(position["entry_price"]), float(position["initial_risk"])
    if not bool(position.get("be_armed", False)) and _be_touched(side, entry, risk, high, low):
        position["be_armed"] = True
        position["be_trigger_count"] = int(position.get("be_trigger_count", 0)) + 1
        if _entry_retest_same_bar(side, entry, high, low):
            ambiguity[0] += 1
    if bool(position.get("be_armed", False)):
        before = float(position["protection"])
        position["protection"] = max(before, entry) if side == 1 else min(before, entry)
    favorable = high if side == 1 else low
    position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - entry) / risk)
    current_r = side * (close - entry) / risk
    position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
    if bool(position["trail_armed"]) and math.isfinite(float(atr)) and atr > 0:
        raw = close - side * spec.trail_atr * atr
        candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
        before = float(position["protection"])
        position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)


def replay_serial(frame: pd.DataFrame, signals: pd.DataFrame, admission: pd.Series, *, tick: float = TICK,
                  enable_be: bool = True, data_gap: pd.Series | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Full one-stream replay; raw opposite signals remain exits even when rejected for entry."""
    required = {"open", "high", "low", "close", "atr"}
    if not required.issubset(frame) or not signals.index.equals(frame.index):
        raise ValueError("aligned OHLC/ATR bars and signals are required")
    minutes = frame.attrs.get("minutes")
    if minutes != MINUTES:
        raise ValueError("this study is restricted to ETH 3m")
    spec = ExecutionSpec(tick=tick)
    gap_a = (pd.Series(False, index=frame.index) if data_gap is None else pd.Series(data_gap, index=frame.index).fillna(True)).to_numpy(bool)
    allowed = pd.Series(admission, index=frame.index).fillna(False).to_numpy(bool)
    long_a, short_a = signals.long_signal.fillna(False).to_numpy(bool), signals.short_signal.fillna(False).to_numpy(bool)
    if (long_a & short_a).any():
        raise ValueError("ambiguous raw side")
    raw_side = np.where(long_a, 1, np.where(short_a, -1, 0))
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")}
    oa, ha, la, ca, aa = (arrays[name] for name in ("open", "high", "low", "close", "atr"))
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None
    trades: list[dict[str, object]] = []
    ambiguity = [0]
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if gap_a[i]:
            if position is not None:
                trades.append(_gap_censor(position, exit_i=i, exit_time=stamp))
            position = None; pending_entry = pending_reverse = None
            continue
        # This intentionally matches the frozen engine: a raw reverse is a
        # next-open close, with an already-effective stop winning at that open.
        if pending_reverse is not None and position is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                opening, protection = float(oa[i]), float(position["protection"])
                hit = _gap_stop(old_side, opening, protection)
                reason = (("trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap")
                          if hit else "opposite_v6_next_open")
                trades.append(_close_trade(position, exit_i=i, exit_time=stamp, exit_price=opening, reason=reason, spec=spec))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                position = _initial_position_fast(frame.index, oa, ha, la, ca, aa, gap_a, signal_i, side, spec)
                if position is not None:
                    position["be_armed"] = False; position["be_trigger_count"] = 0
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            opening, high, low, close, atr = oa[i], ha[i], la[i], ca[i], aa[i]
            if _stop_hit(side, high, low, protection):
                price = min(opening, protection) if side == 1 else max(opening, protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if _gap_stop(side, opening, protection):
                    reason += "_gap"
                trades.append(_close_trade(position, exit_i=i, exit_time=stamp, exit_price=float(price), reason=reason, spec=spec))
                ended_side, position = side, None
            else:
                if enable_be:
                    _apply_close_updates(position, side=side, high=high, low=low, close=close, atr=atr, spec=spec, ambiguity=ambiguity)
                else:
                    # Baseline branch is deliberately equivalent to frozen V8.
                    favorable = high if side == 1 else low
                    position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - float(position["entry_price"])) / float(position["initial_risk"]))
                    current_r = side * (close - float(position["entry_price"])) / float(position["initial_risk"])
                    position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
                    if bool(position["trail_armed"]) and math.isfinite(float(atr)) and atr > 0:
                        raw = close - side * spec.trail_atr * atr
                        candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
                        position["protection"] = max(protection, candidate) if side == 1 else min(protection, candidate)
        side = int(raw_side[i])
        if side:
            if position is not None and side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if allowed[i]: pending_entry = (i, side)
            elif position is None and side != ended_side and allowed[i]:
                pending_entry = (i, side)
    if position is not None:
        last = len(frame) - 1
        trade = _close_trade(position, exit_i=last, exit_time=frame.index[last], exit_price=float(ca[last]), reason="boundary_mark", spec=spec)
        trade["censored"] = True; trade["exit_time_precision"] = "last_complete_close"; trades.append(trade)
    out = pd.DataFrame(trades)
    if len(out):
        equity, before, after = 1.0, [], []
        for trade in out.itertuples():
            before.append(equity)
            if not bool(trade.censored): equity *= 1.0 + float(trade.net_return)
            after.append(equity)
        out["account_equity_before"], out["account_equity_after"] = before, after
    return out, {"same_bar_1r_entry_retest_ambiguous": ambiguity[0]}


def replay_fixed_entry(frame: pd.DataFrame, signals: pd.DataFrame, row: pd.Series, *, tick: float = TICK,
                       prepared: FixedPrepared | None = None) -> dict[str, object]:
    """Re-evaluate one frozen entry without permitting a policy-induced re-entry."""
    spec, start = ExecutionSpec(tick=tick), int(row.entry_i)
    side = int(row.side)
    pos = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    pos.update(protection=float(row.initial_stop), mfe_r=0.0, trail_armed=False, be_armed=False, be_trigger_count=0)
    prepared = prepare_fixed(frame, signals) if prepared is None else prepared
    if not prepared.index.equals(frame.index):
        raise ValueError("prepared fixed inputs belong to another frame")
    gap, oa, ha, la, ca, aa, raw_side = (prepared.gap, prepared.open, prepared.high, prepared.low,
                                          prepared.close, prepared.atr, prepared.raw_side)
    ambiguity = [0]; pending_reverse = False
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if gap[i]: return _gap_censor(pos, exit_i=i, exit_time=stamp)
        protection = float(pos["protection"])
        if pending_reverse:
            hit = _gap_stop(side, oa[i], protection)
            reason = (("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap") if hit else "opposite_v6_next_open")
            return _close_trade(pos, exit_i=i, exit_time=stamp, exit_price=float(oa[i]), reason=reason, spec=spec)
        if _stop_hit(side, ha[i], la[i], protection):
            price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if _gap_stop(side, oa[i], protection): reason += "_gap"
            return _close_trade(pos, exit_i=i, exit_time=stamp, exit_price=float(price), reason=reason, spec=spec)
        _apply_close_updates(pos, side=side, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, ambiguity=ambiguity)
        pending_reverse = int(raw_side[i]) == -side
    trade = _close_trade(pos, exit_i=len(frame)-1, exit_time=frame.index[-1], exit_price=float(ca[-1]), reason="boundary_mark", spec=spec)
    trade["censored"] = True; trade["exit_time_precision"] = "last_complete_close"
    return trade


def _decorate(trades: pd.DataFrame, *, fold: str, arm: str) -> pd.DataFrame:
    out = trades.loc[~trades.censored.astype(bool)].copy() if len(trades) else trades.copy()
    if len(out):
        out["symbol"], out["venue"], out["minutes"], out["arm"], out["fold"] = "ETH-USDT-SWAP", "okx", MINUTES, arm, fold
        out["signal_confirm_time"] = pd.to_datetime(out.signal_bar_open, utc=True) + pd.Timedelta(minutes=MINUTES)
    return out.reset_index(drop=True)


def _by_side(trades: pd.DataFrame, *, view: str, fold: str, arm: str) -> list[dict[str, object]]:
    accounts = stream_account_stats(trades)
    rows = []
    for label, group in [("both", trades), ("long", trades.loc[trades.side.eq(1)]), ("short", trades.loc[trades.side.eq(-1)])]:
        m = metrics(pd.DataFrame(), group, accounts if label == "both" else pd.DataFrame(), label=f"{view}:{fold}:{arm}:{label}")
        rows.append({"view": view, "fold": fold, "arm": arm, "side": label, **m})
    return rows


def _signflip(pairs: pd.DataFrame, *, draws: int = 20000) -> dict[str, object]:
    data = pairs.copy(); data["entry_time"] = pd.to_datetime(data.entry_time_baseline, utc=True)
    blocks = data.groupby(data.entry_time.dt.strftime("%Y-%m")).net_r_difference.mean().to_numpy(float)
    observed = float(blocks.mean()) if len(blocks) else math.nan
    rng = np.random.default_rng(20260913)
    null = (rng.choice([-1.0, 1.0], size=(draws, len(blocks))) * blocks).mean(axis=1) if len(blocks) else np.array([])
    exceed = int((np.abs(null) >= abs(observed)).sum()) if len(null) else 0
    return {"blocks": int(len(blocks)), "observed_mean_net_r_difference": observed, "draws": draws,
            "exceedances": exceed, "p_two_sided": (exceed + 1) / (draws + 1) if len(blocks) else math.nan,
            "p_formula": "(exceedances + 1) / (draws + 1)", "limitation": "three UTC-month blocks; descriptive only"}


def _be_stop_masks(post: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Separate an entry-protection BE stop from a better original trail fill."""
    at_entry = post.protection_be.sub(post.entry_price_be).abs().le(1e-12)
    # ``be_armed`` only exists on the BE input to the paired merge, hence it
    # intentionally remains unsuffixed.
    be_stop = post.be_armed.astype(bool) & post.exit_reason_be.str.contains("trailing_stop", na=False) & at_entry
    price_fill = be_stop & post.exit_price_be.sub(post.entry_price_be).abs().le(1e-12)
    return be_stop, price_fill


def _paired_outcome_summary(post: pd.DataFrame) -> dict[str, object]:
    """Aggregate material paired changes while preserving raw CSV deltas."""
    tolerance = 1e-8
    delta = post.net_r_difference.astype(float)
    improved, worsened = delta.gt(tolerance), delta.lt(-tolerance)
    unchanged = ~(improved | worsened)
    be_stop, price_be_fill = _be_stop_masks(post)
    be_stop_losses = post.loc[be_stop & post.net_r_be.lt(0), "net_r_be"]
    return {"paired_delta_tolerance_net_r": tolerance, "unchanged_pairs": int(unchanged.sum()),
            "improved_pairs": int(improved.sum()), "worsened_pairs": int(worsened.sum()),
            "positive_delta_net_r_sum": float(delta.loc[improved].sum()), "negative_delta_net_r_sum": float(delta.loc[worsened].sum()),
            "one_r_triggered_fixed": int(post.be_trigger_count.gt(0).sum()),
            "be_protection_stop_fixed": int(be_stop.sum()), "price_be_fill_fixed": int(price_be_fill.sum()),
            "be_gap_stop_fixed": int((be_stop & post.exit_reason_be.str.endswith("_gap", na=False)).sum()),
            "be_stop_loss_median_net_r": float(be_stop_losses.median()) if len(be_stop_losses) else math.nan,
            "rescued_original_losers": int((post.net_r_baseline.lt(0) & improved).sum()),
            "lost_original_winners": int((post.net_r_baseline.gt(0) & post.net_r_be.le(0)).sum()),
            "lost_original_10r": int((post.net_r_baseline.ge(10) & post.net_r_be.lt(10)).sum()),
            **_signflip(post)}


def finalize_existing(failed: Path, output: Path) -> None:
    """Finalize an interrupted post-processing stage without opening OHLCV or replaying.

    ``failed`` is immutable evidence of the first authorized read.  This path
    accepts only its already-materialized tables, hashes them before copying,
    and makes an explicitly separate reused-output receipt.
    """
    if output.exists():
        raise FileExistsError(output)
    failed_manifest = json.loads((failed / "manifest.json").read_text())
    if failed_manifest.get("status") != "failed" or failed_manifest.get("complete"):
        raise ValueError("finalize-existing requires an incomplete failed run")
    required = ["summary.csv", "development_serial_closed_trades.csv.gz", "post_authorized_holdout_serial_closed_trades.csv.gz",
                "development_fixed_entry_pairs.csv.gz", "post_authorized_holdout_fixed_entry_pairs.csv.gz",
                "development_diagnostics.json", "post_authorized_holdout_diagnostics.json"]
    missing = [name for name in required if not (failed / name).is_file()]
    if missing:
        raise ValueError("failed run lacks materialized inputs: " + ", ".join(missing))
    input_hashes = {name: _sha256(failed / name) for name in required}
    output.mkdir(parents=True)
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    receipt = {"mode": "finalize_existing_without_ohlcv_read", "source_failed_run": str(failed),
               "source_failed_manifest_sha256": _sha256(failed / "manifest.json"), "input_sha256": input_hashes,
               "source_commit": source_commit, "no_new_holdout_ohlcv_read": True}
    (output / "reused_input_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    for name in required:
        shutil.copy2(failed / name, output / name)
    pairs = pd.read_csv(output / "post_authorized_holdout_fixed_entry_pairs.csv.gz")
    if len(pairs) != 111 or pairs["fold_baseline"].ne("post_authorized_holdout").any():
        raise AssertionError("reused post paired table is not the fixed 111-entry ETH baseline")
    if not math.isclose(float(pairs.net_r_baseline.sum()), -26.282877310782634, rel_tol=0, abs_tol=1e-10):
        raise AssertionError("reused post paired baseline does not equal -26.282877R")
    if pairs.duplicated(["signal_i", "entry_i", "side"]).any():
        raise AssertionError("reused paired table is not one-to-one")
    outcomes = _paired_outcome_summary(pairs)
    (output / "paired_outcomes.json").write_text(json.dumps(outcomes, indent=2) + "\n")
    output_hashes = {path.name: _sha256(path) for path in sorted(output.iterdir()) if path.is_file()}
    manifest = {"experiment": "exp-spike-v8-eth3m-be-20260913-v1", "status": "complete", "complete": True,
                "mode": "finalize_existing_without_ohlcv_read", "source_commit": source_commit,
                "source_sha256": _sha256(Path(__file__)), "reused_failed_run": str(failed),
                "reused_failed_manifest_sha256": receipt["source_failed_manifest_sha256"], "input_sha256": input_hashes,
                "output_sha256": output_hashes, "holdout": "No new OHLCV read; reused tables created during owner-authorized use #1.",
                "frozen_baseline_check": {"closed_trades": 111, "net_r": -26.282877310782634}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def run(output: Path, *, allow_holdout: bool, holdout_exposure_number: int, prefix_end: pd.Timestamp | None = None) -> None:
    """Run one authorized ETH 3m configuration, never a market-wide scan."""
    config = json.loads(CONFIG.read_text())
    if not allow_holdout or holdout_exposure_number != 1:
        raise ValueError("this study requires the owner-authorized holdout exposure #1")
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    # The receipt precedes *any* full input read (including hashing), so an
    # interrupted authorized read cannot disappear from the experiment record.
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    initial_manifest = {"experiment": config["experiment_id"], "status": "running", "complete": False,
                        "source_commit": source_commit, "source_sha256": _sha256(Path(__file__)), "config_sha256": _sha256(CONFIG),
                        "holdout_exposure_number": 1, "holdout_receipt": "owner authorized 2026-09-13; configuration-specific use #1"}
    (output / "manifest.json").write_text(json.dumps(initial_manifest, indent=2) + "\n")
    (output / "holdout_read_receipt.json").write_text(json.dumps({"status": "started_before_input_read", "exposure": 1, "source_commit": source_commit}, indent=2) + "\n")
    frozen_manifest = json.loads((FROZEN / "manifest.json").read_text())
    frozen_closed_hash, input_hash = _sha256(FROZEN / "primary_closed_trades.csv.gz"), _sha256(ETH_PATH)
    if frozen_manifest.get("source_sha256") != FROZEN_BUILDER_SHA:
        raise ValueError("frozen v3 manifest builder identity does not match the registered primary")
    if frozen_closed_hash != FROZEN_CLOSED_SHA:
        raise ValueError("frozen v3 primary_closed_trades hash mismatch")
    manifest = {"experiment": config["experiment_id"], "status": "running", "complete": False,
                "source_commit": source_commit, "source_sha256": _sha256(Path(__file__)), "config_sha256": _sha256(CONFIG),
                "input": {"eth_3m_csv": str(ETH_PATH), "eth_3m_csv_sha256": input_hash,
                          "note": "v3 manifest pins the historical builder but has no separate CSV digest; exact frozen-ledger parity below authenticates this input."},
                "frozen_primary": str(FROZEN), "frozen_manifest_sha256": _sha256(FROZEN / "manifest.json"),
                "frozen_manifest_builder_sha256": frozen_manifest["source_sha256"], "frozen_primary_closed_sha256": frozen_closed_hash,
                "round_trip_cost": ROUND_TRIP_COST, "rule": config["rule"], "holdout_exposure_number": 1,
                "holdout_receipt": "owner authorized 2026-09-13; configuration-specific use #1", "views": ["fixed_original_entry_pair", "serial_replay"]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "holdout_read_receipt.json").write_text(json.dumps({"status": "started_after_input_identity", "exposure": 1, "input_sha256": input_hash, "source_commit": source_commit}, indent=2) + "\n")
    try:
      source = read_ohlcv(ETH_PATH)
      if prefix_end is not None: source = source.loc[source.index < prefix_end].copy()
      frame = features(source); frame.attrs["minutes"] = MINUTES
      raw, _, admission = v8_mask(source, MINUTES)
      gap = _data_gap(frame, MINUTES)
      frozen = pd.read_csv(FROZEN / "primary_closed_trades.csv.gz")
      frozen = frozen.loc[(frozen.symbol == "ETH-USDT-SWAP") & (frozen.venue == "okx") & (frozen.minutes == MINUTES)].copy()
      frozen["entry_time"] = pd.to_datetime(frozen.entry_time, utc=True)
      all_summary, fixed_pairs = [], []
      for fold, start, end in (("development", DEVELOPMENT_START, HOLDOUT_START), ("post_authorized_holdout", HOLDOUT_START, END)):
        if prefix_end is not None: end = min(end, prefix_end)
        allowed = admission & _fold_mask(frame.index, start, end, MINUTES)
        baseline, _ = replay_serial(frame.loc[frame.index < end], raw.loc[frame.index < end], allowed.loc[frame.index < end], enable_be=False, data_gap=gap.loc[frame.index < end])
        baseline = _decorate(baseline, fold=fold, arm="baseline")
        expected = frozen.loc[(frozen.entry_time >= start) & (frozen.entry_time < end)].copy()
        if fold == "post_authorized_holdout" and prefix_end is None:
            if len(expected) != 111 or not math.isclose(float(expected.net_r.sum()), -26.282877310782634, rel_tol=0, abs_tol=1e-10):
                raise AssertionError("frozen ETH 3m post baseline is not the documented 111 / -26.282877R")
        if prefix_end is None:
            left = baseline[TRADE_KEY].sort_values("signal_i").reset_index(drop=True)
            right = expected[TRADE_KEY].sort_values("signal_i").reset_index(drop=True)
            pd.testing.assert_frame_equal(left, right, check_dtype=False, rtol=1e-10, atol=1e-10)
        be_serial, diagnostic = replay_serial(frame.loc[frame.index < end], raw.loc[frame.index < end], allowed.loc[frame.index < end], enable_be=True, data_gap=gap.loc[frame.index < end])
        be_serial = _decorate(be_serial, fold=fold, arm="be1_wick_next_bar")
        all_summary += _by_side(baseline, view="serial_replay", fold=fold, arm="baseline")
        all_summary += _by_side(be_serial, view="serial_replay", fold=fold, arm="be1_wick_next_bar")
        serial = pd.concat([baseline.assign(view="serial_replay"), be_serial.assign(view="serial_replay")], ignore_index=True)
        serial.to_csv(output / f"{fold}_serial_closed_trades.csv.gz", index=False, compression="gzip")
        if prefix_end is None:
            pairs = expected.copy()
            fixed_frame, fixed_raw = frame.loc[frame.index < end], raw.loc[frame.index < end]
            prepared = prepare_fixed(fixed_frame, fixed_raw)
            be_fixed = pd.DataFrame([replay_fixed_entry(fixed_frame, fixed_raw, row, prepared=prepared) for _, row in pairs.iterrows()])
            be_fixed = _decorate(be_fixed, fold=fold, arm="be1_wick_next_bar")
            # The fixed baseline is the frozen ledger by definition; a matching
            # custom disabled replay is independently enforced above.
            frozen_fixed = _decorate(expected.assign(censored=False), fold=fold, arm="baseline")
            all_summary += _by_side(frozen_fixed, view="fixed_original_entry_pair", fold=fold, arm="baseline")
            all_summary += _by_side(be_fixed, view="fixed_original_entry_pair", fold=fold, arm="be1_wick_next_bar")
            pairs = frozen_fixed.merge(be_fixed, on=["signal_i", "entry_i", "side"], suffixes=("_baseline", "_be"), validate="one_to_one")
            pairs["net_r_difference"] = pairs.net_r_be - pairs.net_r_baseline
            pairs["rescued_original_loser"] = pairs.net_r_baseline.lt(0) & pairs.net_r_be.gt(pairs.net_r_baseline)
            pairs["lost_original_winner"] = pairs.net_r_baseline.gt(0) & pairs.net_r_be.le(0)
            pairs["lost_original_10r"] = pairs.net_r_baseline.ge(10) & pairs.net_r_be.lt(10)
            pairs.to_csv(output / f"{fold}_fixed_entry_pairs.csv.gz", index=False, compression="gzip")
            fixed_pairs.append(pairs)
        (output / f"{fold}_diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
      summary = pd.DataFrame(all_summary); summary.to_csv(output / "summary.csv", index=False)
      pairs = pd.concat(fixed_pairs, ignore_index=True) if fixed_pairs else pd.DataFrame()
      if len(pairs):
          post = pairs.loc[pairs.fold_baseline.eq("post_authorized_holdout")]
          outcomes = _paired_outcome_summary(post)
      else: outcomes = {}
      (output / "paired_outcomes.json").write_text(json.dumps(outcomes, indent=2) + "\n")
      (output / "holdout_read_receipt.json").write_text(json.dumps({"status": "complete", "exposure": 1, "input_sha256": input_hash, "source_commit": source_commit}, indent=2) + "\n")
      files = {path.name: _sha256(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "manifest.json"}
      manifest.update(status="complete", complete=True, output_sha256=files)
      (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except Exception as exc:
      manifest.update(status="failed", complete=False, error=f"{type(exc).__name__}: {exc}")
      (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
      (output / "holdout_read_receipt.json").write_text(json.dumps({"status": "failed_after_authorized_read_start", "exposure": 1, "input_sha256": input_hash, "source_commit": source_commit, "error": str(exc)}, indent=2) + "\n")
      raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--holdout-exposure-number", type=int)
    parser.add_argument("--prefix-end")
    parser.add_argument("--finalize-existing", type=Path,
                        help="Pure post-processing reuse of a failed materialized run; never opens OHLCV.")
    args = parser.parse_args()
    if args.finalize_existing is not None:
        if args.allow_holdout or args.holdout_exposure_number is not None or args.prefix_end:
            raise ValueError("finalize-existing cannot be combined with replay or holdout-read flags")
        finalize_existing(args.finalize_existing, args.out)
        return
    run(args.out, allow_holdout=args.allow_holdout, holdout_exposure_number=args.holdout_exposure_number,
        prefix_end=pd.Timestamp(args.prefix_end, tz="UTC") if args.prefix_end else None)


if __name__ == "__main__":
    main()
