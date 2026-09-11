"""Post-process frozen SPIKE V6 BB squeeze runner ledgers.

This builder never recreates V6 signals or BB features.  It aggregates the
runner's CSVs, retains censored positions separately, and uses the already
verified WVF single-event matcher solely as a matched-event diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study_post import matched_random_controls


FOLD_CLOCK = {
    "development": (pd.Timestamp("2024-09-10T00:00:00Z"), pd.Timestamp("2025-09-10T00:00:00Z")),
    "validation": (pd.Timestamp("2025-09-10T00:00:00Z"), pd.Timestamp("2026-09-10T00:00:00Z")),
}
CONTROL_VARIANTS = ("A", "B", "C", "D")
KEY = ["symbol", "timeframe_min", "fold", "side", "signal_bar_open"]


def _as_bool(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.lower().isin(("true", "1", "yes"))


def _write_progress(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(paths: list[Path]) -> list[dict[str, str]]:
    return [{"path": str(path.name), "sha256": _sha(path)} for path in sorted(paths)]


def _load_csvs(raw: Path, suffix: str) -> pd.DataFrame:
    tables = [pd.read_csv(path) for path in sorted(raw.glob(f"*_{suffix}.csv.gz"))]
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


def _normalise(signals: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required_signal = {"symbol", "timeframe_min", "fold", "side", "variant", "signal_bar_open", "admitted", "rejection_reason"}
    required_trade = {"symbol", "timeframe_min", "fold", "side", "variant", "signal_bar_open", "censored", "net_r", "net_return", "mfe_r"}
    if not required_signal.issubset(signals):
        raise ValueError(f"signal ledgers missing {sorted(required_signal - set(signals))}")
    if not required_trade.issubset(trades):
        raise ValueError(f"trade ledgers missing {sorted(required_trade - set(trades))}")
    for frame in (signals, trades):
        frame["signal_bar_open"] = pd.to_datetime(frame.signal_bar_open, utc=True)
        frame["side"] = frame.side.astype(int)
        frame["timeframe_min"] = frame.timeframe_min.astype(int)
    signals["admitted"] = _as_bool(signals.admitted)
    trades["censored"] = _as_bool(trades.censored)
    return signals, trades


def _metric_rows(signals: pd.DataFrame, trades: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    signal = signals.groupby(dimensions, dropna=False).agg(
        raw_signals=("side", "size"), admitted_signals=("admitted", "sum")).reset_index()
    positions = trades.groupby(dimensions, dropna=False).agg(
        positions=("side", "size"), censored_positions=("censored", "sum"),
        mfe_ge_10r_all_positions=("mfe_r", lambda x: x.ge(10).sum())).reset_index()
    closed = trades.loc[~trades.censored].copy()
    if closed.empty:
        return signal.merge(positions, on=dimensions, how="left").assign(
            n=0, win_rate=np.nan, profit_factor=np.nan, net_r=0.0,
            realized_net_r_ge_10=0, mfe_ge_10r_closed=0)

    def stats(group: pd.DataFrame) -> pd.Series:
        gains = group.net_return.clip(lower=0).sum()
        losses = -group.net_return.clip(upper=0).sum()
        return pd.Series({"n": len(group), "win_rate": group.net_return.gt(0).mean(),
                          "profit_factor": gains / losses if losses else np.nan,
                          "net_r": group.net_r.sum(),
                          "realized_net_r_ge_10": group.net_r.ge(10).sum(),
                          "mfe_ge_10r_closed": group.mfe_r.ge(10).sum()})
    result = closed.groupby(dimensions, dropna=False)[["net_return", "net_r", "mfe_r"]].apply(stats).reset_index()
    return signal.merge(positions, on=dimensions, how="left").merge(result, on=dimensions, how="left").fillna({"n": 0, "net_r": 0.0, "realized_net_r_ge_10": 0, "mfe_ge_10r_closed": 0})


def _assert_shared_outcomes(all_trades: pd.DataFrame) -> None:
    """A shared A/B/C/D entry must replay the same raw exit path and outcome."""
    baseline = all_trades.loc[all_trades.variant.eq("A")]
    numeric = ["entry_price", "exit_price", "net_r", "net_return", "mfe_r"]
    for variant in ("B", "C", "D"):
        other = all_trades.loc[all_trades.variant.eq(variant)]
        joined = baseline.merge(other, on=KEY, suffixes=("_a", "_v"))
        for column in numeric:
            left, right = joined.get(f"{column}_a"), joined.get(f"{column}_v")
            if left is not None and not np.isclose(left, right, equal_nan=True).all():
                raise AssertionError(f"shared A/{variant} entry has different {column}")
        for column in ("censored", "exit_time", "exit_reason"):
            left, right = joined.get(f"{column}_a"), joined.get(f"{column}_v")
            if left is not None and not left.fillna("<null>").astype(str).eq(right.fillna("<null>").astype(str)).all():
                raise AssertionError(f"shared A/{variant} entry has different {column}")


def _retention(signals: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    closed_a = trades.loc[(trades.variant == "A") & ~trades.censored].copy()
    rows, rejected = [], []
    for variant in ("B", "C", "D"):
        candidate = trades.loc[trades.variant.eq(variant), KEY].drop_duplicates().assign(retained=True)
        joined = closed_a.merge(candidate, on=KEY, how="left")
        joined["retained"] = joined.retained.eq(True)
        grouped = joined.groupby(["fold", "timeframe_min"], dropna=False)
        for keys, group in grouped:
            winners = group.net_r.ge(10)
            rows.append({"fold": keys[0], "variant": variant, "timeframe_min": keys[1],
                         "a_realized_net_r_ge_10": int(winners.sum()),
                         "retained_same_entry_net_r_ge_10": int((winners & group.retained).sum()),
                         "missed_same_entry_net_r_ge_10": int((winners & ~group.retained).sum())})
        variant_signals = signals.loc[signals.variant.eq(variant), KEY + ["admitted", "rejection_reason"]]
        lost = closed_a.merge(variant_signals, on=KEY, how="left")
        lost = lost.loc[~lost.admitted.fillna(False)]
        for keys, group in lost.groupby(["fold", "timeframe_min", "side", "rejection_reason"], dropna=False):
            rejected.append({"fold": keys[0], "timeframe_min": keys[1], "side": keys[2], "variant": variant,
                             "rejection_reason": keys[3], "rejected_n": len(group),
                             "rejected_winners": int(group.net_return.gt(0).sum()),
                             "rejected_losses": int(group.net_return.le(0).sum()),
                             "rejected_net_r": group.net_r.sum()})
    return pd.DataFrame(rows), pd.DataFrame(rejected)


def _stream_ticks(raw: Path) -> dict[tuple[str, int], float]:
    receipt = json.loads((raw / "manifest.json").read_text())
    prep = Path(receipt["preparation_manifest"]["path"])
    streams = json.loads(prep.read_text())["streams"]
    return {(row["symbol"], int(row["timeframe_min"])): float(row["tick"]) for row in streams}


def _pair_summary(pairs: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    """Aggregate every matched-event row directly; never average subgroup means."""
    columns = dimensions + ["seeds", "pairs", "matched_pairs", "match_rate", "mean_net_r_difference", "mean_net_return_difference",
                             "seed_mean_net_r_difference_p05", "seed_mean_net_r_difference_p95",
                             "seed_mean_net_return_difference_p05", "seed_mean_net_return_difference_p95"]
    if pairs.empty:
        return pd.DataFrame(columns=columns)
    summary = pairs.groupby(dimensions, dropna=False).agg(
        seeds=("seed", "nunique"), pairs=("matched", "size"), matched_pairs=("matched", "sum"),
        mean_net_r_difference=("strategy_net_r_difference", "mean"),
        mean_net_return_difference=("strategy_net_return_difference", "mean")).reset_index()
    summary["match_rate"] = summary.matched_pairs / summary.pairs
    seeded = pairs.groupby(dimensions + ["seed"], dropna=False).agg(
        seed_pairs=("matched", "size"), seed_matched_pairs=("matched", "sum"),
        seed_mean_net_r_difference=("strategy_net_r_difference", "mean"),
        seed_mean_net_return_difference=("strategy_net_return_difference", "mean")).reset_index()
    seeded_stats = seeded.groupby(dimensions, dropna=False).agg(
        seed_mean_net_r_difference_p05=("seed_mean_net_r_difference", lambda x: x.quantile(.05)),
        seed_mean_net_r_difference_p95=("seed_mean_net_r_difference", lambda x: x.quantile(.95)),
        seed_mean_net_return_difference_p05=("seed_mean_net_return_difference", lambda x: x.quantile(.05)),
        seed_mean_net_return_difference_p95=("seed_mean_net_return_difference", lambda x: x.quantile(.95))).reset_index()
    return summary.merge(seeded_stats, on=dimensions, how="left")


def _seed_summary(pairs: pd.DataFrame) -> pd.DataFrame:
    columns = ["fold", "variant", "timeframe_min", "side", "seed", "seed_pairs", "seed_matched_pairs", "seed_match_rate",
               "seed_mean_net_r_difference", "seed_mean_net_return_difference"]
    if pairs.empty:
        return pd.DataFrame(columns=columns)
    result = pairs.groupby(["fold", "variant", "timeframe_min", "side", "seed"], dropna=False).agg(
        seed_pairs=("matched", "size"), seed_matched_pairs=("matched", "sum"),
        seed_mean_net_r_difference=("strategy_net_r_difference", "mean"),
        seed_mean_net_return_difference=("strategy_net_return_difference", "mean")).reset_index()
    result["seed_match_rate"] = result.seed_matched_pairs / result.seed_pairs
    return result


def _control_outputs(raw: Path, all_trades: pd.DataFrame, ticks: dict[tuple[str, int], float], output: Path, progress: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    closed = all_trades.loc[~all_trades.censored & all_trades.variant.isin(CONTROL_VARIANTS)].copy()
    rows = []
    for (symbol, minutes, fold), group in closed.groupby(["symbol", "timeframe_min", "fold"], sort=True):
        targets = group.sort_values("variant").drop_duplicates(KEY)[["signal_bar_open", "side", "net_r", "net_return"]]
        cache_path = raw / f"{symbol}_{minutes}m_bb_diagnostic.pkl.gz"
        cache = pd.read_pickle(cache_path)
        bars, diagnostic = cache["bars"].copy(), cache["diagnostic"]
        bars["ready"] = _as_bool(bars.ready) & _as_bool(diagnostic.ready)
        bars.attrs["minutes"] = int(minutes)
        cache = {"bars": bars, "signals": cache["signals"], "data_gap": cache["data_gap"]}
        start, end = FOLD_CLOCK[fold]
        pairs, _ = matched_random_controls(cache, targets, tick=ticks[(symbol, int(minutes))], fold_start=start, fold_end=end, seeds=range(99))
        for column in ("control_net_r", "control_net_return"):
            if column not in pairs:
                pairs[column] = np.nan
        pairs["symbol"], pairs["timeframe_min"], pairs["fold"] = symbol, minutes, fold
        for variant, variant_trades in group.groupby("variant"):
            actual = variant_trades[KEY + ["net_r", "net_return"]].drop_duplicates(KEY)
            merged = pairs.merge(actual, left_on=["symbol", "timeframe_min", "fold", "side", "target_time"], right_on=KEY, how="inner", suffixes=("_control_target", "_strategy"))
            merged["variant"] = variant
            merged["strategy_net_r_difference"] = merged.net_r - merged.control_net_r
            merged["strategy_net_return_difference"] = merged.net_return - merged.control_net_return
            rows.append(merged)
        _write_progress(progress, {"symbol": symbol, "timeframe_min": int(minutes), "fold": fold, "stage": "controls_complete"})
    pairs = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["fold", "variant", "timeframe_min", "side", "seed", "matched", "strategy_net_r_difference", "strategy_net_return_difference"])
    if pairs.empty:
        return pairs, pd.DataFrame(columns=["fold", "variant", "timeframe_min", "side", "seeds", "pairs", "matched_pairs", "match_rate", "mean_net_r_difference", "mean_net_return_difference"])
    summary = _pair_summary(pairs, ["fold", "variant", "timeframe_min", "side"])
    return pairs, summary


def _write_post_manifest(raw: Path, output: Path) -> None:
    """Write immutable provenance after every non-huge post artifact is complete."""
    inputs = (sorted(raw.glob("*_signals.csv.gz")) + sorted(raw.glob("*_trades.csv.gz"))
              + sorted(raw.glob("*_bb_diagnostic.pkl.gz")) + [raw / "manifest.json"])
    excluded = "matched_control_pairs.csv.gz"
    outputs = [path for path in output.iterdir() if path.is_file() and path.name not in {"post_manifest.json", excluded, "progress.jsonl"}]
    payload = {
        "raw_manifest": {"path": str((raw / "manifest.json").resolve()), "sha256": _sha(raw / "manifest.json")},
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_hashes": {"builder": _sha(Path(__file__)), "matched_control_engine": _sha(Path(__file__).with_name("spike_v6_wvf_study_post.py"))},
        "input_receipt": _receipt(inputs),
        "output_receipt": _receipt(outputs),
        "output_hash_exclusions": {excluded: "potentially large pair-level diagnostic; row count is in matched_control_summary.csv"},
    }
    (output / "post_manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build(raw: Path, output: Path) -> None:
    """Build aggregate BB post data from a committed runner output directory."""
    output.mkdir(parents=True, exist_ok=False)
    progress = output / "progress.jsonl"
    signals, all_trades = _normalise(_load_csvs(raw, "signals"), _load_csvs(raw, "trades"))
    closed = all_trades.loc[~all_trades.censored].copy()
    censored = all_trades.loc[all_trades.censored].copy()
    _assert_shared_outcomes(all_trades)
    signals.to_csv(output / "signals.csv.gz", index=False, compression="gzip")
    all_trades.to_csv(output / "all_trades.csv.gz", index=False, compression="gzip")
    closed.to_csv(output / "closed_trades.csv.gz", index=False, compression="gzip")
    censored.to_csv(output / "censored_trades.csv.gz", index=False, compression="gzip")
    _metric_rows(signals, all_trades, ["fold", "variant", "timeframe_min"]).to_csv(output / "metrics_by_timeframe.csv", index=False)
    _metric_rows(signals, all_trades, ["fold", "variant", "side"]).to_csv(output / "metrics_by_side.csv", index=False)
    retention, rejected = _retention(signals, all_trades)
    retention.to_csv(output / "retention_by_timeframe.csv", index=False)
    rejected.to_csv(output / "rejected_outcomes_by_reason.csv", index=False)
    ticks = _stream_ticks(raw)
    pairs, summary = _control_outputs(raw, all_trades, ticks, output, progress)
    pairs.to_csv(output / "matched_control_pairs.csv.gz", index=False, compression="gzip")
    summary.to_csv(output / "matched_control_summary.csv", index=False)
    _seed_summary(pairs).to_csv(output / "matched_control_seed_summary.csv", index=False)
    allside = _pair_summary(pairs, ["fold", "variant", "timeframe_min"])
    allside.to_csv(output / "matched_control_allside_summary.csv", index=False)
    overall = _pair_summary(pairs, ["fold", "variant"])
    overall.to_csv(output / "matched_control_overall_summary.csv", index=False)
    _write_progress(progress, {"stage": "post_complete", "signals": len(signals), "closed_trades": len(closed), "censored_trades": len(censored)})
    _write_post_manifest(raw, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.raw, args.output)
