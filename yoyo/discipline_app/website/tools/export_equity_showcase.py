#!/usr/bin/env python3
"""Export three frozen historical strategy equity paths for the public website.

This is a projection only. It reads saved strategy-booklet histories and the
frozen ETH 4h equity CSV; it does not replay signals or simulate fills.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[4]
BOOK = ROOT / "yoyo/monitor/static/strategy-booklet"
ETH_RESULTS = ROOT / "experiments/active/exp-eth4h-trend-candidate-20260907-v1/results"
OUT = ROOT / "yoyo/discipline_app/website/dist/assets/strategy-performance.json"
POINT_FORMAT = ["utcMs", "equity", "drawdownPct"]


def utc_datetime(value: str | int) -> datetime:
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"Naive source timestamp: {value}")
    return dt.astimezone(timezone.utc)


def utc_day(utc_ms: int) -> str:
    return datetime.fromtimestamp(utc_ms / 1000, tz=timezone.utc).date().isoformat()


def parse_utc_ms(value: str | int) -> int:
    if isinstance(value, int):
        return value
    return int(utc_datetime(value).timestamp() * 1000)


def validate_series(points: list[list[float | int]]) -> None:
    if len(points) < 2:
        raise ValueError("A showcase path needs an initial point and at least one observation")
    previous = None
    for point in points:
        if len(point) != 3:
            raise ValueError("Every point must be [UTC milliseconds, equity, drawdown percent]")
        ts, equity, drawdown = point
        if not all(math.isfinite(float(v)) for v in (ts, equity, drawdown)):
            raise ValueError("Non-finite point value")
        if previous is not None and int(ts) < previous:
            raise ValueError("Equity points are not in UTC time order")
        if float(equity) < 0:
            raise ValueError("Negative equity is not publishable as a normal account path")
        if float(drawdown) > 1e-8:
            raise ValueError("Drawdown points must be zero or negative")
        previous = int(ts)


def project_points(
    rows: Iterable[tuple[int, float]],
    *,
    seed: float,
    observed_drawdowns: Iterable[float] | None = None,
) -> list[list[float | int]]:
    values = list(rows)
    saved_dd = list(observed_drawdowns) if observed_drawdowns is not None else None
    if saved_dd is not None and len(saved_dd) != len(values):
        raise ValueError("Saved drawdown and equity point counts differ")

    peak = seed
    points: list[list[float | int]] = []
    for index, (ts, equity) in enumerate(values):
        equity = float(equity)
        if equity > peak:
            peak = equity
        dd = 100.0 * (equity / peak - 1.0) if peak else 0.0
        if saved_dd is not None:
            source_dd = float(saved_dd[index])
            if not math.isclose(source_dd, dd, rel_tol=0, abs_tol=2e-5):
                raise ValueError(
                    f"Saved drawdown differs from equity-derived drawdown at point {index}: "
                    f"{source_dd} vs {dd}"
                )
            dd = source_dd
        points.append([int(ts), round(equity, 8), round(min(0.0, dd), 8)])
    validate_series(points)
    if not math.isclose(float(points[0][1]), seed, rel_tol=0, abs_tol=1e-6):
        raise ValueError("First equity point does not equal the stated seed")
    return points


def max_drawdown_segment(points: list[list[float | int]]) -> dict[str, float | int]:
    peak_equity = float(points[0][1])
    peak_time = int(points[0][0])
    deepest = 0.0
    segment: dict[str, float | int] = {
        "peakUtcMs": peak_time,
        "troughUtcMs": peak_time,
        "peakEquity": peak_equity,
        "troughEquity": peak_equity,
        "drawdownPct": 0.0,
    }
    for ts_value, equity_value, _ in points:
        ts, equity = int(ts_value), float(equity_value)
        if equity > peak_equity:
            peak_equity, peak_time = equity, ts
        dd = 100.0 * (equity / peak_equity - 1.0) if peak_equity else 0.0
        if dd < deepest:
            deepest = dd
            segment = {
                "peakUtcMs": peak_time,
                "troughUtcMs": ts,
                "peakEquity": round(peak_equity, 8),
                "troughEquity": round(equity, 8),
                "drawdownPct": round(-dd, 8),
            }
    return segment


def annual_returns(points: list[list[float | int]], seed: float) -> list[dict[str, float | str]]:
    start_year = datetime.fromtimestamp(int(points[0][0]) / 1000, tz=timezone.utc).year
    end_year = datetime.fromtimestamp(int(points[-1][0]) / 1000, tz=timezone.utc).year
    year_ends: dict[int, float] = {}
    for ts_value, equity_value, _ in points:
        year = datetime.fromtimestamp(int(ts_value) / 1000, tz=timezone.utc).year
        year_ends[year] = float(equity_value)

    result: list[dict[str, float | str]] = []
    prior = seed
    latest = seed
    for year in range(start_year, end_year + 1):
        year_end = year_ends.get(year, latest)
        result.append(
            {
                "year": str(year),
                "returnPct": round((year_end / prior - 1.0) * 100.0, 8),
            }
        )
        prior = latest = year_end
    return result


def make_strategy(
    *,
    strategy_id: str,
    name: str,
    points: list[list[float | int]],
    seed: float,
    trades: int,
    fees: str,
    limitations: str,
    drawdown_basis: str,
    source_sampling: str,
    curve_mode: str,
    expected_return_pct: float,
    expected_max_dd_pct: float,
    annual: list[dict[str, float | str]] | None = None,
) -> dict:
    segment = max_drawdown_segment(points)
    terminal = float(points[-1][1])
    return_pct = (terminal / seed - 1.0) * 100.0
    if not math.isclose(return_pct, expected_return_pct, rel_tol=0, abs_tol=2e-4):
        raise ValueError(f"{strategy_id} return does not reconcile to its frozen source")
    if not math.isclose(float(segment["drawdownPct"]), expected_max_dd_pct, rel_tol=0, abs_tol=2e-4):
        raise ValueError(f"{strategy_id} drawdown does not reconcile to its plotted equity path")
    if trades <= 0:
        raise ValueError(f"{strategy_id} must include funded trades/events")
    if curve_mode not in {"settlement", "marked"}:
        raise ValueError(f"Unexpected curve mode for {strategy_id}: {curve_mode}")

    first_ms, last_ms = int(points[0][0]), int(points[-1][0])
    return {
        "id": strategy_id,
        "name": name,
        "period": {"start": utc_day(first_ms), "end": utc_day(last_ms)},
        "seed": round(seed, 8),
        "terminal": round(terminal, 8),
        "cumulativeReturnPct": round(return_pct, 8),
        "maxDrawdownPct": round(float(segment["drawdownPct"]), 8),
        "drawdownBasis": drawdown_basis,
        "maxDrawdownSegment": segment,
        "trades": int(trades),
        "fees": fees,
        "limitations": limitations,
        "sourceSampling": source_sampling,
        "curveMode": curve_mode,
        "annualReturns": annual if annual is not None else annual_returns(points, seed),
        "points": points,
    }


def load_wick_midpoint() -> dict:
    history = json.loads((BOOK / "live-wick6x3mid-history.json").read_text())
    summary = history["summary"]
    meta = history["meta"]
    if history.get("refilled") or summary.get("cash_added") != 0:
        raise ValueError("The wick midpoint account is not a no-addition capital path")
    if meta["allocator_result"]["liquidations"] != 0:
        raise ValueError("The wick midpoint history contains a liquidation")

    raw = history["points"]
    points = project_points(
        ((int(row[0]), float(row[1])) for row in raw),
        seed=float(summary["start"]),
        observed_drawdowns=(float(row[3]) for row in raw),
    )
    trades = int(summary["trades"])
    if trades != int(meta["funded"]) or len(points) != trades + 1:
        raise ValueError("Wick midpoint trade count does not match its frozen funded settlements")
    return make_strategy(
        strategy_id="live-wick6x3mid",
        name="插针收盘 · 浅穿中点",
        points=points,
        seed=float(summary["start"]),
        trades=trades,
        fees=(
            "止盈挂单maker单边0.02%、市价taker单边0.05%；入场和非目标退出另计单边0.10%滑点，"
            "未计永续资金费用。"
        ),
        limitations=(
            "浅穿中点条件按历史结果事后选出，仍属观察规则；历史Binance价格路径与OKX前向数据不同。"
        ),
        drawdown_basis="最大回撤按每笔历史平仓净值相对此前高点计算，未计平仓间的持仓盈亏。",
        source_sampling=f"1000U起始账户和{trades}笔历史回测平仓净值，共{len(points)}点；图中不含持仓浮盈亏。",
        curve_mode="settlement",
        expected_return_pct=float(summary["return_pct"]),
        expected_max_dd_pct=float(summary["max_drawdown_pct"]),
    )


def load_breadth_ema20() -> dict:
    source = json.loads((BOOK / "breadth-ema20-data.json").read_text())
    summary = source["summaries"]["all_history_fee_only"]
    curve = source["equity_curves"]["all_history_fee_only_settlement"]
    if curve.get("marking") != "realized exit settlements only; not intratrade 5-minute marks":
        raise ValueError("Unexpected breadth-ema20 curve sampling")
    if source["rules"]["account"]["capital_transfers_between_sleeves"] is not False:
        raise ValueError("Breadth-ema20 sleeves unexpectedly transfer capital")

    raw = curve["points"]
    seed = 1000.0
    points = project_points(
        ((parse_utc_ms(row["time"]), seed * float(row["combined_equity"])) for row in raw),
        seed=seed,
    )
    trades = int(summary["event_count"])
    if len(points) != trades + 1:
        raise ValueError("Breadth-ema20 settlement point count does not match event count")
    return make_strategy(
        strategy_id="breadth-ema20",
        name="全市场宽度 · EMA20",
        points=points,
        seed=seed,
        trades=trades,
        fees=(
            "BTC止盈挂单maker单边0.02%、市价单及ALT市价单taker单边0.05%；"
            "全历史回测未计永续资金费用。"
        ),
        limitations=(
            "2026年历史已被检视，不属于盲测；全历史曲线未计资金费用，0.20个百分点双边滑点情景净收益仍为+291.13%。"
        ),
        drawdown_basis=(
            "最大回撤按每笔历史平仓后的组合净值相对此前高点计算，未计平仓间的持仓盈亏；"
            "同期5分钟盯市摘要回撤为26.75%。"
        ),
        source_sampling=(
            f"1000U总资金分为BTC和ALT两个各500U子账户，历史回测每次平仓记录净值，共{len(points)}点；"
            "不含平仓间的浮盈亏。"
        ),
        curve_mode="settlement",
        expected_return_pct=float(summary["growth_pct"]),
        expected_max_dd_pct=float(
            next(
                row["settlement_dd_pct"]
                for row in source["summaries"]["frequency_review"]["btc_variants"]
                if row["variant"] == "current" and row["scope"] == "all_history"
            )
        ),
    )


def load_eth_4h() -> dict:
    result_summary = json.loads((ETH_RESULTS / "summary.json").read_text())
    continuous = next(
        row
        for row in result_summary["summary"]
        if row["window"] == "continuous" and row["arm"] == "S0_immediate_1x"
    )
    if continuous["administrative_exits"] != 0 or continuous["nonpositive_equity_seen"]:
        raise ValueError("ETH S0 does not pass the no-boundary-exit and positive-equity checks")

    source_rows = []
    with (ETH_RESULTS / "continuous_S0_immediate_1x_equity.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        for row in csv.DictReader(stream):
            source_rows.append((parse_utc_ms(row["time"]), float(row["equity"])))
    if not source_rows:
        raise ValueError("ETH S0 saved equity path is empty")
    seed = float(source_rows[0][1])
    if not math.isclose(seed, 500.0, rel_tol=0, abs_tol=1e-8):
        raise ValueError(f"Unexpected ETH S0 initial capital: {seed}")
    points = project_points(source_rows, seed=seed)

    # The stored CSV contains four-hour close marks. Its close drawdown matches
    # the plotted series; the source's deeper path metric includes intrabar risk.
    return make_strategy(
        strategy_id="eth4h-trend-s0-1x",
        name="ETH 4H 趋势 · 1x",
        points=points,
        seed=seed,
        trades=int(continuous["trades"]),
        fees=(
            "每边按0.10%手续费；本段共扣102.40U，不另计滑点或永续资金费。"
        ),
        limitations=(
            "Binance ETHUSDT历史回测，2025至2026年数据已检视；S0为历史研究基线，不代表上架或实盘策略。"
        ),
        drawdown_basis=(
            "最大回撤按4小时收盘账户净值相对此前高点计算，含持仓盈亏；"
            "研究报告的盘中回撤为34.54%。"
        ),
        source_sampling=f"每根4小时K线收盘的账户净值，共{len(points):,}点；持仓按收盘价计入浮动盈亏。",
        curve_mode="marked",
        expected_return_pct=float(continuous["return_pct"]),
        expected_max_dd_pct=float(continuous["close_dd_pct"]),
    )


def main() -> None:
    strategies = [load_wick_midpoint(), load_breadth_ema20(), load_eth_4h()]
    output = {
        "schemaVersion": 1,
        "currency": "USDT",
        "timezone": "UTC",
        "pointFormat": POINT_FORMAT,
        "drawdownDefinition": "(equity / runningPeak - 1) * 100; series values are zero or negative",
        "strategies": strategies,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    total_points = sum(len(strategy["points"]) for strategy in strategies)
    print(
        json.dumps(
            {
                "output": str(OUT.relative_to(ROOT)),
                "strategies": [
                    {
                        "id": strategy["id"],
                        "period": strategy["period"],
                        "seed": strategy["seed"],
                        "terminal": strategy["terminal"],
                        "cumulativeReturnPct": strategy["cumulativeReturnPct"],
                        "maxDrawdownPct": strategy["maxDrawdownPct"],
                        "trades": strategy["trades"],
                        "points": len(strategy["points"]),
                        "maxDrawdownSegment": strategy["maxDrawdownSegment"],
                    }
                    for strategy in strategies
                ],
                "totalPoints": total_points,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
