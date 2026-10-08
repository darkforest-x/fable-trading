"""Compact per-event research history for the workbench 全市场异动 menu.

Owner 2026-10-08: "给这个单独做个菜单吧 然后我们再去精细化研究". The menu shows the
research events next to the live observer (yoyo/monitor/market_sync.py), so each event
of exp-market-sync-shock-20261008-v2 can be browsed and re-filtered by config, timeframe,
direction and year without rerunning the study.

Source: the v2 builder's local outputs ``events.csv`` and ``trades.csv.gz`` (written by
yoyo/evaluation/market_sync_shock_v2.py at the commit in its receipt). Nothing is
recomputed here: rows are joined on (config, kind, minutes, time) and reshaped. Only 30m
and 1H are exported - every v2 cell positive after cost in both periods is 30m or 1H.

Values are net of the 0.2% round trip, in basis points rounded to 0.1bp. ``ctrl`` is the
mean net of the 20 paired random entries (same month, same BTC-volatility tercile, same
side and exit) that v2 drew for that event.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

RESULTS = Path("experiments/active/exp-market-sync-shock-20261008-v2/results_v2")
MINUTES = (30, 60)
EXITS = ("time_1h", "time_4h", "time_12h", "time_24h", "trend")
INSTRUMENTS = ("eth", "btc", "alts")


def bp(value: float) -> float | None:
    return None if pd.isna(value) else round(float(value) * 1e4, 1)


def build(results: Path) -> dict:
    receipt = json.loads((results / "receipt.json").read_text())
    events = pd.read_csv(results / "events.csv")
    trades = pd.read_csv(results / "trades.csv.gz")
    events = events.loc[events.minutes.isin(MINUTES)]
    trades = trades.loc[trades.minutes.isin(MINUTES)]
    key = ["config", "kind", "minutes", "time"]
    wide = trades.pivot_table(index=key, columns=["exit", "instrument"], values=["net", "control_net"],
                              aggfunc="first")
    groups: dict[str, list] = {}
    for row in events.itertuples(index=False):
        k = (row.config, row.kind, row.minutes, row.time)
        net, ctrl = [], []
        for exit_ in EXITS:
            for inst in INSTRUMENTS:
                net.append(bp(wide.at[k, ("net", exit_, inst)]) if k in wide.index else None)
                ctrl.append(bp(wide.at[k, ("control_net", exit_, inst)]) if k in wide.index else None)
        config = row.config.rsplit("_", 1)[0]
        group = f"{row.minutes}|{row.kind}|{config}"
        time_ms = int(pd.Timestamp(row.time).value // 1_000_000)
        groups.setdefault(group, []).append([time_ms, int(row.side), round(float(row.btc_z), 2),
                                             round(float(row.eth_z), 2), round(float(row.breadth), 3), net, ctrl])
    for rows in groups.values():
        rows.sort(key=lambda r: r[0])
    return {
        "experiment_id": receipt["experiment_id"],
        "source_commit": receipt["source_commit"],
        "source_generated_at": receipt["generated_at"],
        "unit": "bp net of 0.2% round trip",
        "exits": list(EXITS),
        "instruments": list(INSTRUMENTS),
        "columns": ["time_ms", "side", "btc_z", "eth_z", "breadth", "net[exit x instrument]", "ctrl[exit x instrument]"],
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--output", type=Path, default=RESULTS / "menu_history.json.gz")
    args = parser.parse_args()
    payload = build(args.results)
    with gzip.open(args.output, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
    print(args.output, sum(len(v) for v in payload["groups"].values()), "events in", len(payload["groups"]), "groups")


if __name__ == "__main__":
    main()
