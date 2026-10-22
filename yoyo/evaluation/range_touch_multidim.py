"""Describe V2.37's unchanged V2.31 touch-highlight ledger without replay.

Source: owner request on 2026-10-22 and the frozen five-year Nautilus results.
Entry-time dimensions use side, original key, entry clock, original stop risk,
causal ATR bucket and touch/arrow timestamps, all known by admission. Exit,
holding duration and peak R are explicitly outcome-only descriptive dimensions.
No new signal, stop, cost, execution, model or production parameter is applied.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "experiments/active/exp-range-v231-fiveyear-20261021-v1"
DEST = ROOT / "experiments/active/exp-range-touch-multidim-20261022-v1"
ENTRY_DIMENSIONS = ["year", "side", "key_group", "weekday_ny", "hour4_shanghai",
                    "risk_band", "bucket", "touch_lag", "month_shanghai"]
OUTCOME_DIMENSIONS = ["exit_reason", "holding_band"]


def read_csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def load(symbol):
    """Join exactly one existing control to each original signal occurrence."""
    folder = SOURCE / symbol
    signals = read_csv(folder / "signal_details.csv")
    trades = read_csv(folder / "trades.csv")
    assert signals.pair_id.is_unique
    controls = trades[trades.population.eq("random")].copy()
    assert controls.pair_id.is_unique and len(controls) == len(signals)
    data = signals.merge(controls, on="pair_id", suffixes=("", "_random"),
                         how="outer", validate="one_to_one", indicator=True)
    assert data._merge.eq("both").all()
    data = data.drop(columns="_merge")
    for col in ["side", "week", "segment", "bucket"]:
        assert data[col].eq(data[f"{col}_random"]).all(), col
    for suffix in ["", "_random"]:
        entry, stop, risk = (data[c + suffix] for c in ["entry", "stop", "risk"])
        np.testing.assert_allclose(risk, data["side" + suffix] * (entry - stop), atol=1e-7)
        np.testing.assert_allclose(data["fee_r" + suffix], .002 * entry / risk, atol=1e-7)
        np.testing.assert_allclose(data["net_r" + suffix],
                                   data["gross_r" + suffix] - data["fee_r" + suffix], atol=1e-8)
        np.testing.assert_allclose(data["net_notional_bp" + suffix],
                                   data["net_r" + suffix] * risk / entry * 10000, atol=1e-7)
    assert data.reason.isin([1, 7]).all() and data.reason_random.isin([1, 7]).all()
    entry = pd.to_datetime(data.entry_time, utc=True)
    exit_at = pd.to_datetime(data.exit_time, utc=True)
    start = pd.Timestamp("2021-10-21T00:00:00+08:00")
    bounds = [start + pd.DateOffset(years=i) for i in range(6)]
    assert ((entry >= bounds[0]) & (entry < bounds[-1])).all()
    data["symbol"] = symbol
    data["year"] = sum((entry >= boundary).astype(int) for boundary in bounds[1:-1])
    local = entry.dt.tz_convert("Asia/Shanghai")
    data["entry_shanghai"] = local.astype(str)
    data["exit_shanghai"] = exit_at.dt.tz_convert("Asia/Shanghai").astype(str)
    data["weekday_ny"] = entry.dt.tz_convert("America/New_York").dt.dayofweek
    assert data.weekday_ny.isin([1, 2, 3, 4]).all()
    data["hour4_shanghai"] = local.dt.hour // 4 * 4
    data["month_shanghai"] = local.dt.strftime("%Y-%m")
    data["risk_percent"] = data.risk / data.entry * 100
    data["risk_band"] = pd.cut(data.risk_percent, [0, .1, .2, .4, .8, np.inf],
                               labels=["<=0.1%", "0.1-0.2%", "0.2-0.4%", "0.4-0.8%", ">0.8%"])
    data["touch_lag"] = ((pd.to_datetime(data.touch_time, utc=True) -
                          pd.to_datetime(data.signal_time, utc=True)).dt.total_seconds() / 900).astype(int)
    assert data.touch_lag.between(-2, 2).all()
    known = pd.concat([pd.to_datetime(data.touch_time, utc=True),
                       pd.to_datetime(data.signal_time, utc=True)], axis=1).max(axis=1)
    assert entry.eq(known + pd.Timedelta(minutes=15)).all()
    data["holding_minutes"] = (exit_at - entry).dt.total_seconds() / 60
    data["holding_band"] = pd.cut(data.holding_minutes, [0, 15, 30, 60, 120, np.inf],
                                  labels=["<=15m", "15-30m", "30-60m", "60-120m", ">120m"], include_lowest=True)
    data["exit_reason"] = data.reason.map({1: "initial_stop", 7: "confirmed_pivot_close"})
    assert data[ENTRY_DIMENSIONS + OUTCOME_DIMENSIONS].notna().all().all()
    return data


def metrics(data):
    result = {"n": len(data)}
    for suffix, prefix in [("", ""), ("_random", "random_")]:
        r = data["net_r" + suffix]
        pos, neg = r[r > 1e-9], r[r < -1e-9]
        result.update({prefix + "wins": int(len(pos)), prefix + "win_rate": float((r > 1e-9).mean()),
                       prefix + "gross_r": float(data["gross_r" + suffix].sum()),
                       prefix + "fee_r": float(data["fee_r" + suffix].sum()),
                       prefix + "net_r": float(r.sum()), prefix + "mean_net_r": float(r.mean()),
                       prefix + "mean_net_bp": float(data["net_notional_bp" + suffix].mean()),
                       prefix + "pf": float(pos.sum() / -neg.sum()) if len(neg) else None})
    result.update(excess_r=result["net_r"] - result["random_net_r"],
                  median_risk_percent=float(data.risk_percent.median()),
                  mean_cost_r=float(data.fee_r.mean()), median_cost_r=float(data.fee_r.median()),
                  median_hold_minutes=float(data.holding_minutes.median()),
                  median_peak_r=float(data.peak_r.median()),
                  initial_stops=int(data.reason.eq(1).sum()),
                  gross_wins=int(data.gross_r.gt(1e-9).sum()),
                  cost_erased_wins=int((data.gross_r.gt(1e-9) & data.net_r.le(1e-9)).sum()),
                  best_r=float(data.net_r.max()), worst_r=float(data.net_r.min()),
                  net_r_without_best=float(data.net_r.sum() - data.net_r.max()),
                  net_r_without_best5=float(data.net_r.sum() - data.net_r.nlargest(5).sum()))
    for threshold in [1, 2, 3, 5]:
        reached = data.peak_r.ge(threshold - 1e-9)
        result[f"peak_ge{threshold}_n"] = int(reached.sum())
        result[f"peak_ge{threshold}_then_netloss_n"] = int((reached & data.net_r.lt(-1e-9)).sum())
        result[f"random_peak_ge{threshold}_n"] = int(data.peak_r_random.ge(threshold - 1e-9).sum())
    return result


def tail_stats(data):
    """Cash-only independent-R diagnostics, not an account equity curve."""
    ordered = data.sort_values(["exit_time", "pair_id"])
    # Aggregate simultaneous closes to avoid an arbitrary within-timestamp drawdown.
    grouped = ordered.groupby("exit_time").net_r.sum().to_numpy()
    curve = np.r_[0., grouped.cumsum()]
    flags = data.sort_values(["entry_time", "pair_id"]).net_r.lt(-1e-9).to_numpy()
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return dict(cash_only_drawdown_r=float((np.maximum.accumulate(curve) - curve).max()),
                longest_entry_order_losing_streak=longest,
                risk_percent_quantiles=data.risk_percent.quantile([.1, .25, .5, .75, .9]).to_dict(),
                net_r_quantiles=data.net_r.quantile([0, .1, .5, .9, 1]).to_dict(),
                cost_exceeds_1r=int(data.fee_r.gt(1).sum()),
                unique_touch_events=int(data[["touch_time", "side"]].drop_duplicates().shape[0]),
                mean_winner_r=float(data.loc[data.net_r.gt(0), "net_r"].mean()),
                mean_loser_r=float(data.loc[data.net_r.lt(0), "net_r"].mean()),
                median_pivot_reference_r=float(data.loc[data.reason.eq(7), "pivot_reference_r"].median()),
                median_pivot_fill_gross_r=float(data.loc[data.reason.eq(7), "gross_r"].median()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEST)
    args = parser.parse_args()
    dest = args.output
    dest.mkdir(parents=True, exist_ok=True)
    old = json.loads((SOURCE / "summary.json").read_text())
    marker = "// === V2.15 level-touch opportunity engine"
    pine = [ROOT / f"yoyo/evaluation/pine/monday_range_v2_{v}.pine" for v in [31, 37]]
    assert pine[0].read_text().split(marker)[1] == pine[1].read_text().split(marker)[1]
    overall, grouped, stability, details = [], [], [], []
    for symbol in ["BTC", "ETH"]:
        data = load(symbol)
        original = next(row for row in old["overall"] if row["symbol"] == symbol)
        overall_row = dict(symbol=symbol, **metrics(data), **tail_stats(data))
        for key in ["n", "wins", "gross_r", "fee_r", "net_r", "random_net_r", "pf"]:
            np.testing.assert_allclose(overall_row[key], original[key], atol=1e-7)
        overall_row["cached_inference"] = {k: original[k] for k in
                                             ["p_one_sided", "weekly_bootstrap_total_r_95ci", "wilson_95ci", "weeks"]}
        overall.append(overall_row)
        for dimension in ENTRY_DIMENSIONS + OUTCOME_DIMENSIONS:
            parts = []
            for value, part in data.groupby(dimension, observed=True, sort=True):
                row = dict(symbol=symbol, dimension=dimension, value=str(value),
                           outcome_only=dimension in OUTCOME_DIMENSIONS, **metrics(part))
                parts.append(row)
            assert sum(row["n"] for row in parts) == len(data)
            np.testing.assert_allclose(sum(row["net_r"] for row in parts), data.net_r.sum(), atol=1e-7)
            np.testing.assert_allclose(sum(row["random_net_r"] for row in parts), data.net_r_random.sum(), atol=1e-7)
            grouped.extend(parts)
        # A diagnostic chronological check, not an untouched validation dataset.
        for dimension in ["side", "key_group", "weekday_ny", "hour4_shanghai", "risk_band", "touch_lag"]:
            earlier, later = data[data.year < 3], data[data.year >= 3]
            choices = [(str(v), part) for v, part in earlier.groupby(dimension, observed=True) if len(part) >= 30]
            if choices:
                value, part = max(choices, key=lambda item: item[1].net_r.mean())
                check = later[later[dimension].astype(str).eq(value)]
                if len(check):
                    stability.append(dict(symbol=symbol, dimension=dimension, selected=value,
                                          first_three_years=metrics(part), last_two_years=metrics(check)))
        details.append(data)
        data.to_csv(dest / f"{symbol}_paired_details.csv", index=False)
    combined = pd.concat(details)
    combined.to_csv(dest / "all_paired_details.csv", index=False)
    export_names = {"symbol": "币种", "pair_id": "配对编号", "side": "方向_1多负1空",
                    "entry_shanghai": "入场时间_北京时间", "entry": "入场价格", "stop": "初始止损",
                    "risk_percent": "止损距离_百分比", "key_group": "触价关键位", "level": "关键位价格",
                    "touch_time": "触价K开盘时间_带时区", "signal_time": "勾叉K开盘时间_带时区",
                    "touch_lag": "触价相对勾叉_15m根数", "exit_shanghai": "退出时间_北京时间",
                    "exit_price": "退出价格", "exit_reason": "退出原因", "holding_minutes": "持仓分钟",
                    "peak_r": "原版持仓最大浮盈_R", "gross_r": "毛收益_R", "fee_r": "费用_R",
                    "net_r": "净收益_R", "net_notional_bp": "名义净收益_bp",
                    "pivot_reference_r": "回标极值参考_R", "entry_time_random": "随机入场时间_UTC",
                    "net_r_random": "配对随机净收益_R", "net_notional_bp_random": "配对随机名义净收益_bp"}
    combined[list(export_names)].rename(columns=export_names).to_csv(
        dest / "trade_details_zh.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(grouped).to_csv(dest / "multidimensional.csv", index=False)
    result = dict(source=str(SOURCE.relative_to(ROOT)), source_start=old["start"], source_end=old["end"],
                  source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  wf_source_exactly_unchanged=True, rows=1740, signal_occurrences=870, overall=overall,
                  chronology=stability, groups=len(grouped),
                  control_grouping="Each signal stratum selects its original paired random. Controls are not rematched by hour/key/touch lag.",
                  inference="Reuse original full-period weekly-block tests. Subgroup cuts are descriptive; no optimized trading rule is adopted.",
                  outcome_only_dimensions=OUTCOME_DIMENSIONS,
                  limitations="Known history; repeated touch events remain occurrences. PeakR ends at original exit and excludes stop-bar favorable extremes. Cash R drawdown excludes open MTM and shared capital. No fees, prices or exits changed.",
                  training_eligible=False, production_eligible=False)
    (dest / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    receipt = dict(ledger_economic_rows_checked=1740, paired_rows_checked=870,
                   all_dimensions_partition_exactly=True, original_totals_reconciled=True,
                   no_backtest_replay=True, no_content_hashes=True,
                   reproduction=".venv/bin/python -m yoyo.evaluation.range_touch_multidim",
                   files=[dict(path=str(p.relative_to(ROOT)), bytes=p.stat().st_size)
                          for p in sorted(dest.iterdir()) if p.is_file()])
    (dest / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(dict(output=str(dest), signal_occurrences=870, groups=len(grouped))))


if __name__ == "__main__":
    main()
