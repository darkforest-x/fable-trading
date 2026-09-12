"""Matched random-entry controls for development-only SPIKE market breadth.

The only outcome input is stage one's bounded ``candidate_context.csv.gz``.
For every actual target, a control is selected at a causal signal-bar close in
the same venue, symbol, timeframe, continuous segment, side, calendar month,
and quartile of ATR/close.  Quartiles use the *preceding* 120 closed bars;
their current bar is never in its own ranking window.  Execution enters the
next observed open and reuses ``evaluate_single_control``: five-bar initial
risk, 2R arm, 4ATR trail, raw opposite-V6 exit, and 0.2% round-trip cost.

Only normalized 30-minute source prefixes before 2025-09-10 are streamed via
``spike_market_breadth_study._load_bars``.  The V7 replay input manifest maps
an identity to its small ``control_cache.receipt.json`` solely to obtain the
receipt-pinned tick and source identity; this module never opens
``control_cache.pkl.gz`` or a combined trade ledger.  V7 controls additionally
require causal BB readiness, while V1 common execution does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features as v1_features
from yoyo.evaluation.spike_market_breadth_study import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    _load_bars,
    complete_aggregate_30m,
)
from yoyo.evaluation.spike_v1_twoyear_allmarkets import _continuous
from yoyo.evaluation.spike_v6_wvf_study import _data_gap, v6_signals
from yoyo.evaluation.spike_v6_wvf_study_post import evaluate_single_control
from yoyo.evaluation.spike_v7_v1_compare import (
    reference_v7_diagnostics,
    v1_common_signals,
    v1_common_with_v6_exit_feed,
)


ROOT = Path(__file__).resolve().parents[2]
STAGE_ONE = ROOT / "experiments/active/exp-spike-market-breadth-20260913-v1/results"
V7_RAW = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3"
DEFAULT_OUT = ROOT / "experiments/active/exp-spike-market-breadth-matched-controls-20260913-v1/results"
VARIANTS = ("v1_common_execution_long", "v7_bb_long")
IDENTITY = ("venue", "symbol", "timeframe_min", "segment")
TARGET_REQUIRED = set(IDENTITY) | {
    "variant", "signal_bar_open", "entry_time", "exit_time", "side", "censored", "net_r", "net_return",
}


def sha256(path: Path) -> str:
    """Return a complete digest only for a small receipt or manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _truth(value: object) -> bool:
    """Interpret CSV boolean cells without treating the string ``False`` as true."""
    return bool(value) if isinstance(value, (bool, np.bool_)) else str(value).strip().lower() == "true"


def _identity(row: object) -> tuple[str, str, int, int]:
    return (str(getattr(row, "venue")), str(getattr(row, "symbol")),
            int(getattr(row, "timeframe_min")), int(getattr(row, "segment")))


def _read_targets(path: Path) -> pd.DataFrame:
    """Read only the stage-one candidate context and reject post-fold outcomes."""
    table = pd.read_csv(path)
    missing = TARGET_REQUIRED.difference(table.columns)
    if missing:
        raise ValueError("candidate context missing columns: " + ", ".join(sorted(missing)))
    for name in ("signal_bar_open", "entry_time", "exit_time"):
        table[name] = pd.to_datetime(table[name], utc=True, errors="raise")
    table["censored"] = table.censored.map(_truth)
    table["side"] = pd.to_numeric(table.side, errors="raise").astype(int)
    if not table.side.isin((1, -1)).all():
        raise ValueError("candidate context contains an invalid side")
    if (table.variant.isin(VARIANTS) & table.side.ne(1)).any():
        raise ValueError("the frozen breadth variants are long-only and require side=1")
    if (table.entry_time.ge(DEVELOPMENT_END) | table.exit_time.ge(DEVELOPMENT_END)).any():
        raise ValueError("candidate context contains a post-development outcome")
    if table.entry_time.lt(DEVELOPMENT_START).any():
        raise ValueError("candidate context contains a pre-development target")
    table = table.loc[table.variant.isin(VARIANTS) & ~table.censored].copy()
    table = table.loc[np.isfinite(pd.to_numeric(table.net_r, errors="coerce"))
                      & np.isfinite(pd.to_numeric(table.net_return, errors="coerce"))].copy()
    table["target_id"] = [f"target-{i}" for i in table.index]
    return table.reset_index(drop=True)


