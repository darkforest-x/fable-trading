"""Receipt-bound statistics for the fixed 2026-09-20 trend baseline study.

This module reads no market source directly until it has authenticated the
completed replay.  The five fixed signal families deliberately retain their
own raw opposite exits, so a shared entry is not required to have the same
later return.  Event-R curves are descriptive sums of closed events, never a
portfolio equity curve or a position-sizing recommendation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation import spike_v9_htf_sma_report as prior
from yoyo.evaluation.spike_v10_4_study import digest
from yoyo.evaluation import trend_baseline_study as study


EXP = Path("experiments/active/exp-trend-baselines-20260920-v1")
REPORT = Path("analysis/p1_trend_baselines_20260920.md")
TEST = Path("tests/evaluation/test_trend_baseline_report.py")
ARMS = ("v9", "v9_1", "dc20", "dc55", "sma20_60")
PERIODS = ("full", "earlier", "later", "cross_split")
DIRECTIONS = (("both", 0), ("long", 1), ("short", -1))


def _period_bounds(cfg: dict, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the reported interval; full/cross use the complete frozen span."""

    start, split, end = map(pd.Timestamp, (cfg["start"], cfg["split"], cfg["end"]))
    if period == "earlier":
        return start, split
    if period == "later":
        return split, end
    return start, end


def _control_period(frame: pd.DataFrame, split: pd.Timestamp) -> pd.Series:
    """Classify control outcomes independently from the executed event."""

    clock = pd.DataFrame({"entry_time": frame["control_entry_time"],
                          "exit_time": frame["control_exit_time"]})
    return pd.Series(prior.assign_period(clock, split), index=frame.index).where(
        frame["control_entry_time"].notna(), "unavailable"
    )


