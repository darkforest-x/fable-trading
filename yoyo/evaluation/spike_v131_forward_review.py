"""Describe saved V13.1 reference outcomes; never replay candles or create fills.

Owner 2026-10-31 requested a continue/pause review. Inputs are the complete live
signal ledger and the existing observed-entry paper export. Grouping uses saved
entry/exit timestamps; no predictive features are constructed. The original
20bp R is preserved. A separate fee sensitivity uses 2026-10-23 Lv1 taker 5bp
on each saved entry/exit notional; it is not an executable backtest. Bootstrap
resamples entry-day clusters (seed fixed), retaining cross-symbol dependence
within a day; cross-day dependence and this short sample limit inference.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
from urllib.request import urlopen

TZ = timezone(timedelta(hours=8))
RUN = "dc0c7e42add44beb902cafb33ea48424"
SEED = 20261031


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat()


def summary(rows, key="net_r"):
    values = [r[key] for r in rows]
    positive = sum(v for v in values if v > 0)
    negative = -sum(v for v in values if v < 0)
    exits = defaultdict(float)
    for row in rows:
        exits[row["exit_ms"]] += row[key]
    cumulative = peak = drawdown = 0.
    peak_ms = None
    for at, value in sorted(exits.items()):
        cumulative += value
        if cumulative > peak:
            peak, peak_ms = cumulative, at
        drawdown = max(drawdown, peak - cumulative)
    return dict(n=len(values), net_r=sum(values), win_rate=sum(v > 0 for v in values) / len(values) if values else None,
                mean_r=statistics.mean(values) if values else None,
                median_r=statistics.median(values) if values else None,
                pf=positive / negative if negative else None,
                avg_win=positive / sum(v > 0 for v in values) if positive else None,
                avg_loss=-negative / sum(v < 0 for v in values) if negative else None,
                max_drawdown_r=drawdown, peak_r=peak,
                peak_at=stamp(peak_ms) if peak_ms else None)


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return {k: summary(v) for k, v in sorted(groups.items())}


def cluster_ci(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row["entry_day"]].append(row[key])
    blocks = [(sum(v), len(v)) for v in groups.values()]
    rng = random.Random(SEED)
    estimates = []
    for _ in range(10000):
        selected = rng.choices(blocks, k=len(blocks))
        estimates.append(sum(x[0] for x in selected) / sum(x[1] for x in selected))
    estimates.sort()
    return dict(entry_days=len(blocks), resamples=10000, seed=SEED,
                mean_r_ci95=[estimates[249], estimates[9749]],
                scope="exploratory_entry_day_cluster_percentile_cross_day_dependence_not_controlled")


def analyze(ledger, paper):
    assert len(ledger["items"]) == ledger["total"] and not ledger["has_more"]
    assert len({r["id"] for r in ledger["items"]}) == ledger["total"]
    instruments = paper["spec"]["instrument_snapshot"]["instruments"]
    rows, errors = [], []
    for event in ledger["items"]:
        p = event["performance"]
        if p.get("exit_r") is None:
            continue
        entry, exit_, risk = p["entry_price"], p["exit_price"], p["initial_risk"]
        assert risk > 0 and p["round_trip_cost"] == .002
        gross = (exit_ - entry) * (1 if event["side"] == "long" else -1) / risk
        net = float(p["exit_r"])
        errors.append(abs(gross - .002 * entry / risk - net))
        rows.append(dict(id=event["id"], symbol=event["symbol"], timeframe=event["timeframe"],
                         side=event["side"], net_r=net, gross_r=gross,
                         taker_fee_sensitivity_r=gross - .0005 * (entry + exit_) / risk,
                         cost_r=.002 * entry / risk, exit_ms=p["exit_time_ms"],
                         exit_day=stamp(p["exit_time_ms"])[:10], entry_day=stamp(p["entry_time_ms"])[:10],
                         exit_reason=p["exit_reason"], peak_r=p["peak_r"],
                         observation_delay_min=(event["detected_at_ms"] - event["bar_close_ms"]) / 60000,
                         asset_class=instruments.get(event["symbol"], {}).get("asset_class", "unknown")))
    assert max(errors) < 1e-8
    assert len(rows) == ledger["stats"]["measured_closed"]
    assert math.isclose(sum(r["net_r"] for r in rows), ledger["stats"]["realized_r"], abs_tol=1e-8)
    ordered = sorted(rows, key=lambda r: r["net_r"], reverse=True)
    recent = [r for r in rows if r["exit_ms"] >= ledger["as_of_ms"] - 7 * 86400000]
    output = dict(as_of=stamp(ledger["as_of_ms"]), api_stats=ledger["stats"],
                  versions=dict(Counter(r["strategy_version"] for r in ledger["items"])),
                  all_closed=summary(rows), last_7_days_by_exit=summary(recent),
                  by_timeframe=grouped(rows, "timeframe"), by_side=grouped(rows, "side"),
                  by_entry_day=grouped(rows, "entry_day"), by_exit_day=grouped(rows, "exit_day"),
                  by_exit_reason=grouped(rows, "exit_reason"),
                  by_asset_class_at_registration=grouped(rows, "asset_class"),
                  top_5=ordered[:5], bottom_5=ordered[-5:],
                  excluding_best_5=summary(ordered[5:]),
                  fee_sensitivity=dict(taker_per_leg=.0005, rate_read_date="2026-10-23",
                      all_closed=summary(rows, "taker_fee_sensitivity_r"),
                      last_7_days_by_exit=summary(recent, "taker_fee_sensitivity_r"),
                      ci=cluster_ci(rows, "taker_fee_sensitivity_r")),
                  ci=cluster_ci(rows, "net_r"), max_r_arithmetic_error=max(errors),
                  paper=dict(id=paper["id"], status=paper["status"], metrics=paper["metrics"],
                             error=paper["error"], transitions=[dict(at=stamp(t["at_ms"]), kind=t["kind"])
                                                              for t in paper["transitions"]]),
                  limitations=["Saved next-open reference R, not executable fills or account equity",
                               "No contemporaneous matched random control",
                               "Mixed entry days and still-open outcomes imply censoring",
                               "Asset class uses the 2026-10-16 frozen registration snapshot",
                               "Fee sensitivity retains saved reference prices; no funding/slippage"])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    paths = {"ledger": "/api/signal-ledger?source=live&confirmation=retest&period=all&limit=2000",
             "paper": f"/api/research/paper/runs/{RUN}/export"}
    inputs = {}
    for name, path in paths.items():
        if args.inputs:
            inputs[name] = json.loads((args.inputs / (name + ".json")).read_text())
        else:
            with urlopen(args.base + path, timeout=30) as response:
                inputs[name] = json.load(response)
        (args.output / (name + ".json")).write_text(json.dumps(inputs[name], ensure_ascii=False))
    result = analyze(inputs["ledger"], inputs["paper"])
    result["builder_commit"] = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", __file__], text=True).strip()
    result["input_routes"] = paths
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: result[k] for k in ("as_of", "all_closed", "last_7_days_by_exit", "fee_sensitivity", "ci", "paper")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