def _source_rows(path: Path) -> dict[tuple[str, str], dict]:
    """Index stage-one preferred normalized sources without opening their payloads."""
    table = pd.read_csv(path)
    required = {"venue", "symbol", "source_path", "frozen_upstream_expected_full_source_sha256",
                "actual_development_prefix_sha256"}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError("source manifest missing columns: " + ", ".join(sorted(missing)))
    result: dict[tuple[str, str], dict] = {}
    for row in table.itertuples(index=False):
        key = (str(row.venue), str(row.symbol))
        if key in result:
            raise ValueError(f"source manifest has non-unique source identity: {key}")
        result[key] = row._asdict()
    return result


def _v7_streams(path: Path) -> dict[tuple[str, str, int, int], dict]:
    """Map target identities to V7 stream receipts; ambiguity is a hard error."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("streams"), list):
        raise ValueError("V7 input manifest has no stream list")
    result: dict[tuple[str, str, int, int], dict] = {}
    for item in raw["streams"]:
        needed = {"key", "source_path", "source_sha256", "minutes", "segment"}
        if not needed.issubset(item):
            raise ValueError("V7 input manifest stream lacks required identity")
        # The stream key is the canonical mapping from a folder to its receipt.
        # Venue/symbol are recovered by joining the source manifest before use.
        key = (str(Path(item["source_path"]).resolve()), int(item["minutes"]), int(item["segment"]), 0)
        if key in result:
            raise ValueError(f"V7 input manifest has non-unique stream mapping: {key}")
        result[key] = item
    return result


def _stream_for_target(target: object, source: dict, streams: dict, streams_root: Path) -> tuple[dict, dict, Path]:
    """Resolve one target to exactly one receipt-pinned V7 stream and tick."""
    source_key = (str(getattr(target, "venue")), str(getattr(target, "symbol")))
    source_row = source.get(source_key)
    if source_row is None:
        raise ValueError(f"missing preferred normalized source for {source_key}")
    source_path = str(Path(source_row["source_path"]).resolve())
    key = (source_path, int(getattr(target, "timeframe_min")), int(getattr(target, "segment")), 0)
    stream = streams.get(key)
    if stream is None:
        raise ValueError(f"missing or non-unique V7 stream mapping for {source_key}/{key[1:3]}")
    if str(stream["source_sha256"]) != str(source_row["frozen_upstream_expected_full_source_sha256"]):
        raise ValueError(f"V7/source-manifest source hash mismatch for {source_key}")
    receipt_path = streams_root / str(stream["key"]) / "control_cache.receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"missing control-cache receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("key") != stream["key"] or receipt.get("source_sha256") != stream["source_sha256"]:
        raise ValueError(f"control-cache receipt identity mismatch: {receipt_path}")
    try:
        tick = float(receipt["tick"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"control-cache receipt lacks tick: {receipt_path}") from exc
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError(f"control-cache receipt has invalid tick: {receipt_path}")
    receipt["tick"] = tick
    return source_row, receipt, receipt_path


def _rebuild_cache(source_row: dict, *, minutes: int, segment: int, tick: float,
                   expected_prefix_sha256: str) -> dict:
    """Rebuild a minimal causal replay cache from one safe 30m source prefix."""
    digest = hashlib.sha256()
    raw = _load_bars(str(source_row["source_path"]), prefix_digest=digest)
    if digest.hexdigest() != str(expected_prefix_sha256):
        raise ValueError("normalized source development-prefix digest drift")
    bars = complete_aggregate_30m(raw, minutes)
    segments = _continuous(bars, minutes)
    if segment < 0 or segment >= len(segments):
        raise ValueError("target segment is absent from development source prefix")
    frame = v1_features(segments[segment])
    frame.attrs["minutes"] = minutes
    gap = _data_gap(frame, minutes)
    raw_v6 = v6_signals(frame, minutes)
    diagnostic = reference_v7_diagnostics(frame, data_gap=gap)
    v1 = v1_common_signals(frame, tick)
    v1, _ = v1_common_with_v6_exit_feed(v1, raw_v6.short_signal)
    return {"bars": frame, "raw_v6": raw_v6, "v1": v1, "data_gap": gap,
            "bb_ready": diagnostic.v7_ready.astype(bool), "tick": tick}


def _variant_cache(cache: dict, variant: str) -> dict:
    """Select raw exits and eligibility gates without changing the replay engine."""
    if variant not in VARIANTS:
        raise ValueError(f"unsupported breadth-control variant: {variant}")
    bars = cache["bars"].copy()
    if variant == "v1_common_execution_long":
        signals = cache["v1"].copy()
    else:
        signals = cache["raw_v6"].copy()
        bars["ready"] = bars.ready.astype(bool) & cache["bb_ready"].astype(bool)
    # A side-opposite raw event is exit-only, so it cannot also serve as a
    # fresh random entry at that same close.
    return {"bars": bars, "signals": signals, "data_gap": cache["data_gap"], "tick": cache["tick"]}


def _quartile_buckets(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return current ATR/close quartiles from the preceding 120 completed bars."""
    fraction = pd.to_numeric(bars.atr, errors="coerce") / pd.to_numeric(bars.close, errors="coerce")
    prior = fraction.shift(1)
    quantiles = pd.concat([prior.rolling(120, min_periods=120).quantile(q) for q in (.25, .5, .75)], axis=1)
    values = quantiles.to_numpy(float)
    return (fraction.to_numpy(float)[:, None] > values).sum(axis=1), np.isfinite(values).all(axis=1)


