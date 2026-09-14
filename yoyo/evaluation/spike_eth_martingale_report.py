"""Render the pre-registered ETH V8 martingale study from frozen result files.

This is a presentation-only CLI.  It reads the evaluator's saved summaries,
cash ledgers, opportunity ledgers, and matched-event pairs; it never reads an
OHLCV source, recomputes a signal, or selects a policy.  A missing holdout
summary is normal before the single authorized frozen-policy exposure.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-eth-martingale-20260914-v1"
RESULTS = EXP / "results"
PRE_EVALUATION = RESULTS / "pre_evaluation"
HOLDOUT_EVALUATION = RESULTS / "holdout_evaluation"
SELECTION = EXP / "selection.json"
SEARCH = RESULTS / "pre" / "development_search.csv"
REPORT = ROOT / "analysis" / "p1_spike_eth_martingale_20260914.md"
HTML = ROOT / "analysis" / "html" / "p1_spike_eth_martingale_20260914.html"
ASSETS = RESULTS / "report_assets"


def _require(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"required study artifact is missing: {path}")
    return path


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(_require(path))


def _read_optional_csv(path: Path) -> pd.DataFrame:
    """Treat an empty saved pair ledger as an honest absence of matches."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}"


def _pct(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _summary_frames() -> tuple[pd.DataFrame, pd.DataFrame | None]:
    pre = _read_csv(PRE_EVALUATION / "summary.csv")
    holdout_path = HOLDOUT_EVALUATION / "summary.csv"
    holdout = _read_csv(holdout_path) if holdout_path.is_file() else None
    required = {
        "stream", "period", "arm", "final_balance", "profit", "max_realized_drawdown",
        "n_accepted", "n_rejected", "max_effective_leverage", "boundary_marks", "ruined",
        "matched_control_pnl", "matched_delta", "matched_p",
    }
    for name, frame in [("pre", pre), ("holdout", holdout)]:
        if frame is not None:
            missing = required - set(frame.columns)
            if missing:
                raise ValueError(f"{name} summary missing fields: {', '.join(sorted(missing))}")
    return pre, holdout


def _stage_winners(search: pd.DataFrame) -> pd.DataFrame:
    """Reapply the frozen runner's documented development-only tie breaker."""
    required = {"stream", "stage", "final_balance", "base_risk_usdt", "max_level", "reset_mode", "leverage_cap"}
    missing = required - set(search.columns)
    if missing:
        raise ValueError(f"development search missing fields: {', '.join(sorted(missing))}")
    winners = []
    for (_, _), candidates in search.groupby(["stream", "stage"], sort=False):
        # This is deliberately the runner's max key written as sortable columns:
        # (final balance, lower risk, lower level, lower capacity, win preferred).
        # A Boolean final key avoids depending on alphabetical reset-mode order.
        ordered = candidates.assign(_win_preferred=candidates["reset_mode"].eq("win")).sort_values(
            ["final_balance", "base_risk_usdt", "max_level", "leverage_cap", "_win_preferred"],
            ascending=[False, True, True, True, False], kind="stable",
        )
        winners.append(ordered.iloc[0].to_dict())
    return pd.DataFrame(winners)


def _ledger_path(directory: Path, stream: str, period: str, arm: str) -> Path:
    return directory / f"{stream}_{period}_{arm}_ledger.csv"


def _opportunity_path(directory: Path, stream: str, period: str) -> Path:
    return directory / f"{stream}_{period}_opportunities.csv"


def _pairs_path(directory: Path, stream: str, period: str, arm: str) -> Path:
    return directory / f"{stream}_{period}_{arm}_pairs.csv"


def _natural_stats(opportunities: pd.DataFrame) -> dict[str, Any]:
    censored = opportunities.get("censored", pd.Series(False, index=opportunities.index)).map(_truth)
    natural = opportunities.loc[~censored].copy()
    net_r = pd.to_numeric(natural.get("net_r"), errors="coerce")
    positives = float(net_r.loc[net_r > 0].sum())
    negatives = float(-net_r.loc[net_r < 0].sum())
    pf = None if negatives == 0 else positives / negatives
    entries = pd.to_datetime(opportunities.get("entry_time"), utc=True, errors="coerce")
    exits = pd.to_datetime(opportunities.get("exit_time"), utc=True, errors="coerce")
    return {
        "natural_closed": int(len(natural)),
        "natural_wins": int(pd.to_numeric(natural.get("net_return"), errors="coerce").gt(0).sum()),
        "reference_win_rate": None if natural.empty else float(pd.to_numeric(natural.get("net_return"), errors="coerce").gt(0).mean()),
        "natural_pf": pf,
        "boundary_marks": int(censored.sum()),
        "time_range": "—" if entries.dropna().empty else f"{entries.min().isoformat()} 至 {exits.max().isoformat()}",
    }


def _accepted_natural(directory: Path, stream: str, period: str, arm: str) -> pd.DataFrame:
    ledger = _read_csv(_ledger_path(directory, stream, period, arm))
    opportunities = _read_csv(_opportunity_path(directory, stream, period))
    accepted = ledger.loc[ledger.get("accepted", pd.Series(False, index=ledger.index)).map(_truth)].copy()
    if accepted.empty:
        return accepted
    required = {"trade_id", "notional"}
    if missing := required - set(accepted.columns):
        raise ValueError(f"ledger missing fields: {', '.join(sorted(missing))}")
    fields = [name for name in [
        "trade_id", "censored", "gross_return", "net_return", "net_r", "initial_risk_frac",
    ] if name in opportunities]
    merged = accepted.merge(opportunities[fields], on="trade_id", how="inner", validate="one_to_one")
    censored = merged.get("censored", pd.Series(False, index=merged.index)).map(_truth)
    merged = merged.loc[~censored].copy()
    merged["notional"] = pd.to_numeric(merged["notional"], errors="coerce")
    merged["initial_risk_frac"] = pd.to_numeric(merged.get("initial_risk_frac"), errors="coerce")
    derived_risk = merged["notional"] * merged["initial_risk_frac"]
    # Exact requested dollars avoid floating-point tie breaks turning a fixed
    # risk baseline into an invented ranking of "higher risk" observations.
    merged["risk_dollars"] = (pd.to_numeric(merged["planned_risk"], errors="coerce")
                              if "planned_risk" in merged else derived_risk)
    merged["gross_pnl"] = merged["notional"] * pd.to_numeric(merged.get("gross_return"), errors="coerce")
    merged["net_pnl"] = merged["notional"] * pd.to_numeric(merged.get("net_return"), errors="coerce")
    return merged.dropna(subset=["risk_dollars", "notional"])


def _account_natural_pf(directory: Path, stream: str, period: str, arm: str) -> float | None:
    """Profit factor for actually accepted, naturally closed cash-account trades."""
    rows = _accepted_natural(directory, stream, period, arm)
    if rows.empty or "net_pnl" not in rows:
        return None
    pnl = pd.to_numeric(rows.get("net_pnl"), errors="coerce")
    gains = float(pnl.loc[pnl > 0].sum())
    losses = float(-pnl.loc[pnl < 0].sum())
    return None if losses == 0 else gains / losses


def _risk_decile(directory: Path, stream: str, period: str, arm: str) -> dict[str, Any]:
    rows = _accepted_natural(directory, stream, period, arm)
    if rows.empty:
        return {"stream": stream, "period": period, "arm": arm, "n_top": 0, "n_rest": 0,
                "gross_pnl_average": None, "net_pnl_average": None, "net_r_average": None,
                "rest_gross_pnl_average": None, "rest_net_pnl_average": None, "rest_net_r_average": None,
                "control_net_r_average": None, "matched_delta": None, "matched_n": 0,
                "alpha_note": "没有已接受且自然平仓的交易"}
    top_n = max(1, math.ceil(len(rows) * 0.10))
    top = rows.sort_values(["risk_dollars", "trade_id"], ascending=[False, True], kind="stable").head(top_n).copy()
    rest = rows.drop(index=top.index)
    pairs_file = _pairs_path(directory, stream, period, arm)
    if pairs_file.is_file():
        pairs = _read_optional_csv(pairs_file)
        if "trade_id" in pairs:
            top = top.merge(pairs, on="trade_id", how="left", suffixes=("", "_pair"))
    control_net_r = (
        pd.to_numeric(top["control_net_r"], errors="coerce")
        if "control_net_r" in top
        else pd.Series(float("nan"), index=top.index)
    )
    matched = top.loc[control_net_r.notna()].copy()
    if matched.empty:
        control_average = None
        delta = None
        alpha_note = "该高风险子集没有可用匹配随机事件，不能支持 alpha 结论"
    else:
        control = pd.to_numeric(matched["control_net_r"], errors="coerce")
        control_average = float(control.mean())
        control_pnl = matched["risk_dollars"] * control
        delta = float((matched["net_pnl"] - control_pnl).sum())
        alpha_note = "仅为同币同月同波动桶匹配事件描述，不是可交易随机账户"
    return {
        "stream": stream, "period": period, "arm": arm, "n_top": len(top), "n_rest": len(rows) - len(top),
        "gross_pnl_average": float(top["gross_pnl"].mean()), "net_pnl_average": float(top["net_pnl"].mean()),
        "net_r_average": float(pd.to_numeric(top.get("net_r"), errors="coerce").mean()),
        "rest_gross_pnl_average": None if rest.empty else float(rest["gross_pnl"].mean()),
        "rest_net_pnl_average": None if rest.empty else float(rest["net_pnl"].mean()),
        "rest_net_r_average": None if rest.empty else float(pd.to_numeric(rest.get("net_r"), errors="coerce").mean()),
        "control_net_r_average": control_average, "matched_delta": delta, "matched_n": len(matched),
        "alpha_note": alpha_note,
    }


def _continuous_chart(pre: pd.DataFrame, stream: str) -> Path | None:
    rows = pre.loc[(pre.stream == stream) & (pre.period == "continuous_pre")]
    if rows.empty:
        return None
    ASSETS.mkdir(parents=True, exist_ok=True)
    chart = ASSETS / f"{stream}_continuous_pre_equity.png"
    opportunities = _read_csv(_opportunity_path(PRE_EVALUATION, stream, "continuous_pre"))
    entry_times = pd.to_datetime(opportunities.get("entry_time"), utc=True, errors="coerce").dropna()
    exit_times = pd.to_datetime(opportunities.get("exit_time"), utc=True, errors="coerce").dropna()
    if entry_times.empty or exit_times.empty:
        return None
    window_start = entry_times.min()
    # Censored opportunities carry the configured window end in exit_time, so
    # this includes rejected-candidate time rather than truncating at the last fill.
    window_end = exit_times.max()
    fig, ax = plt.subplots(figsize=(10, 4.8))
    plotted = False
    for arm, color in [("martingale", "#9b2226"), ("fixed", "#005f73")]:
        if not (rows.arm == arm).any():
            continue
        ledger = _read_csv(_ledger_path(PRE_EVALUATION, stream, "continuous_pre", arm))
        accepted = ledger.loc[ledger.get("accepted", pd.Series(False, index=ledger.index)).map(_truth)].copy()
        if not accepted.empty and {"exit_time", "equity_after"}.issubset(accepted.columns):
            accepted["exit_time"] = pd.to_datetime(accepted["exit_time"], utc=True, errors="coerce")
            accepted["equity_after"] = pd.to_numeric(accepted["equity_after"], errors="coerce")
            accepted = accepted.dropna(subset=["exit_time", "equity_after"]).sort_values("exit_time")
        else:
            accepted = pd.DataFrame(columns=["exit_time", "equity_after"])
        times = [window_start, *accepted["exit_time"].tolist()]
        equities = [1000.0, *accepted["equity_after"].tolist()]
        # Explicitly retain the last realized balance across rejected-candidate
        # intervals through the frozen window end.
        if times[-1] < window_end:
            times.append(window_end)
            equities.append(equities[-1])
        ax.step(times, equities, where="post", label=arm, color=color)
        plotted = True
    if not plotted:
        plt.close(fig)
        return None
    ax.axhline(1000, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_title(f"{stream} continuous_pre: realized/window-end-marked equity (not bar-by-bar)")
    ax.set_ylabel("USDT")
    ax.set_xlabel("exit or window-end mark time (UTC)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(chart, dpi=160)
    plt.close(fig)
    return chart


def _formal_table(frame: pd.DataFrame, period: str, directory: Path) -> str:
    rows = frame.loc[frame.period == period]
    values: list[list[Any]] = []
    for row in rows.itertuples(index=False):
        values.append([
            row.stream, row.arm, _fmt(row.final_balance), _fmt(row.profit), _pct(row.max_realized_drawdown),
            int(row.n_accepted), int(row.n_rejected), _fmt(row.max_effective_leverage), int(row.boundary_marks),
            "是" if _truth(row.ruined) else "否", _fmt(_account_natural_pf(directory, row.stream, period, row.arm)),
            _fmt(row.matched_control_pnl), _fmt(row.matched_delta), _fmt(row.matched_p, 4),
        ])
    return _table(
        ["stream", "账户", "期末余额(U)", "利润(U)", "最大已实现回撤", "接受", "拒绝", "最高实际杠杆", "窗口末标记", "归零", "账户自然PF", "matched_control_pnl(U)", "matched_delta(U)", "matched_p"],
        values,
    )


def _answer_first(search: pd.DataFrame, pre: pd.DataFrame, holdout: pd.DataFrame | None) -> list[str]:
    """State the dynamic evidence before explaining the frozen workflow."""
    policy_fields = ["base_risk_usdt", "max_level", "reset_mode", "leverage_cap"]
    unique_policies = len(search[policy_fields].drop_duplicates())
    final_balances = pd.to_numeric(search["final_balance"], errors="coerce")
    development = search.loc[final_balances.notna()]
    all_development_below = not development.empty and bool((final_balances.loc[development.index] < 1000).all())
    validation = pre.loc[(pre.period == "validation") & (pre.arm == "martingale")]
    continuous = pre.loc[(pre.period == "continuous_pre") & (pre.arm == "martingale")]
    validation_lost = not validation.empty and bool((pd.to_numeric(validation["profit"], errors="coerce") < 0).all())
    continuous_lost = not continuous.empty and bool((pd.to_numeric(continuous["profit"], errors="coerce") < 0).all())
    parts = [
        "## 先看结论",
        "",
        f"开发搜索共比较 {len(search)} 次，涉及 {len(search[['stream']+policy_fields].drop_duplicates())} 个周期×参数组合（去掉周期后为 {unique_policies} 组不同参数）；候选期末余额最高 {_fmt(final_balances.max())}U。所有开发候选期末余额是否低于 1000U：{'是' if all_development_below else '否'}。",
    ]
    if validation.empty:
        parts.append("复用验证的倍投结果尚未生成。")
    else:
        verdict = "全部亏损" if validation_lost else "并非全部亏损"
        details = "；".join(f"{row.stream} 利润 {_fmt(row.profit)}U" for row in validation.itertuples(index=False))
        parts.append(f"复用验证 martingale：{verdict}（{details}）。")
    if continuous.empty:
        parts.append("continuous_pre 诊断尚未生成。")
    else:
        details = "；".join(
            f"{row.stream} 期末 {_fmt(row.final_balance)}U、接受 {int(row.n_accepted)}、拒绝 {int(row.n_rejected)}"
            for row in continuous.itertuples(index=False)
        )
        parts.append(f"continuous_pre 连续账户：{details}。")
    if all_development_below and validation_lost and continuous_lost:
        parts.append("本轮未找到可推荐的持续盈利倍投方案；以下仅为受限开发搜索中的开发冠军，不构成可用策略。")
    else:
        parts.append("证据未满足持续盈利方案的推荐门槛；以下仅报告受限开发搜索中的开发冠军及其窗口边界。")
    preholdout_martingale = pre.loc[(pre.period == "preholdout") & (pre.arm == "martingale")]
    for row in preholdout_martingale.itertuples(index=False):
        if _number(row.profit) is not None and float(row.profit) > 0:
            fixed = pre.loc[(pre.period == "preholdout") & (pre.arm == "fixed") & (pre.stream == row.stream)]
            fixed_note = ""
            if not fixed.empty:
                fixed_row = fixed.iloc[0]
                if float(fixed_row.final_balance) > float(row.final_balance):
                    fixed_note = f"；同窗 fixed 期末 {_fmt(fixed_row.final_balance)}U，更高"
                else:
                    fixed_note = f"；同窗 fixed 期末 {_fmt(fixed_row.final_balance)}U，未更高"
            level_note = ("；实际最高执行层级为0，该盈利窗口没有执行翻倍" if int(row.max_executed_level)==0 else "")
            parts.append(f"反例边界：{row.stream} 在 2026年1–4月 preholdout 单窗 martingale 利润 {_fmt(row.profit)}U{fixed_note}{level_note}；它不能推翻连续账户与其他窗口的结论。")
    if holdout is not None:
        rows = holdout.loc[holdout.arm == "martingale"]
        details = "；".join(
            f"{row.stream} 期末 {_fmt(row.final_balance)}U、接受 {int(row.n_accepted)}、拒绝 {int(row.n_rejected)}"
            for row in rows.itertuples(index=False)
        )
        parts.append(f"冻结后唯一授权的最终 holdout：{details}。")
    parts.append("")
    return parts


def _build_report(pre: pd.DataFrame, holdout: pd.DataFrame | None, selection: dict[str, Any], search: pd.DataFrame) -> str:
    winners = _stage_winners(search)
    streams = list(dict.fromkeys(pre["stream"].tolist()))
    parts = [
        "# ETH V8 止损后倍投：1000 USDT 有限资金研究",
        "",
        f"本地交付：[MD]({REPORT}) · [HTML]({HTML}) · [完整开发搜索 CSV]({SEARCH})。",
        "",
    ]
    parts += _answer_first(search, pre, holdout)
    parts += [
        "本报告只渲染已冻结的研究产物：没有重新计算 V8 信号、OHLCV 或政策搜索。开发冠军由开发期四阶段单变量坐标搜索产生；验证、预 holdout 与最终 holdout 均只评估该冻结选择，不预设结果正负。",
        "",
        "开发冠军只是在受限开发搜索中期末余额最高的政策，不能等同于可用的“最佳方案”。特别是容量不足会导致大量 V8 候选被拒绝；账户参与率必须与余额一起判断，不能只看少数接受交易的结果。",
        "",
        "## 开发冠军与冻结选择",
        "",
        "选择只使用开发期账户期末余额；平手按较低底注、层数与容量优先。它是有限坐标搜索的开发冠军，不是全局最优证明。",
        "",
    ]
    stage_rows: list[list[Any]] = []
    for stream in streams:
        for stage in ["1_levels", "2_reset", "3_base_risk", "4_capacity"]:
            row = winners.loc[(winners.stream == stream) & (winners.stage == stage)]
            if row.empty:
                continue
            item = row.iloc[0]
            stage_rows.append([stream, stage, _fmt(item.base_risk_usdt), int(item.max_level), item.reset_mode, _fmt(item.leverage_cap), _fmt(item.final_balance)])
    parts += [_table(["stream", "开发阶段", "底注(U)", "最大层级", "复位", "容量上限", "该步冠军期末余额(U)"], stage_rows), ""]
    frozen_rows = []
    for stream, item in selection.get("selected", {}).items():
        policy = item.get("policy", {})
        frozen_rows.append([stream, _fmt(policy.get("base_risk_usdt")), policy.get("max_level", "—"), policy.get("reset_mode", "—"), _fmt(policy.get("leverage_cap"))])
    parts += ["最终冻结政策：", "", _table(["stream", "底注(U)", "最大层级", "复位", "容量上限"], frozen_rows), ""]
    development_participation = []
    for item in pre.loc[(pre.period == "development") & (pre.arm == "martingale")].itertuples(index=False):
        total = int(item.n_accepted) + int(item.n_rejected)
        development_participation.append([
            item.stream, int(item.n_accepted), int(item.n_rejected),
            "—" if total == 0 else _pct(int(item.n_accepted) / total),
        ])
    if development_participation:
        parts += ["开发冠军的容量参与情况（动态读取正式账本摘要）：", "", _table(["stream", "接受", "拒绝", "接受率"], development_participation), ""]
    parts += [
        "1/2/4 层是计划初始止损金额序列，底注为固定 USDT，不是保证金或 ETH 数量：notional = base_risk_usdt × 2**level / initial_risk_frac。触发仅为净亏损且退出原因含 stop；非 stop 亏损保持层级。触顶触发亏损会认亏、记录 capped_cycle_reset 并回到 level 0，绝不会抹掉账户损失。win 是任意净盈利复位，recovery 仅在本轮累计净 PnL 回本后复位。",
        "",
        "## 正式窗口账户结果",
        "",
        "matched_control_pnl、matched_delta 与 matched_p 来自实际接受交易按计划风险金额加权的同 ETH、同 UTC 月、同此前 120 bar 波动桶匹配事件。它们是配对反事实，不是可交易随机账户。",
        "",
    ]
    for period, title in [("development", "开发"), ("validation", "复用验证"), ("preholdout", "预 holdout"), ("continuous_pre", "开发至预 holdout 连续账户诊断")]:
        if (pre.period == period).any():
            parts += [f"### {title}", "", _formal_table(pre, period, PRE_EVALUATION), ""]
    if holdout is None:
        parts += ["### 最终 holdout", "", "尚未生成最终 holdout 结果。只有冻结选择提交后才能读取一次；本报告不把缺失的 Binance 5m holdout 伪造成数据。", ""]
    else:
        parts += ["### 最终 holdout（冻结后授权第 1 次）", "", _formal_table(holdout, "holdout", HOLDOUT_EVALUATION), "", "最终 holdout 只允许 OKX ETH 3m；Binance 5m 源截至 2026-05-01，没有 holdout 数据。", ""]
    parts += [
        "## 数据统计与自然交易参考",
        "",
        "以下 refwinrate 与参考机会池 PF 来自全部自然平仓机会，未施加现金账户的拒单/接受约束，因此不是账户成交 PF；账户自然 PF 已单列在正式账户表。窗口末标记不混入任一胜率或 PF。AUC 不适用：本研究没有训练预测器或预测分数。",
        "",
    ]
    data_rows = []
    for directory, frame in [(PRE_EVALUATION, pre), (HOLDOUT_EVALUATION, holdout)]:
        if frame is None:
            continue
        for item in frame.drop_duplicates(["stream", "period"]).itertuples(index=False):
            opportunities = _read_csv(_opportunity_path(directory, item.stream, item.period))
            stats = _natural_stats(opportunities)
            data_rows.append([item.stream, item.period, int(item.candidates), int(item.opportunities), stats["natural_closed"], stats["natural_wins"], _pct(stats["reference_win_rate"]), _fmt(stats["natural_pf"]), stats["boundary_marks"], stats["time_range"]])
    parts += [_table(["stream", "窗口", "候选", "opportunity", "自然平仓", "自然胜", "refwinrate", "参考机会池自然PF", "窗口末标记", "时间范围"], data_rows), ""]
    parts += [
        "既有原始 baseline：3m 111 笔均 −0.237R、5m 693 笔均 −0.275R，来自不同历史样本，不能与本报告各窗口从 1000U 重启的账户百分比直接比较。",
        "",
        "## 最高实际风险 10% 的交易描述",
        "",
        "仅将账户账本中 accepted 且非 censored 的交易与 opportunities 合并；按实际 risk_dollars=notional×initial_risk_frac 排序，取最高 10%。这描述仓位分配，并非训练排序或 alpha 证明。风险金额并列时按 trade_id 稳定取样；固定风险基线的最高10%只是一组并列样本，没有风险排序含义。",
        "",
    ]
    risk_rows = []
    for directory, frame in [(PRE_EVALUATION, pre), (HOLDOUT_EVALUATION, holdout)]:
        if frame is None:
            continue
        for item in frame.itertuples(index=False):
            details = _risk_decile(directory, item.stream, item.period, item.arm)
            risk_rows.append([details["stream"], details["period"], details["arm"], details["n_top"], details["n_rest"], _fmt(details["gross_pnl_average"]), _fmt(details["net_pnl_average"]), _fmt(details["net_r_average"]), _fmt(details["rest_gross_pnl_average"]), _fmt(details["rest_net_pnl_average"]), _fmt(details["rest_net_r_average"]), _fmt(details["control_net_r_average"]), _fmt(details["matched_delta"]), details["matched_n"], details["alpha_note"]])
    parts += [_table(["stream", "窗口", "账户", "最高风险10%", "其余", "高风险平均毛PnL(U)", "高风险平均净PnL(U)", "高风险平均净R", "其余平均毛PnL(U)", "其余平均净PnL(U)", "其余平均净R", "匹配随机平均净R", "配对美元差(U)", "匹配数", "解释"], risk_rows), ""]
    parts += ["## 连续账户诊断图", ""]
    charts = [_continuous_chart(pre, stream) for stream in streams]
    for chart in [path for path in charts if path is not None]:
        parts += [f"![{chart.stem}]({chart})", ""]
    parts += [
        "曲线使用已实现退出或窗口末标记时点，不是逐 bar 浮动权益；每个正式窗口各自从 1000U 空仓重启，continuous_pre 才显示开发起至预 holdout 的连续诊断。",
        "",
        "## 风险与诚实声明",
        "",
        "- 现金账本没有真实 mark price、持仓内最大浮亏、历史分层维持保证金、资金费、盘口滑点或可执行成交。现金未归零不能证明避免交易所强平；归零也不是历史交易所强平概率。",
        "- 回撤为已实现或窗口末标记口径，不是持仓内最大回撤。容量上限只是模拟入场约束，不能作为实际杠杆建议。",
        "- 容量不足的候选保持层级，并等待原冻结 V8 影子持仓结束后再尝试下一笔；不是拒单后立刻扫描所有空档信号。",
        "- 研究含跨所差异、已被历史研究复用的数据、多次开发选择、有限样本和固定 9 个随机种子。任何看似异常好的结果先按 bug 或泄漏检查，不能自动 promote、部署或称为实盘证据。",
        "",
        "## 复现与 holdout 纪律",
        "",
        "Owner 原始约束为“1000 承受爆仓”，授权为“冻结方案后使用 holdout 最终评估”。本配置第1次消耗 holdout，授权、冻结选择与消耗收据均保存在实验目录。首次 builder 提交00cb37d879；输入哈希修复ba84ff0a2f；最终选择提交f4aa4530bc在验证及holdout读取之前。首次选择保留为selection.initial.json，修复前后政策和搜索表完全一致。",
        "",
        "V8原始确认、压缩门、同方向绳索距离3ATR、次根开盘入场、结构止损、2R后4ATR跟踪及原V6反向退出均冻结。每笔成本固定为名义本金的0.2%往返，入场额外预留同额费用但不重复扣款。开发2023-08-01至2025-01-01，验证为2025全年，预holdout为2026年1至4月；区间右端不含。最大层级1至8、复位win/recovery、底注1/2/5/10/20/50U、模拟容量3/5/10/20依次单字段搜索，没有穷举全部交叉组合。",
        "",
        "核验记录见实验目录verification.json：逐笔账本与18行账户摘要对账、产物哈希及13项专项测试。仓库级注册表检查受一个既有记录缺失source_commit阻塞，未把它计作本研究通过。浏览器安全策略阻止本地HTML页面预览，因此仅完成静态结构和图片资源检查，不声称浏览器视觉验收通过。",
        "",
        "```bash\n# 先确认 builder、内核、测试和计划已提交；prepare 输出不可覆盖\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study prepare --phase pre\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study select\ngit add experiments/active/exp-spike-eth-martingale-20260914-v1/selection.json experiments/active/exp-spike-eth-martingale-20260914-v1/results/pre/development_search.csv\ngit branch --show-current  # 必须为 main\ngit commit -m 'Freeze ETH martingale development selection'\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study evaluate --phase pre\n# 仅在 Owner 已授权的冻结配置上执行一次；如需重跑，创建新版本并记录新的曝光，绝不直接重复 holdout\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study prepare --phase holdout\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study evaluate --phase holdout\n.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_report\n```",
        "",
        "## 下一步选项",
        "",
        "本版记为拒绝，不启用。可复用的是有限资金核算与拒单诊断。若继续研究容量不足时重置、不同入场机会或更高容量，须另立配置、先预注册再评估；其中重新使用holdout或修改V8障碍、成本需Owner另行决策，本轮不会凭已有holdout结果继续挑参数。",
        "",
        "已归档至[Spike研究记录](https://app.notion.com/p/3db8856479af81bf9c94f5e9f483e621)。",
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    """Refuse an overwrite, render Markdown, then immediately render its HTML."""
    if REPORT.exists():
        raise FileExistsError(f"report already exists and will not be overwritten: {REPORT}")
    pre, holdout = _summary_frames()
    selection = json.loads(_require(SELECTION).read_text())
    search = _read_csv(SEARCH)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(_build_report(pre, holdout, selection, search), encoding="utf-8")
    subprocess.run([
        str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "md_to_html.py"),
        str(REPORT), "--out-dir", str(ROOT / "analysis" / "html"),
    ], cwd=ROOT, check=True)
    print(f"Markdown: {REPORT}")
    print(f"HTML: {HTML}")


if __name__ == "__main__":
    main()
