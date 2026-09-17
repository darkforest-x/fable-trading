"""Asset-trim concentration diagnostic for SPIKE V1 and V9.

Owner asked what is left after the best and worst few assets are removed. This
reads only already-published per-trade ledgers -- no price replay, no strategy
or threshold change, no new holdout scoring -- and therefore measures where the
realised net R of each system actually lives.

Three things are produced per system:

1. per-asset realised net R, ranked;
2. a trim curve that drops the top-k and bottom-k assets by that ranking and
   recomputes closed count / net R / R per trade / PF, plus, for V9, the frozen
   matched random-entry control on the surviving trades;
3. a null control, because dropping the best asset always lowers the total: 2000
   random 2-asset drops matched on combined closed-trade count (+-25%) say how
   far the observed drop sits outside what any comparable pair would do.

The trim ranking uses realised outcomes, so it is a post-hoc decomposition and
never an executable selection rule. The V9 control machinery is imported from
the published report module and the untrimmed row must reproduce the published
summary.csv values before any trimmed row is written.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, periods, SPLIT
from yoyo.evaluation.spike_v9_full_report import control_metrics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V1_LEDGER = ROOT / "experiments/active/exp-spike-v1-v8-be05-20260914-v1/delivery_v1/all_outcomes.csv.gz"
V9_DIR = ROOT / "experiments/active/exp-spike-v9-full-backtest-20260915-v1/statistics/full_v1"
SEED, REPS, TRIM_K, COUNT_TOLERANCE = 91509, 2000, (0, 1, 3, 5, 10), 0.25


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_v1() -> pd.DataFrame:
    """Native V1 rows carry `symbol` but no `asset`; recover it from the file itself."""
    raw = pd.read_csv(V1_LEDGER, parse_dates=["entry_time", "exit_time"],
                      usecols=["asset", "symbol", "net_r", "censored", "entry_time",
                               "exit_time", "system", "mode", "rule"])
    mapping = raw.dropna(subset=["asset"]).drop_duplicates("symbol").set_index("symbol").asset
    recovered = raw["asset"].fillna(raw["symbol"].map(mapping))
    if recovered.isna().any():
        raise ValueError("unmapped symbol in the V1 ledger")
    raw["asset"] = recovered
    return raw[(raw.rule == "baseline") & (raw["mode"] == "fixed")]


def plain_metrics(table: pd.DataFrame) -> dict:
    closed = table[~table.censored.astype(bool)]
    positive = closed.net_r[closed.net_r > 0].sum()
    negative = -closed.net_r[closed.net_r < 0].sum()
    return {"closed": len(closed), "net_r": float(closed.net_r.sum()),
            "mean_r": float(closed.net_r.mean()), "pf_r": float(positive / negative)}


def ranking(table: pd.DataFrame) -> pd.DataFrame:
    closed = table[~table.censored.astype(bool)]
    return closed.groupby("asset").net_r.agg(closed="size", net_r="sum").sort_values("net_r")


def verify_v9_parity(trades: pd.DataFrame, controls: pd.DataFrame) -> None:
    published = pd.read_csv(V9_DIR / "summary.csv")
    published = published[(published.arm == "v9") & (published.group == "all")].set_index("period")
    for period, part in periods(trades):
        got, control, want = metrics(part), control_metrics(part, controls, period), published.loc[period]
        if (abs(got["total_r"] - want.total_r) > 1e-6
                or control["matched_pairs"] != want.matched_pairs
                or abs(control["paired_excess_r"] - want.paired_excess_r) > 1e-9
                or abs(control["p_month_signflip"] - want.p_month_signflip) > 1e-12):
            raise ValueError(f"untrimmed V9 {period} does not reproduce the published summary")


def trim_curve(label: str, table: pd.DataFrame, rank: pd.DataFrame, controls) -> list[dict]:
    rows = []
    for k in TRIM_K:
        dropped = set(rank.head(k).index) | set(rank.tail(k).index) if k else set()
        kept = table[~table.asset.isin(dropped)]
        for period, part in [("full", kept), ("later", kept[kept.entry_time >= SPLIT])]:
            row = {"system": label, "period": period, "trim_k": k,
                   "assets_kept": len(rank) - len(dropped), **plain_metrics(part)}
            if controls is not None:
                control = control_metrics(part, controls, period)
                row |= {"matched_pairs": control["matched_pairs"],
                        "paired_excess_r": control["paired_excess_r"],
                        "p_month_signflip": control["p_month_signflip"]}
            rows.append(row)
    return rows


def null_drop(label: str, rank: pd.DataFrame) -> dict:
    """Dropping the extremes always lowers the total; ask whether it lowers it unusually."""
    total = float(rank.net_r.sum())
    worst, best = rank.index[0], rank.index[-1]
    removed_trades = int(rank.loc[[worst, best], "closed"].sum())
    observed = total - float(rank.loc[[worst, best], "net_r"].sum())
    pool = rank.drop(index=[worst, best])
    generator, draws, attempts = np.random.default_rng(SEED), [], 0
    while len(draws) < REPS and attempts < REPS * 400:
        attempts += 1
        pick = generator.choice(pool.index, size=2, replace=False)
        count = pool.loc[pick, "closed"].sum()
        if abs(count - removed_trades) <= COUNT_TOLERANCE * removed_trades:
            draws.append(total - float(pool.loc[pick, "net_r"].sum()))
    if len(draws) < REPS:
        raise ValueError(f"{label}: only {len(draws)} count-matched null draws available")
    draws = np.asarray(draws)
    return {"system": label, "total_net_r": total, "removed_best": best, "removed_worst": worst,
            "removed_trades": removed_trades, "observed_net_r": observed,
            "null_median": float(np.median(draws)), "null_p2_5": float(np.quantile(draws, .025)),
            "null_p97_5": float(np.quantile(draws, .975)),
            "fraction_at_or_below_observed": float((draws <= observed).mean()), "draws": len(draws)}


def main() -> None:
    v1 = load_v1()
    v9 = pd.read_csv(V9_DIR / "trades.csv.gz", parse_dates=["entry_time", "exit_time"])
    v9 = v9[v9.arm == "v9"]
    controls = pd.read_csv(V9_DIR / "controls.csv.gz",
                           parse_dates=["entry_time", "control_entry_time", "control_exit_time"])
    verify_v9_parity(v9, controls)

    systems = {"v1_native": (v1[v1.system == "v1_native"], None),
               "v1_common": (v1[v1.system == "v1_common"], None),
               "v9": (v9, controls)}
    ranks, curves, nulls = {}, [], []
    for label, (table, control) in systems.items():
        rank = ranking(table)
        ranks[label] = rank.assign(system=label).reset_index()
        curves += trim_curve(label, table, rank, control)
        nulls.append(null_drop(label, rank))

    out = HERE / "results"
    pd.concat(ranks.values())[["system", "asset", "closed", "net_r"]].to_csv(out / "per_asset_net_r.csv", index=False)
    pd.DataFrame(curves).to_csv(out / "trim_curve.csv", index=False)
    pd.DataFrame(nulls).to_csv(out / "null_drop.csv", index=False)
    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                         capture_output=True, text=True, check=True).stdout.strip(),
        "builder_sha256": digest(Path(__file__)),
        "inputs": {str(p.relative_to(ROOT)): digest(p) for p in
                   [V1_LEDGER, V9_DIR / "trades.csv.gz", V9_DIR / "controls.csv.gz", V9_DIR / "summary.csv"]},
        "seed": SEED, "reps": REPS, "trim_k": list(TRIM_K), "count_tolerance": COUNT_TOLERANCE,
        "replayed_prices": False, "configuration_changed": False, "holdout_consumed": 0,
        "ranking": "post-hoc, by realised net R; not an executable selection rule",
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(pd.DataFrame(curves).to_string(index=False))
    print(pd.DataFrame(nulls).to_string(index=False))


if __name__ == "__main__":
    main()