def _event_curve(closed: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
    """Aggregate same-time exits before drawdown, including an initial zero mark.

    The underwater duration runs to ``end`` when the cumulative event curve has
    not recovered.  This prevents a no-trade tail from being silently omitted.
    """

    if end < start:
        raise ValueError("invalid event-curve interval")
    if closed.empty:
        return {"event_drawdown_r": 0.0, "max_underwater_days": 0.0}
    exits = closed.assign(exit_time=pd.to_datetime(closed.exit_time, utc=True)).groupby(
        "exit_time", sort=True
    ).net_r.sum()
    level, high, high_time, drawdown, underwater, below_high = 0.0, 0.0, start, 0.0, 0.0, False
    for when, amount in exits.items():
        level += float(amount)
        if level >= high:
            if below_high:
                underwater = max(underwater, (when - high_time).total_seconds() / 86400.0)
            high, high_time = level, when
            below_high = False
        else:
            drawdown = max(drawdown, high - level)
            underwater = max(underwater, (when - high_time).total_seconds() / 86400.0)
            below_high = True
    if below_high:
        underwater = max(underwater, (end - high_time).total_seconds() / 86400.0)
    return {"event_drawdown_r": float(drawdown), "max_underwater_days": float(underwater)}


def _controls_for_period(group: pd.DataFrame, split: pd.Timestamp, period: str) -> pd.DataFrame:
    """Keep recorded controls only; no outcome-dependent replacement is allowed."""

    matched = (~group.censored.astype(bool) & group.matched.fillna(False).astype(bool)
               & ~group.control_censored.fillna(True).astype(bool))
    if period == "earlier":
        matched &= pd.to_datetime(group.control_exit_time, utc=True) < split
    elif period == "later":
        matched &= pd.to_datetime(group.control_entry_time, utc=True) >= split
    elif period == "cross_split":
        entry = pd.to_datetime(group.control_entry_time, utc=True)
        exit_ = pd.to_datetime(group.control_exit_time, utc=True)
        matched &= entry.lt(split) & exit_.ge(split)
    return group.loc[matched].copy()


def metrics(group: pd.DataFrame, cfg: dict, period: str) -> dict[str, float | int]:
    """Extend the established V9 metrics with fixed-cost, tail, and curve facts."""

    base = prior.metrics(group)  # Shared definitions for win rate, PF, and R/bp.
    closed = group.loc[~group.censored.astype(bool)].copy()
    gross_r = closed.gross_r.astype(float) if len(closed) else pd.Series(dtype=float)
    net_r = closed.net_r.astype(float) if len(closed) else pd.Series(dtype=float)
    positive = net_r.loc[net_r.gt(0)]
    winners_10r = net_r.loc[net_r.ge(10)]
    curve = _event_curve(closed, *_period_bounds(cfg, period))
    base.update({
        "gross_bp": float(closed.gross_return.astype(float).sum() * 1e4),
        "cost_r": float((gross_r - net_r).sum()),
        "winners_10r": int(len(winners_10r)),
        "winners_10r_positive_profit_share": (
            float(winners_10r.sum() / positive.sum()) if len(positive) and positive.sum() else np.nan
        ),
        "realized_top10pct_net_r_concentration": (
            float(net_r.nlargest(max(1, int(np.ceil(len(net_r) * .1)))).sum() / positive.sum())
            if len(net_r) and len(positive) and positive.sum() else np.nan
        ),
        **curve,
    })
    return base


def control_metrics(group: pd.DataFrame, split: pd.Timestamp, period: str) -> dict[str, float | int]:
    """Summarize recorded eligible controls without dropping their parent events."""

    paired = _controls_for_period(group, split, period)
    net = paired.control_net_r.astype(float) if len(paired) else pd.Series(dtype=float)
    strategy = paired.net_r.astype(float) if len(paired) else pd.Series(dtype=float)
    control_keys = ["symbol", "timeframe", "side", "control_signal_time"]
    unique_controls = paired.drop_duplicates(control_keys) if len(paired) else paired
    return {
        "random_pairs": int(len(paired)),
        "control_unmatched": int((~group.matched.fillna(False).astype(bool)).sum()),
        "control_censored": int((group.matched.fillna(False).astype(bool) & group.control_censored.fillna(True).astype(bool)).sum()),
        "closed_without_eligible_control": int((~group.censored.astype(bool)).sum() - len(paired)),
        "random_mean_r": float(net.mean()) if len(net) else np.nan,
        "paired_strategy_mean_r": float(strategy.mean()) if len(strategy) else np.nan,
        "random_net_r": float(net.sum()),
        "random_net_bp": float(paired.control_net_return.astype(float).sum() * 1e4) if len(paired) else 0.0,
        "random_excess_r": float((strategy - net).mean()) if len(paired) else np.nan,
        "random_excess_sum_r": float((strategy - net).sum()),
        "random_unique_control_timestamps": int(len(unique_controls)),
    }


def _slice(group: pd.DataFrame, period: str, side: int) -> pd.DataFrame:
    """Select one temporal/directional view while retaining valid empty frames."""

    result = group if period == "full" else group.loc[group.period.eq(period)]
    return result if side == 0 else result.loc[result.side.eq(side)]


def summarize(trades: pd.DataFrame, cfg: dict, *, per_asset: bool = False) -> pd.DataFrame:
    """Return every fixed arm/timeframe view, including zero-trade groups."""

    split = pd.Timestamp(cfg["split"])
    symbols = sorted(trades.symbol.unique()) if per_asset else [None]
    rows: list[dict] = []
    for symbol in symbols:
        symbol_data = trades if symbol is None else trades.loc[trades.symbol.eq(symbol)]
        for tf, _ in cfg["timeframes"]:
            for arm in ARMS:
                arm_data = symbol_data.loc[symbol_data.timeframe.eq(tf) & symbol_data.arm.eq(arm)]
                for period in PERIODS:
                    for direction, side in DIRECTIONS:
                        part = _slice(arm_data, period, side)
                        row = {"timeframe": tf, "arm": arm, "period": period,
                               "direction": direction, **metrics(part, cfg, period),
                               **control_metrics(part, split, period)}
                        if symbol is not None:
                            row["symbol"] = symbol
                        rows.append(row)
    return pd.DataFrame(rows)


def monthly(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Give each observed month a paired-control view; no zero-fill redraw occurs."""

    split = pd.Timestamp(cfg["split"])
    rows: list[dict] = []
    for tf, _ in cfg["timeframes"]:
        for arm in ARMS:
            base = trades.loc[trades.timeframe.eq(tf) & trades.arm.eq(arm)]
            for period in PERIODS:
                for direction, side in DIRECTIONS:
                    view = _slice(base, period, side).copy()
                    if view.empty:
                        continue
                    view["month"] = pd.to_datetime(view.entry_time, utc=True).dt.strftime("%Y-%m")
                    for month, part in view.groupby("month", sort=True):
                        facts = metrics(part, cfg, period)
                        # Entry-month cohorts can exit in a later month, so an
                        # event curve bounded to this row would misstate both
                        # drawdown and duration.  Curves stay on full periods.
                        facts.pop("event_drawdown_r")
                        facts.pop("max_underwater_days")
                        rows.append({"timeframe": tf, "arm": arm, "period": period,
                                     "direction": direction, "month": month,
                                     **facts,
                                     **control_metrics(part, split, period)})
    return pd.DataFrame(rows)


def random_tests(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Run recorded-pair month-block tests for all arms/timeframes and two folds."""

    split = pd.Timestamp(cfg["split"])
    rows: list[dict] = []
    for tf, _ in cfg["timeframes"]:
        for arm in ARMS:
            for period in ("earlier", "later"):
                group = _slice(trades.loc[trades.timeframe.eq(tf) & trades.arm.eq(arm)], period, 0)
                pairs = _controls_for_period(group, split, period)
                strategy = pairs.loc[:, ["entry_time", "net_r"]].copy()
                control = pairs.loc[:, ["entry_time", "control_net_r"]].rename(
                    columns={"control_net_r": "net_r"}
                )
                test = prior.monthly_delta(control, strategy, period, cfg, "net_r")
                control_keys = ["symbol", "timeframe", "side", "control_signal_time"]
                rows.append({"timeframe": tf, "arm": arm, "period": period,
                             "pairN": int(len(pairs)),
                             "unique_controls": int(len(pairs.drop_duplicates(control_keys))), **test})
    return pd.DataFrame(rows)


def comparisons(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Compare each predeclared simple family with V9; Holm applies only later."""

    rows: list[dict] = []
    for tf, _ in cfg["timeframes"]:
        for arm in ("dc20", "dc55", "sma20_60"):
            for period in ("earlier", "later"):
                source = _slice(trades.loc[trades.timeframe.eq(tf) & trades.arm.eq("v9")], period, 0)
                target = _slice(trades.loc[trades.timeframe.eq(tf) & trades.arm.eq(arm)], period, 0)
                delta = prior.monthly_delta(source.loc[~source.censored], target.loc[~target.censored], period, cfg)
                source_control = control_metrics(source, pd.Timestamp(cfg["split"]), period)
                target_control = control_metrics(target, pd.Timestamp(cfg["split"]), period)
                rows.append({"timeframe": tf, "arm": arm, "period": period,
                             "v9_entries": int(len(source)), "arm_entries": int(len(target)),
                             "v9_net_r": float(source.loc[~source.censored, "net_r"].sum()),
                             "arm_net_r": float(target.loc[~target.censored, "net_r"].sum()),
                             "random_excess_delta_r": target_control["random_excess_r"] - source_control["random_excess_r"],
                             "v9_random_pairs": source_control["random_pairs"],
                             "arm_random_pairs": target_control["random_pairs"], **delta})
    result = pd.DataFrame(rows)
    later = result.period.eq("later")
    if int(later.sum()) != 9:
        raise ValueError("expected exactly nine later simple-family comparisons")
    result["holm_p"] = np.nan
    result.loc[later, "holm_p"] = prior.holm(result.loc[later, "p"].to_numpy(float))
    return result


def _verify_source(source: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Authenticate full-pool replay identity, receipts, coverage, and ledgers."""

    identity = json.loads((source / "identity.json").read_text())
    complete = json.loads((source / "completion.json").read_text())
    cfg = json.loads((EXP / "config.json").read_text())
    if identity.get("config") != cfg or not identity.get("full_pool") or not complete.get("full_pool"):
        raise ValueError("statistics require the frozen full_pool replay")
    symbols = sorted(complete.get("symbols", []))
    if len(symbols) != 29 or symbols != sorted(identity.get("inputs", {})):
        raise ValueError("statistics require exactly the frozen 29-symbol input pool")
    if complete.get("identity_hash") != identity.get("identity_hash") or complete.get("stream_arms") != 435:
        raise ValueError("completion identity differs from replay identity")
    if digest(study.old.source.EXCHANGE_INFO) != identity.get("metadata_sha256"):
        raise ValueError("frozen input metadata SHA differs")
    for path, sha in identity.get("declared", {}).items():
        if digest(Path(path)) != sha:
            raise ValueError(f"declared replay input drift: {path}")
    trades, controls, coverage = [], [], []
    for symbol in symbols:
        item = identity["inputs"][symbol]
        if digest(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"raw source SHA differs: {symbol}")
        folder = source / "streams" / symbol
        receipt = json.loads((folder / "completion.json").read_text())
        if receipt.get("identity_hash") != identity["identity_hash"]:
            raise ValueError(f"stream identity differs: {symbol}")
        if receipt.get("source_sha256") != item["sha256"]:
            raise ValueError(f"stream receipt is not bound to its raw source: {symbol}")
        if receipt.get("prior_identity_sha256") != identity.get("prior_identity_sha256"):
            raise ValueError(f"stream receipt is not bound to the prior replay identity: {symbol}")
        for name, sha in receipt.get("files", {}).items():
            if digest(folder / name) != sha:
                raise ValueError(f"stream artifact SHA differs: {symbol}/{name}")
        trades.append(pd.read_csv(folder / "trades.csv.gz"))
        controls.append(pd.read_csv(folder / "controls.csv.gz"))
        coverage.extend(receipt.get("coverage", []))
    coverage_frame = pd.DataFrame(coverage)
    required = {(symbol, tf, arm) for symbol in symbols for tf, _ in cfg["timeframes"] for arm in ARMS}
    observed = set(map(tuple, coverage_frame[["symbol", "timeframe", "arm"]].itertuples(index=False, name=None)))
    if len(coverage_frame) != 435 or observed != required:
        raise ValueError("coverage must be exactly 29 symbols × 3 timeframes × 5 arms")
    t, c = pd.concat(trades, ignore_index=True), pd.concat(controls, ignore_index=True)
    if c.duplicated(["arm", "event_key"]).any():
        raise ValueError("controls must be unique by arm and event_key")
    t = t.merge(c, on=["arm", "event_key"], how="left", validate="many_to_one", suffixes=("", "_control"))
    if t.reason.isna().any() or len(t) != sum(len(part) for part in trades):
        raise ValueError("control join lost an executed event")
    split = pd.Timestamp(cfg["split"])
    for field in ("signal_bar_open", "entry_time", "exit_time", "control_signal_time", "control_entry_time", "control_exit_time"):
        t[field] = pd.to_datetime(t[field], utc=True)
    t["period"] = prior.assign_period(t, split)
    t["control_period"] = _control_period(t, split)
    return identity, t, coverage_frame


def _write_report(summary: pd.DataFrame, comp: pd.DataFrame, tests: pd.DataFrame, identity: dict, source: Path, output: Path) -> None:
    """Write the Chinese Markdown record without selecting a winner or tuning."""

    view = summary.loc[summary.direction.eq("both") & summary.period.isin(["earlier", "later"])]
    view = view.loc[:, ["timeframe", "arm", "period", "entries", "closed", "censored", "gross_r", "net_r",
                        "cost_r", "net_bp", "win_rate", "pf", "winners_10r",
                        "winners_10r_positive_profit_share", "random_pairs", "random_mean_r", "random_excess_r"]]
    report = f'''# 固定趋势基线：29 币、三周期、共同风险退出

本报告比较预注册的 V9、V9.1、dc20、dc55 和 sma20_60 五个信号家族。20/55/60 都是图表 bar 单位，简单臂不是原版海龟的日线、加仓或完整风控系统。信号家族各自拥有反向退出，因此共同进场并不要求后续收益相同。

## 数据与统计口径

- 冻结 Binance 29 币池，PEPE 映射 1000PEPE、ARY 排除；时间范围、split、既有风险退出与 20bp 往返成本均由 `{EXP}/config.json` 绑定。
- 每臂均报告 full / earlier / later / cross_split，以及 long / short / both。共同 61 根有效 OHLC 预热、V9 原始就绪与日历/RV 守门均已在回放写入账本；缺 bar、坏 OHLC 与 partial bar 聚合在流收据的 coverage 中保留。
- 匹配随机对照按同币、方向、UTC 月、时间段与波动桶在回放时确定。统计只读取原记录，不因删失、跨切点或收益而重抽。earlier 的控制要求控制退出在 split 前，later 要求控制入场在 split 后。

## 前段与后段全臂结果

{prior.markdown_table(view, list(view.columns))}

`cost_r = gross_r - net_r`；名义 bp 是固定名义收益，不是账户收益。10R 列为已实现净 R≥10 的赢家数及其占全部正盈利 R 的份额。事后 top10% 净 R 集中度见 `summary.csv`，它只是已实现尾部诊断，不能解释为预测 top-decile alpha。

## 简单臂相对 V9：前后段与随机对照

{prior.markdown_table(comp, ["timeframe", "arm", "period", "v9_net_r", "arm_net_r", "delta", "low95", "high95", "p", "holm_p", "random_excess_delta_r", "v9_random_pairs", "arm_random_pairs"])}

后段恰有 9 个预注册简单臂×周期差异，`holm_p` 只对这九项调整。月块 bootstrap/sign-flip 的 pairN 和独立控制时间戳数见 `random_tests.csv`；不把有限月份的 p 值当作验证或部署裁决。

## 相对匹配随机入场的月块检验

{prior.markdown_table(tests, ["timeframe", "arm", "period", "pairN", "unique_controls", "delta", "low95", "high95", "p"])}

这里的 delta 是配对实际净 R 减去随机对照净 R 的总差。p 为单侧月块符号翻转检验（正向改善），此表没有多重比较校正，不能从中挑最小 p 宣布有效。抽样单位不足、时间依赖与同月跨币相关均限制结论。summary.csv 另列 unmatched、control_censored 和闭合交易中无合格对照的数量。

## 复现与限制

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_trend_baseline_signals.py tests/evaluation/test_trend_baseline_study.py tests/evaluation/test_trend_baseline_report.py
.venv/bin/python -W ignore -m yoyo.evaluation.trend_baseline_study --workers 3 --output {EXP}/run_v2
.venv/bin/python -m yoyo.evaluation.trend_baseline_report --source {EXP}/run_v2 --output-name {output.name}
```

回放 identity：`{identity["identity_hash"]}`；回放 source commit：`{identity["source_commit"]}`。`summary.csv`、`per_asset.csv`、`monthly.csv`、`comparisons.csv`、`random_tests.csv` 和带控制的逐笔账本均在 `{output}`。命令用于输入归档齐全且输出尚未生成的环境；统计拒绝覆盖历史文件。相同重跑源须保持 identity 中的提交及全部声明文件一致，后续文档提交不能冒充原构建提交。run_v1 保留，run_v2 仅补来源绑定校验；最终审计比较两次逐流产物 SHA。

风险与诚实声明：后段已被看过，不能称盲样本外；固定 29 币池也不等于实时可交易宇宙。本轮未计资金费、额外滑点、容量、跨周期并发或账户仓位。事件累计 R 的最大回撤按同一 exit_time 先合并，水下日数从区间起点的零值持续到区间终点；两者均不是账户权益回撤。AUC 和预测 top-decile 不适用，因为没有分类器或预测排序。结果不得自动 promote、改成本/止损或替换生产；V12.3 也不是本报告的全量回测对象。

下一步只能由 owner 在完整表、随机对照与限制基础上决定是否做新的独立观察；不得据此追调简单信号参数。
'''
    if REPORT.exists():
        raise ValueError("refusing to overwrite an existing historical report")
    REPORT.write_text(report)


def run(source: Path = EXP / "run_v2", output_name: str = "statistics_v1") -> None:
    """Authenticate a committed full replay, then produce immutable statistics."""

    if not engine._committed((Path(__file__), TEST)):
        raise ValueError("commit this report module and its tests before market statistics")
    source = Path(source)
    identity, trades, coverage = _verify_source(source)
    cfg = identity["config"]
    output = source / output_name
    if output.exists():
        raise ValueError("statistics output already exists; choose a new output-name")
    output.mkdir()
    summary = summarize(trades, cfg)
    per_asset = summarize(trades, cfg, per_asset=True)
    months = monthly(trades, cfg)
    comp = comparisons(trades, cfg)
    random = random_tests(trades, cfg)
    trades.to_csv(output / "trades_with_controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    per_asset.to_csv(output / "per_asset.csv", index=False)
    months.to_csv(output / "monthly.csv", index=False)
    comp.to_csv(output / "comparisons.csv", index=False)
    random.to_csv(output / "random_tests.csv", index=False)
    coverage.to_csv(output / "coverage.csv", index=False)
    _write_report(summary, comp, random, identity, source, output)
    files = {path.name: digest(path) for path in output.iterdir() if path.is_file()}
    receipt = {"sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "replay_identity": identity["identity_hash"], "files": files,
               "report_sha256": digest(REPORT), "stream_coverage": int(len(coverage))}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    manifest = {"sourceCommit": receipt["sourceCommit"], "source_commit": receipt["source_commit"],
                "replay_identity": identity["identity_hash"], "artifacts": [
                    {"path": str(REPORT), "sha256": digest(REPORT)},
                    *[{"path": str(path), "sha256": digest(path)} for path in output.iterdir()],
                    *[{"path": str(path), "sha256": digest(path)} for path in
                      [source/"identity.json", source/"completion.json", EXP/"config.json", EXP/"PROJECT_PLAN.md",
                       *sorted(source.glob("streams/*/completion.json"))]],
                ]}
    manifest_path = EXP / "delivery_manifest.json"
    if manifest_path.exists():
        raise ValueError("refusing to overwrite experiment delivery manifest")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=EXP / "run_v2")
    parser.add_argument("--output-name", default="statistics_v1")
    args = parser.parse_args()
    run(args.source, args.output_name)
