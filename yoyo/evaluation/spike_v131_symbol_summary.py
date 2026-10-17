"""Aggregate archived V13.1-compatible closed trades by symbol and direction.

Owner request 2026-10-17: rank 15m and 1H symbols by cumulative net R, with
trade count, win rate, mean net R and R-based profit factor, separately by side.
This descriptive outcome grouping introduces no entry feature or strategy gate.
15m uses only the both-admission retest ledger; alternative admissions duplicate
events. 1H uses the frozen-entry trail2 exit replay and the original incumbent
parity eligibility. It is not a new serial backtest after earlier exits.
Reuse each source's already-matched controls without redrawing or broadening its
eligibility. Missing controls remain missing. No content hashes are computed.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

CONFIG = Path("experiments/active/exp-spike-v131-symbol-summary-20261017-v1/config.json")


def metrics(frame: pd.DataFrame) -> dict:
    """Use finite closed net_r outcomes; zero is neither a win nor a loss."""
    r = frame.net_r.to_numpy(float)
    if not len(r) or not np.isfinite(r).all():
        raise ValueError("empty or non-finite outcome group")
    positive = math.fsum(float(x) for x in r if x > 0)
    negative = -math.fsum(float(x) for x in r if x < 0)
    total = math.fsum(r)
    return dict(trades=len(r), wins=int((r > 0).sum()), win_rate=float((r > 0).mean()),
                sum_net_r=total, mean_net_r=total / len(r),
                pf_r=positive / negative if negative else ("inf" if positive else None),
                positive_net_r=positive, negative_net_r_abs=negative)


def paired_metrics(pairs: pd.DataFrame, total: int) -> dict:
    n = len(pairs)
    target = math.fsum(pairs.net_r) if n else 0.0
    random = math.fsum(pairs.control_net_r) if n else 0.0
    return dict(matched=n, matched_coverage=n / total,
                matched_target_sum_net_r=target, matched_random_sum_net_r=random,
                matched_target_mean_net_r=target / n if n else None,
                random_mean_net_r=random / n if n else None,
                matched_excess_mean_net_r=(target - random) / n if n else None)


def assert_ledger(frame: pd.DataFrame, cfg: dict) -> None:
    if frame.trade_key.duplicated().any() or not frame.side.isin([-1, 1]).all():
        raise ValueError("duplicate events or invalid side")
    values = frame[["net_r", "net_return", "gross_return"]].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("missing closed outcome")
    np.testing.assert_allclose(frame.gross_return - frame.net_return,
                               cfg["round_trip_cost"], atol=1e-12, rtol=0)
    entry = pd.to_datetime(frame.entry_time, utc=True)
    lower = pd.to_datetime(frame.timeframe_min.map({15: cfg["start"], 60: cfg["source_1h_earliest_entry"]}), utc=True)
    if not ((entry >= lower) & (entry < pd.Timestamp(cfg["end"]))).all():
        raise ValueError("entry outside source analysis window")


def match(target: pd.DataFrame, control: pd.DataFrame) -> pd.DataFrame:
    keys = ["trade_key", "symbol", "timeframe_min", "side"]
    if control.trade_key.duplicated().any():
        raise ValueError("duplicate random control")
    return target.merge(control[keys + ["net_r"]].rename(columns={"net_r": "control_net_r"}),
                        on=keys, how="inner", validate="one_to_one")


def load(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, list[Path]]:
    p15, p60 = Path(cfg["source_15m"]), Path(cfg["source_1h"])
    raw15 = pd.read_csv(p15 / "trades.csv.gz")
    selected15 = raw15[raw15.admission.eq("both") & raw15.policy.eq("retest") &
                       raw15.arm.eq("v9_both") & raw15.timeframe_min.eq(15)]
    t15 = selected15[selected15.status.eq("closed")].copy()
    c15 = pd.read_csv(p15 / "matched_controls.csv.gz")
    if not (c15.matched.eq(True) & c15.control_status.eq("closed") &
            c15.policy.eq("retest") & c15.timeframe_min.eq(15)).all():
        raise ValueError("unexpected prepared 15m control eligibility")
    pair15 = match(t15, c15)
    prior15 = pd.read_csv(p15 / "summary.csv")
    for side, admission in [(None, "both"), (1, "old_longs"), (-1, "old_shorts")]:
        part = t15 if side is None else t15[t15.side.eq(side)]
        pairs = pair15 if side is None else pair15[pair15.side.eq(side)]
        actual = metrics(part) | paired_metrics(pairs, len(part))
        old = prior15[prior15.admission.eq(admission) & prior15.period.eq("all")].iloc[0]
        for new, previous in [("trades", "closed"), ("wins", "wins"), ("sum_net_r", "sum_net_r"),
                              ("mean_net_r", "mean_net_r"), ("pf_r", "pf_r"),
                              ("matched", "matched"), ("random_mean_net_r", "random_mean_net_r")]:
            np.testing.assert_allclose(actual[new], old[previous], atol=1e-9, rtol=0)

    raw60 = pd.read_csv(p60 / "trades.csv.gz", low_memory=False)
    raw60 = raw60[raw60.version.eq("retest") & raw60.timeframe_min.eq(60)]
    keys = ["cohort", "version", "timeframe_min", "trade_key", "entry_time"]
    ok = raw60[raw60.policy.eq("incumbent") & raw60.parity.eq("ok")][keys]
    eligible = raw60[raw60.policy.eq("trail2")].merge(ok, on=keys, validate="one_to_one")
    t60 = eligible[eligible.cohort.eq("signal")].copy()
    c60 = eligible[eligible.cohort.eq("control")].copy()
    prior60 = pd.read_csv(p60 / "summary.csv")
    for cohort, part in [("signal", t60), ("control", c60)]:
        early = pd.to_datetime(part.exit_time, utc=True) < pd.Timestamp("2025-01-01T00:00:00Z")
        for period, selector in [("select_<2025", early), ("check_>=2025", ~early)]:
            old = prior60[prior60.cohort.eq(cohort) & prior60.version.eq("retest") &
                          prior60.timeframe_min.eq(60) & prior60.policy.eq("trail2") &
                          prior60.period.eq(period)].iloc[0]
            actual = metrics(part[selector])
            for new, previous in [("trades", "n"), ("sum_net_r", "sum_net_r"),
                                  ("mean_net_r", "mean_net_r"), ("pf_r", "profit_factor")]:
                np.testing.assert_allclose(actual[new], old[previous], atol=1e-9, rtol=0)
    for part in [t15, t60]:
        assert_ledger(part, cfg)
    pair60 = match(t60, c60)
    before_start = t60[pd.to_datetime(t60.entry_time, utc=True) < pd.Timestamp(cfg["start"])]
    audits = {"source_summary_reconciliations": "passed", "unique_closed_trade_keys": "passed",
              "cost_20bp": "passed", "15m_excluded_censored": len(selected15) - len(t15),
              "15m_other_admissions_excluded": len(raw15) - len(selected15),
              "1h_preserved_source_entries_before_2023": len(before_start),
              "1h_preserved_source_entries_before_2023_net_r": math.fsum(before_start.net_r),
              "1h_frozen_entry_exit_replay": True}
    files = [p15 / n for n in ["trades.csv.gz", "matched_controls.csv.gz", "summary.csv"]]
    files += [p60 / n for n in ["trades.csv.gz", "summary.csv"]]
    return pd.concat([t15, t60]), pd.concat([pair15, pair60]), audits, files


def run(config: Path, output: Path | None = None) -> None:
    cfg = json.loads(config.read_text())
    output = output or Path(cfg["output"])
    if output.exists():
        raise ValueError("refusing to overwrite existing outputs")
    for path in [Path(__file__), config]:
        subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", str(path)], check=True, stdout=subprocess.DEVNULL)
    trades, pairs, audit, paths = load(cfg)
    rows, totals = [], []
    for (minutes, side), part in trades.groupby(["timeframe_min", "side"]):
        selected_pairs = pairs[pairs.timeframe_min.eq(minutes) & pairs.side.eq(side)]
        label = {"timeframe_min": int(minutes), "timeframe": "15m" if minutes == 15 else "1H",
                 "side": int(side), "direction": "多头" if side == 1 else "空头"}
        totals.append(label | {"symbols": int(part.symbol.nunique()),
                               "first_entry": str(pd.to_datetime(part.entry_time, utc=True).min()),
                               "last_entry": str(pd.to_datetime(part.entry_time, utc=True).max())} | metrics(part) |
                      paired_metrics(selected_pairs, len(part)))
        group = []
        for symbol, subset in part.groupby("symbol"):
            matched = selected_pairs[selected_pairs.symbol.eq(symbol)]
            group.append(label | {"symbol": symbol} | metrics(subset) | paired_metrics(matched, len(subset)))
        group.sort(key=lambda r: (-r["sum_net_r"], r["symbol"]))
        for rank, row in enumerate(group, 1):
            row["rank"] = rank
        assert sum(r["trades"] for r in group) == len(part)
        np.testing.assert_allclose(math.fsum(r["sum_net_r"] for r in group), metrics(part)["sum_net_r"], atol=1e-9)
        rows.extend(group)
    ordering = lambda r: (r["timeframe_min"], -r["side"], r.get("rank", 0))
    rows.sort(key=ordering)
    totals.sort(key=ordering)
    output.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(output / "symbol_rankings.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(totals).to_csv(output / "totals.csv", index=False, encoding="utf-8-sig")
    metadata = {"generated_at": datetime.now(timezone.utc).isoformat(), "config": cfg,
                "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "sources": [{"path": str(p), "realpath": str(p.resolve()), "bytes": p.stat().st_size,
                             "mtime_ns": p.stat().st_mtime_ns} for p in paths],
                "audit": audit, "totals": totals, "rows": rows,
                "limitations": ["Descriptive full-history ranking, not a causal symbol-selection rule.",
                  "1H replays frozen entries under trail2; no admission replay after earlier exits.",
                  "1H preserves 19 source trades entered in December 2022; 15m starts in January 2023.",
                  "Cumulative R is arithmetic trade risk units, not account return.",
                  "Controls reuse separate source eligibility; unmatched groups have no comparator.",
                  "No minimum sample cutoff; infinite PF means no losing trade in the observed sample.",
                  "Binance archive; no new OKX forward data, funding or portfolio simulation."]}
    (output / "result.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"output": str(output), "rows": len(rows), "totals": totals, "audit": audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run(args.config, args.output)