def match_stream_controls(cache: dict, targets: pd.DataFrame, *, variant: str,
                          seed: int = 0, fold_start: pd.Timestamp = DEVELOPMENT_START,
                          fold_end: pd.Timestamp = DEVELOPMENT_END) -> pd.DataFrame:
    """Pair targets with deterministic same-stream/month/causal-volatility controls."""
    required = {"target_id", "signal_bar_open", "side", "net_r", "net_return"}
    if missing := required.difference(targets.columns):
        raise ValueError("targets missing columns: " + ", ".join(sorted(missing)))
    context = _variant_cache(cache, variant)
    bars, signals = context["bars"], context["signals"]
    if not bars.index.equals(signals.index):
        raise ValueError("control bars and raw exit signals have different clocks")
    bucket, bucket_ready = _quartile_buckets(bars)
    cadence = pd.Timedelta(minutes=int(bars.attrs["minutes"]))
    gap = pd.Series(context["data_gap"], index=bars.index).fillna(True).astype(bool)
    valid = np.isfinite(bars[["open", "high", "low", "close", "atr"]].to_numpy(float)).all(axis=1)
    next_contiguous = ~gap.shift(-1, fill_value=True).to_numpy(bool)
    next_valid = np.r_[valid[1:], False]
    history_continuous = ~gap.rolling(5, min_periods=5).max().fillna(1).to_numpy(bool)
    eligible = (bars.ready.fillna(False).to_numpy(bool) & bucket_ready & valid & next_valid & next_contiguous
                & history_continuous & (bars.atr.to_numpy(float) > 0) & (bars.close.to_numpy(float) > 0)
                & ((bars.index + cadence) >= fold_start) & ((bars.index + cadence) < fold_end))
    months = bars.index.strftime("%Y-%m").to_numpy()
    replayed: dict[tuple[int, int], dict | None] = {}
    rows: list[dict] = []
    for target in targets.itertuples(index=False):
        stamp, side = pd.Timestamp(target.signal_bar_open), int(target.side)
        identity = {name: getattr(target, name) for name in IDENTITY if hasattr(target, name)}
        base = {**identity, "target_id": target.target_id, "target_time": stamp, "side": side, "variant": variant,
                "matched": False, "reason": "unmatched"}
        if stamp not in bars.index:
            rows.append({**base, "reason": "target_not_in_rebuilt_segment"}); continue
        i = int(bars.index.get_loc(stamp))
        if not bucket_ready[i]:
            rows.append({**base, "reason": "target_bucket_unavailable"}); continue
        allowed = eligible.copy()
        opposite = signals.short_signal.to_numpy(bool) if side == 1 else signals.long_signal.to_numpy(bool)
        allowed &= ~opposite
        choices = np.flatnonzero(allowed & (months == months[i]) & (bucket == bucket[i]))
        choices = choices[choices != i]
        if not len(choices):
            rows.append({**base, "reason": "no_exact_causal_match"}); continue
        selected = int(choices[int(hashlib.sha256(
            f"{seed}|{target.target_id}|{stamp.isoformat()}".encode("utf-8")).hexdigest(), 16) % len(choices)])
        key = (selected, side)
        if key not in replayed:
            replayed[key] = evaluate_single_control(context, selected, side, tick=float(context["tick"]),
                                                     fold_start=fold_start, fold_end=fold_end)
        control = replayed[key]
        if control is None or bool(control.get("censored", False)):
            rows.append({**base, "reason": "control_unresolved"}); continue
        rows.append({**base, "matched": True, "reason": "matched", "candidate_time": bars.index[selected],
                     "calendar_month": months[i], "volatility_quartile": int(bucket[i]),
                     "target_net_r": float(target.net_r), "control_net_r": float(control["net_r"]),
                     "net_r_difference": float(target.net_r) - float(control["net_r"]),
                     "target_net_return": float(target.net_return), "control_net_return": float(control["net_return"]),
                     "net_return_difference": float(target.net_return) - float(control["net_return"])})
    return pd.DataFrame(rows)


