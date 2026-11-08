"""Attribute equal original notional to two archived native E20 target legs on each identical price path."""
import json
import pandas as pd

from yoyo.evaluation.live_wick_score_october import metrics
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.range_framework import ROOT, dump


OUT = ROOT / "experiments/active/exp-live-wick-two-target-20261109-v1"


def main():
    committed("yoyo/evaluation/live_wick_two_target.py", str((OUT / "config.json").relative_to(ROOT)))
    cfg = json.loads((OUT / "config.json").read_text())
    cohort = pd.read_csv(ROOT / cfg["cohort"]).set_index("event")
    legs = []
    for path in ["ohlc", "olhc"]:
        for file in (ROOT / cfg["fills"] / path / "symbols").glob("*/trades.csv.gz"):
            data = pd.read_csv(file, usecols=["trade_id", "entry", "side", "entry_i", "reason", "net_notional_bp", "variant"])
            data = data[data.variant == "E20"].copy()
            parts = data.trade_id.str.split(":")
            data["event"] = parts.str[:3].str.join(":")
            data["target"] = parts.str[4]
            data["path"] = path
            data["net"] = data.net_notional_bp - 10 - 10 * (data.reason != 4)
            legs.append(data)
    common = pd.concat(legs).merge(cohort[["entry_t", "net_bp"]], left_on="event", right_index=True)
    groups = common.groupby("event")
    if (groups[["entry", "side", "entry_i"]].nunique() != 1).any().any():
        raise ValueError("Target legs disagree on entry")
    pivot = common.pivot(index="event", columns=["path", "target"], values="net")
    if len(pivot) != 398 or pivot.isna().any().any() or pivot.shape[1] != 4:
        raise ValueError("Expected all four legs for each of 398 original entries")
    near = pivot.xs("near_monday_edge", level="target", axis=1)
    mid = pivot.xs("range_0.5", level="target", axis=1)
    paired = pd.DataFrame({"near": near.min(axis=1), "half_mid": ((near + mid) / 2).min(axis=1),
                           "midpoint": mid.min(axis=1)}).join(cohort[["entry_t", "net_bp"]])
    if (paired.near - paired.net_bp).abs().max() > 1e-6:
        raise ValueError("Near-leg net does not match the frozen E20 reference")
    year = pd.to_datetime(paired.entry_t, utc=True).dt.year
    summary = {}
    for period, rows in [("all", paired), ("2020_24", paired[year < 2025]), ("2025", paired[year == 2025]),
                         ("2026", paired[year == 2026]), ("since_2025", paired[year >= 2025])]:
        summary[period] = {col: metrics(rows[col]) for col in ["near", "half_mid", "midpoint"]}
    paired.to_csv(OUT / "paired.csv.gz")
    dump(OUT / "summary.json", summary)


if __name__ == "__main__":
    main()
