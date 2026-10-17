"""Measure bottom-20 symbol exclusion on the original V13.1-compatible ledgers.

Owner 2026-10-17 asks for the effect of removing the worst twenty symbols by
cumulative net R. Retrospective ranking uses the full outcome window and is
therefore descriptive selection, not an executable historical admission rule.
The chronological diagnostic selects only entries AND exits before 2025-01-01
and applies the fixed list to entries on/after that date. Boundary-crossing
trades enter neither chronological period. No features or trading parameters
change. Evaluate separate side lists and one combined list per timeframe.
Reuse original matched controls, without replacement or expanded eligibility;
later-period controls must also enter in the later period. Preserve fixed-entry
1H exit-replay limitations and the original 20bp cost contract.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.evaluation.spike_v131_symbol_summary import CONFIG, load, metrics, paired_metrics


def describe(frame, pairs):
    if frame.empty:
        return {"trades": 0, "sum_net_r": 0.0, "matched": 0}
    selected = pairs[pairs.trade_key.isin(frame.trade_key)]
    return metrics(frame) | paired_metrics(selected, len(frame))


def worst_twenty(frame):
    rows = [{"symbol": symbol, **metrics(part)} for symbol, part in frame.groupby("symbol")]
    rows.sort(key=lambda row: (row["sum_net_r"], row["symbol"]))
    if len(rows) < 20:
        raise ValueError("fewer than twenty ranked symbols")
    return [{"worst_rank": i, **row} for i, row in enumerate(rows[:20], 1)]


def comparison(frame, pairs, ranked):
    symbols = [row["symbol"] for row in ranked]
    mask = frame.symbol.isin(symbols)
    before = describe(frame, pairs)
    removed = describe(frame[mask], pairs)
    after = describe(frame[~mask], pairs)
    assert before["trades"] == removed["trades"] + after["trades"]
    assert before["matched"] == removed["matched"] + after["matched"]
    assert math.isclose(before["sum_net_r"], removed["sum_net_r"] + after["sum_net_r"], abs_tol=1e-9)
    return {"before": before, "removed": removed, "after": after,
            "removed_symbols_with_trades": int(frame[mask].symbol.nunique()),
            "delta_sum_net_r": after["sum_net_r"] - before["sum_net_r"]}


def run():
    for path in [Path(__file__), CONFIG, Path("yoyo/evaluation/spike_v131_symbol_summary.py")]:
        subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", str(path)], check=True)
    cfg = json.loads(CONFIG.read_text())
    root = Path(cfg["output"])
    outputs = [root / ("bottom20_20261017" + suffix) for suffix in [".json", "_comparisons.csv", "_symbols.csv"]]
    if any(path.exists() for path in outputs):
        raise ValueError("refusing to overwrite existing bottom-20 results")
    trades, pairs, audit, paths = load(cfg)
    for col in ["entry_time", "exit_time"]:
        trades[col] = pd.to_datetime(trades[col], utc=True)
    times = []
    for minutes, source, filename in [(15, cfg["source_15m"], "matched_controls.csv.gz"),
                                       (60, cfg["source_1h"], "trades.csv.gz")]:
        fields = ["timeframe_min", "trade_key", "entry_time"]
        if minutes == 60:
            fields += ["cohort", "version", "policy"]
        frame = pd.read_csv(Path(source) / filename, usecols=fields)
        if minutes == 60:
            frame = frame[frame.cohort.eq("control") & frame.version.eq("retest") &
                          frame.timeframe_min.eq(60) & frame.policy.eq("trail2")]
        times.append(frame[["timeframe_min", "trade_key", "entry_time"]])
    pairs = pairs.merge(pd.concat(times).rename(columns={"entry_time": "control_entry_time"}),
                        on=["timeframe_min", "trade_key"], validate="one_to_one")
    pairs["control_entry_time"] = pd.to_datetime(pairs.control_entry_time, utc=True)
    cut = pd.Timestamp("2025-01-01T00:00:00Z")
    groups, comparisons, symbol_rows = [], [], []
    for minutes in [15, 60]:
        for side in [1, -1, 0]:
            part = trades[trades.timeframe_min.eq(minutes)]
            paired = pairs[pairs.timeframe_min.eq(minutes)]
            if side:
                part = part[part.side.eq(side)]
                paired = paired[paired.side.eq(side)]
            label = {"timeframe_min": minutes, "timeframe": "15m" if minutes == 15 else "1H",
                     "side": side, "direction": {1: "多头", -1: "空头", 0: "多空合并"}[side]}
            early = part[(part.entry_time < cut) & (part.exit_time < cut)]
            later = part[part.entry_time >= cut]
            cross = part[(part.entry_time < cut) & (part.exit_time >= cut)]
            assert len(part) == len(early) + len(later) + len(cross)
            all_worst, early_worst = worst_twenty(part), worst_twenty(early)
            full = comparison(part, paired, all_worst)
            chronological = comparison(later, paired[paired.control_entry_time >= cut], early_worst)
            groups.append(label | {"full_history_bottom20": all_worst, "full_history": full,
                "chronology": {"early_closed": len(early), "later_closed": len(later),
                    "cross_boundary_excluded": len(cross), "early_bottom20": early_worst,
                    "later": chronological}})
            for scope, values, ranks in [("full_history_retrospective", full, all_worst),
                                          ("early_rank_later_evaluate", chronological, early_worst)]:
                for state in ["before", "removed", "after"]:
                    comparisons.append(label | {"scope": scope, "state": state} | values[state])
                symbol_rows.extend(label | {"scope": scope} | row for row in ranks)
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "split": str(cut),
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "definition": "Rank symbols by sum(net_r) ascending, symbol ascending for ties; exclude exactly 20.",
        "selection_count": 20, "round_trip_cost": cfg["round_trip_cost"], "audit": audit,
        "sources": [{"path": str(p), "realpath": str(p.resolve()), "bytes": p.stat().st_size,
                     "mtime_ns": p.stat().st_mtime_ns} for p in paths],
        "limitations": ["Full-history exclusions use future outcomes and are descriptive only.",
            "Chronological split reuses already observed history, not blind or forward validation.",
            "No new serial replay, portfolio sizing or account-return calculation.",
            "1H preserves fixed original entries and 19 December-2022 entries.",
            "Matched control coverage is sparse; do not generalize paired excess to unmatched trades."],
        "groups": groups}
    outputs[0].write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    pd.DataFrame(comparisons).to_csv(outputs[1], index=False, encoding="utf-8-sig")
    pd.DataFrame(symbol_rows).to_csv(outputs[2], index=False, encoding="utf-8-sig")
    print(json.dumps({"outputs": [str(p) for p in outputs], "groups": len(groups)}, ensure_ascii=False))


if __name__ == "__main__":
    run()