def paired_sign_flip_p(values: pd.Series, *, seed: int = 0, draws: int = 9_999) -> float:
    """Two-sided deterministic paired sign-flip p-value; no IID claim beyond pairs."""
    value = pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)
    if len(value) < 2:
        return math.nan
    observed = abs(float(value.mean()))
    signs = np.random.default_rng(seed).choice((-1.0, 1.0), size=(draws, len(value)))
    null = np.abs((signs * value).mean(axis=1))
    return float((1 + np.sum(null >= observed)) / (draws + 1))


def summarize_controls(pairs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Summarize baseline, metric quartiles, and frozen 60m breadth rule cohorts."""
    numeric = ("up_participation", "fast_breadth", "joint_breadth", "joint_delta_30m", "joint_delta_60m",
               "launch_density_1h", "rv_tr_atr_expansion", "return_median_30m", "return_iqr_30m",
               "btc_return_30m", "eth_return_30m")
    if pairs.empty:
        pairs = pd.DataFrame(columns=["target_id", "matched", "reason", "target_net_r", "control_net_r", "net_r_difference"])
    rows = []
    group_keys = [name for name in ("variant", "timeframe_min") if name in targets]
    grouped = targets.groupby(group_keys, dropna=False, sort=True) if group_keys else [((), targets)]
    for group, scoped_targets in grouped:
        if not isinstance(group, tuple):
            group = (group,)
        group_values = dict(zip(group_keys, group))
        cohorts: list[tuple[str, str, pd.Series]] = [("baseline", "all", pd.Series(True, index=scoped_targets.index))]
        for metric in numeric:
            if metric not in scoped_targets:
                continue
            values = pd.to_numeric(scoped_targets[metric], errors="coerce")
            if values.notna().sum() < 4:
                continue
            cohorts.extend(((metric, "bottom_quartile", values.le(values.quantile(.25))),
                            (metric, "top_quartile", values.ge(values.quantile(.75)))))
        if "joint_delta_60m" in scoped_targets:
            cohorts.append(("joint_delta_60m", "positive_rule", pd.to_numeric(
                scoped_targets.joint_delta_60m, errors="coerce").gt(0)))
        for metric, slice_name, mask in cohorts:
            selected = scoped_targets.loc[mask.fillna(False), "target_id"]
            part = pairs.loc[pairs.target_id.isin(selected)] if len(pairs) else pairs
            good = part.loc[part.matched.eq(True)] if len(part) else part
            rows.append({**group_values, "metric": metric, "slice": slice_name, "targets": int(len(part)),
                         "matched": int(len(good)), "match_rate": float(len(good) / len(part)) if len(part) else math.nan,
                         "target_mean_net_r": good.target_net_r.mean() if len(good) else math.nan,
                         "control_mean_net_r": good.control_net_r.mean() if len(good) else math.nan,
                         "paired_delta_mean_net_r": good.net_r_difference.mean() if len(good) else math.nan,
                         "paired_sign_flip_p": paired_sign_flip_p(good.net_r_difference) if len(good) else math.nan,
                         "unmatched_reasons": json.dumps(dict(sorted(Counter(part.loc[~part.matched.eq(True), "reason"]).items()))),
                         })
    return pd.DataFrame(rows)


def run(stage_one: Path = STAGE_ONE, v7_raw: Path = V7_RAW, output: Path = DEFAULT_OUT) -> None:
    """Build a new bounded control artifact; refuse all existing destinations."""
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output: {output}")
    targets_path, source_path = stage_one / "candidate_context.csv.gz", stage_one / "source_manifest.csv"
    targets, sources = _read_targets(targets_path), _source_rows(source_path)
    input_manifest = v7_raw / "input_manifest.json"
    streams_root = v7_raw / "streams"
    streams = _v7_streams(input_manifest)
    all_pairs, receipts, failures = [], [], []
    for identity, part in targets.groupby(list(IDENTITY), sort=True):
        representative = next(part.itertuples(index=False))
        try:
            source_row, receipt, receipt_path = _stream_for_target(representative, sources, streams, streams_root)
            cache = _rebuild_cache(source_row, minutes=int(identity[2]), segment=int(identity[3]), tick=receipt["tick"],
                                   expected_prefix_sha256=str(source_row["actual_development_prefix_sha256"]))
            for variant, chosen in part.groupby("variant", sort=True):
                all_pairs.append(match_stream_controls(cache, chosen, variant=str(variant)))
            receipts.append({"venue": identity[0], "symbol": identity[1], "timeframe_min": identity[2], "segment": identity[3],
                             "receipt_path": str(receipt_path), "receipt_sha256": sha256(receipt_path),
                             "source_sha256": receipt["source_sha256"], "tick": receipt["tick"]})
        except (FileNotFoundError, ValueError) as exc:
            failures.extend({**{name: getattr(row, name) for name in IDENTITY}, "target_id": row.target_id,
                             "target_time": row.signal_bar_open, "side": row.side,
                             "variant": row.variant, "matched": False, "reason": f"fail_closed:{exc}"}
                            for row in part.itertuples(index=False))
    pairs = pd.concat(all_pairs + ([pd.DataFrame(failures)] if failures else []), ignore_index=True) if (all_pairs or failures) else pd.DataFrame()
    summary = summarize_controls(pairs, targets)
    output.mkdir(parents=True)
    pairs.to_csv(output / "matched_control_pairs.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "matched_control_summary.csv", index=False)
    pd.DataFrame(receipts).to_csv(output / "control_receipts.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({
        "development_start": DEVELOPMENT_START.isoformat(), "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "seed": 0, "input_candidate_context_sha256": sha256(targets_path), "input_source_manifest_sha256": sha256(source_path),
        "input_v7_manifest_sha256": sha256(input_manifest), "receipt_count": len(receipts),
        "forbidden_inputs": ["control_cache.pkl.gz", "combined_trade_ledger", "post_2025-09-10 outcomes"],
    }, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="write one new matched-control artifact")
    parser.add_argument("--stage-one", type=Path, default=STAGE_ONE)
    parser.add_argument("--v7-raw", type=Path, default=V7_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required")
    run(args.stage_one, args.v7_raw, args.output)


if __name__ == "__main__":
    main()
