"""Render authenticated three-year results; never fit, select rules or rescore.

Tables consume frozen account/event/annual/score CSVs and expose their exact
denominators. Extra matched counts only join existing event/control identities;
they neither create controls nor change stored outcomes. Equity figures read
saved NAV. At most five post-hoc natural-exit examples are selected from actual
SPIKE account fills by highest PnL, lowest PnL per timeframe, and largest global
peak-minus-net-R giveback. The existing figure helper reconstructs only each
selected display path and reconciles prices/times/R against its frozen ledger.

Chart contract: one two-timeframe 2x2 NAV/drawdown figure, neutral titles,
linear axes, blue/gold plus neutral controls and line-style distinctions.
Case figures include100 bars before signal, complete actual holding and24
available bars after exit; peak R is visibly separate from realized net R.
The maximum is six PNGs in total. Final interpretation belongs to the report
reviewer; the default summary is a literal readout, not a profitability claim.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_long_history import EXP, START, END, CONFIG, CUTS
from yoyo.evaluation import spike_burst_figures as case_figures

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "analysis/p1_spike_burst_three_year_20260910.md"
CHOICES = (("burst_trail", "full", "SPIKE 全部", "#3F6FAE", "-"),
           ("focus_trail", "full", "旧 IMACD 全部", "#AE8237", "-"),
           ("burst_trail", "paired_actual", "SPIKE 可配对", "#3F6FAE", "--"),
           ("burst_trail", "paired_random", "SPIKE 匹配随机", "#69747D", ":"))
ARM_NAMES = {"burst_trail": "SPIKE 爆发", "focus_trail": "旧 IMACD 入场"}
LEDGER_COLUMNS = ["event_id", "asset", "minutes", "arm", "valid", "portfolio_selected",
    "realized_net_pnl", "natural_exit", "censored", "exit_time", "peak_r", "net_r"]
CONTEXT_AUDITS = {
    "previous_study_delivery_audit.json": "c6be92556372c333b5af7faa33cf73a9914686f3c65e3ca64389c0e2372a1dfc",
    "source_audit_pre_sanitize.json": "72fde128e5b50a929da16e95aac8a985852360afd93b91d060ba890402596337",
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), size_bytes=path.stat().st_size)


def check(path, expected):
    path = Path(path).resolve()
    if not path.is_file() or sha(path) != expected:
        raise ValueError("Changed or missing authenticated report input: " + str(path))
    return path


class Evidence:
    """Read only final, linked validation/dataset/history receipts and their files."""
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.read_sources = {}
        validation_path = self.folder / "validation_manifest.json"
        self.validation = json.loads(validation_path.read_text())
        if self.validation.get("status") != "complete" or self.validation.get("config") != CONFIG:
            raise ValueError("Final three-year validation with the frozen protocol is required")
        self.read_sources[str(validation_path)] = artifact(validation_path)
        dataset_path = check(self.folder / "dataset_manifest.json", self.validation["input_manifest_sha256"])
        self.dataset = json.loads(dataset_path.read_text())
        if self.dataset.get("status") != "complete" or self.dataset.get("config") != CONFIG:
            raise ValueError("Final three-year dataset protocol mismatch")
        self.read_sources[str(dataset_path)] = artifact(dataset_path)
        history_record = self.dataset["history"]
        history_path = check(history_record["path"], history_record["sha256"])
        self.history = json.loads(history_path.read_text())
        if self.history.get("status") != "complete" or self.history.get("schema") != "spike-burst-history-v1":
            raise ValueError("Complete authenticated history receipt required")
        if self.history.get("tradability_audit", {}).get("status") != "complete":
            raise ValueError("Tradability-sanitized history required; raw placeholder coverage is not tradable")
        self.read_sources[str(history_path)] = artifact(history_path)
        raw_parent = self.history["raw_parent"]
        raw_path = check(raw_parent["path"], raw_parent["sha256"])
        self.read_sources[str(raw_path)] = artifact(raw_path)
        for name, expected in CONTEXT_AUDITS.items():
            audit_path = check(EXP / "diagnostics" / name, expected)
            self.read_sources[str(audit_path)] = artifact(audit_path)
        self.allowed = {}
        for receipt in (self.validation, self.dataset):
            for item in receipt["artifacts"] + receipt.get("feature_sources", []):
                path = str(Path(item["path"]).resolve())
                if path in self.allowed and self.allowed[path] != item["sha256"]:
                    raise ValueError("Conflicting source hashes in final receipts")
                self.allowed[path] = item["sha256"]
        self.cache = {}

    def path(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.folder / path
        key = str(path.resolve())
        if key not in self.allowed:
            raise ValueError("Unregistered report source: " + key)
        path = check(key, self.allowed[key])
        self.read_sources[key] = artifact(path)
        return path

    def table(self, path):
        path = self.path(path)
        key = str(path)
        if key not in self.cache:
            try:
                frame = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                frame = pd.DataFrame(columns=LEDGER_COLUMNS if "ledger" in path.name else [])
            for name in ("time", "decision_time", "entry_time", "exit_time", "exit_time_lower", "exit_time_upper"):
                if name in frame:
                    frame[name] = pd.to_datetime(frame[name], utc=True)
            self.cache[key] = frame
        return self.cache[key].copy()

    def account(self, minutes, cohort, arm, account, kind):
        return self.table("accounts/%d_%s_%s_%s_%s.csv.gz" % (minutes, cohort, arm, account, kind))

    def finish(self):
        for row in self.read_sources.values():
            check(row["path"], row["sha256"])


def number(value, digits=2, signed=False):
    if value is None or pd.isna(value):
        return "不适用"
    if np.isposinf(value):
        return "+∞"
    if np.isneginf(value):
        return "−∞"
    return format(float(value), ("+" if signed else "") + ",." + str(digits) + "f")


def rate(wins, count):
    return (number(100 * wins / count) + "%%（%d/%d）" % (wins, count)) if count else "不适用（0/0）"


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "／").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"] +
                     ["| " + " | ".join(cell(x) for x in row) + " |" for row in rows])


def one(frame, **conditions):
    selected = frame
    for key, value in conditions.items():
        selected = selected.loc[selected[key].eq(value)]
    if len(selected) != 1:
        raise ValueError("Expected exactly one frozen summary row: " + str(conditions))
    return selected.iloc[0]


def denominator_rows(ledger):
    """Count outcomes already saved in account fills; do not simulate anything."""
    filled = ledger.loc[ledger.portfolio_selected.eq(True)].copy()
    if len(filled) and (filled.event_id.duplicated().any() or not filled.natural_exit.isin([True, False]).all()
                        or not filled.censored.isin([True, False]).all()):
        raise ValueError("Explicit unique account fills and exit categories required")
    natural = filled.loc[filled.natural_exit.eq(True)]
    marked = filled.loc[filled.censored.eq(True)]
    if len(natural) + len(marked) != len(filled) or set(natural.event_id) & set(marked.event_id):
        raise ValueError("Natural exits and marks must partition selected fills")
    return {name: (int(part.realized_net_pnl.gt(0).sum()), len(part))
            for name, part in (("all", filled), ("natural", natural), ("marked", marked))}


def score_counts(events, controls, score):
    """Audit top-decile membership and count real finite matched controls only."""
    subset = events.loc[events.minutes.eq(int(score.minutes)) & events.arm.eq(score.arm) & events.valid.eq(True)].copy()
    subset = subset.loc[~subset.asset.isin(["BTC", "ETH"])] if score.cohort == "altcoins" else subset.loc[subset.asset.eq(score.cohort)]
    finite = np.isfinite(pd.to_numeric(subset[score.feature], errors="coerce"))
    subset = subset.loc[finite].sort_values([score.feature, "event_id"], ascending=[False, True])
    expected_top = max(1, int(np.ceil(len(subset) * .1))) if len(subset) else 0
    if len(subset) != int(score.n) or expected_top != int(score.top_n):
        raise ValueError("Frozen top-decile denominator mismatch")
    top = subset.head(expected_top)
    valid = controls.loc[controls.valid.eq(True) & controls.matched_event_id.isin(top.event_id)].copy()
    valid = valid.loc[np.isfinite(pd.to_numeric(valid.net_bp, errors="coerce"))]
    return len(top), int(valid.matched_event_id.nunique()), len(valid)


def neutral_curves(evidence, output):
    """Saved continuous NAV, with yearly boundaries shown but no resets."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), sharex="col", sharey="row", height_ratios=[2.3, 1])
    for column, minutes in enumerate((60, 240)):
        for arm, account, label, color, linestyle in CHOICES:
            frame = evidence.account(minutes, "altcoins", arm, account, "curve")
            if frame.empty or not frame.time.is_monotonic_increasing or frame.time.duplicated().any():
                raise ValueError("Complete chronological NAV is required")
            if frame.time.min() > START + pd.Timedelta(minutes=minutes) or frame.time.max() != END:
                raise ValueError("NAV does not cover the full frozen three-year clock")
            axes[0, column].plot(frame.time, 100 * (frame.equity / 100000 - 1), color=color,
                linestyle=linestyle, lw=1.7, label=label)
            axes[1, column].plot(frame.time, 100 * frame.drawdown, color=color, linestyle=linestyle, lw=1.3)
        axes[0, column].set_title("%dH · 币安山寨账户" % (minutes // 60), loc="left", fontsize=13, fontweight="bold")
        axes[0, column].legend(loc="upper left", fontsize=9, ncol=2)
        for ax in axes[:, column]:
            ax.grid(axis="y")
            ax.axhline(0, color="#88949F", lw=.7)
            for cut in CUTS[1:-1]:
                ax.axvline(cut, color="#CED5DB", lw=.8, linestyle="--")
            ax.set_xlim(START, END)
        axes[0, column].set_ylabel("账户累计收益 %")
        axes[1, column].set_ylabel("全期高点回撤 %")
        axes[1, column].xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axes[1, column].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.suptitle("三年山寨账户 · 累计收益与回撤", x=.055, ha="left", fontsize=18, fontweight="bold")
    fig.text(.055, .027, "2023-09-09—2026-09-09 UTC｜每个周期独立10万美元现金账户｜固定20bp往返成本｜年度边界不平仓", fontsize=10, color="#53616E")
    fig.tight_layout(rect=[0, .06, 1, .94])
    path = Path(output) / "account_paths.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def select_examples(ledgers):
    """Fixed, explicitly post-hoc choices; only actually selected natural exits."""
    selected, all_filled, seen = [], [], set()
    for minutes in (60, 240):
        ledger = ledgers[minutes]
        filled = ledger.loc[ledger.portfolio_selected.eq(True) & ledger.natural_exit.eq(True)].copy()
        if filled.empty:
            continue
        all_filled.append(filled)
        for ascending, reason in ((False, "本周期自然退出利润最高"), (True, "本周期自然退出盈亏最低")):
            row = filled.sort_values(["realized_net_pnl", "event_id"], ascending=[ascending, True]).iloc[0]
            if row.event_id not in seen:
                selected.append((row, reason)); seen.add(row.event_id)
    if all_filled:
        pool = pd.concat(all_filled, ignore_index=True)
        pool["giveback"] = pool.peak_r - pool.net_r
        row = pool.sort_values(["giveback", "event_id"], ascending=[False, True]).iloc[0]
        if row.event_id not in seen:
            selected.append((row, "两周期自然退出峰值回吐最大"))
    return selected


def render_cases(evidence, output):
    ledgers = {m: evidence.account(m, "altcoins", "burst_trail", "full", "ledger") for m in (60, 240)}
    examples = []
    for row, reason in select_examples(ledgers):
        evidence.path(row.features_path)
        reason = pd.Timestamp(row.entry_time).tz_convert("Asia/Shanghai").strftime("%Y年 · ") + reason
        path = case_figures.example(row, reason, output, len(examples) + 1)
        examples.append(dict(path=path, reason=reason, event_id=row.event_id, minutes=int(row.minutes),
            asset=row.asset, symbol=row.symbol, venue=row.venue, portfolio_selected=True,
            entry_time=row.entry_time.isoformat(), exit_time=row.exit_time.isoformat(),
            entry_price=float(row.entry_price), exit_price=float(row.exit_price),
            peak_r=float(row.peak_r), net_r=float(row.net_r), net_return=float(row.net_return),
            account_pnl=float(row.realized_net_pnl), notional=float(row.notional)))
    return examples


def report_text(evidence, curve_path, examples, report_path, summary_text=None):
    accounts = evidence.table("accounts_summary.csv")
    events_summary = evidence.table("event_summary.csv")
    annual = evidence.table("annual_summary.csv")
    scores = evidence.table("score_summary.csv")
    events, controls = evidence.table("events.csv.gz"), evidence.table("controls.csv.gz")
    coverage = evidence.table("coverage.csv")
    rows, account_rows = [], []
    literal = []
    for minutes in (60, 240):
        primary = one(accounts, minutes=minutes, cohort="altcoins", arm="burst_trail", account="full")
        literal.append("%dH 全部山寨账户净收益 %s%%、最大回撤 %s%%、成交 %d 笔" %
            (minutes // 60, number(primary.return_pct, signed=True), number(primary.max_drawdown_pct), primary.trades))
        for arm, account, label, _, _ in CHOICES:
            row = one(accounts, minutes=minutes, cohort="altcoins", arm=arm, account=account)
            ledger = evidence.account(minutes, "altcoins", arm, account, "ledger")
            counts = denominator_rows(ledger)
            if counts["all"][1] != int(row.trades) or counts["natural"][1] != int(row.natural_exits) or counts["marked"][1] != int(row.boundary_marks):
                raise ValueError("Account summary and ledger denominators disagree")
            account_rows.append((minutes, arm, account, label, row, counts))
            rows.append([str(minutes // 60) + "H", label, number(row.return_pct, signed=True), number(row.max_drawdown_pct),
                int(row.trades), rate(*counts["all"]), rate(*counts["natural"]), rate(*counts["marked"]), number(row.profit_factor)])
    image_ref = lambda p: os.path.relpath(Path(p).resolve(), Path(report_path).resolve().parent)
    lines = ["# SPIKE 强劲爆发 V1：三年冻结规则回顾", "", "## 结果概览", "",
        summary_text.strip() if summary_text else "；".join(literal) + "。下表同时列出旧 IMACD 入场和同条件随机对照，不能只看盈利案例判断系统。",
        "", "评价时间为 **2023-09-09 至 2026-09-09（UTC，1096天）**。本轮是币安 USDT 永续多头回顾，BTC、ETH 单列，不混入山寨池。原始数据从2023-05-01用于预热，新上市品种只从实际可得历史开始。",
        "", "这是同一冻结配置第 **2** 次收益评估消耗 holdout，正式边界仍为2026-05-04，且与前次61天研究重叠。**不是新的盲测或未见样本外结果，也没有通过本轮数据调参。**",
        "", "## 完整账户与匹配账户分别比较", "",
        "全部账户回答整套候选能留下多少收益；可配对账户与随机账户只比较同一批可匹配事件。每周期均从10万美元现金开始，账户互相独立。旧 IMACD 使用原启动入场加同一套宽止损/趋势退出，不能称为原策略整体收益。",
        "", table(["周期", "账户", "净收益 %", "最大回撤 %", "成交数", "全部胜率（含估值）", "自然退出胜率", "边界估值为正比例", "PF"], rows),
        "", "胜率分母是账户实际成交；自然退出与边界估值互斥。边界包括评价期末及连续数据段末的存续仓估值，并非实际保护退出。PF也包括已按边界估值计入的账本盈亏；没有亏损时本实现记为不适用，不编成无限收益。",
        "", "曲线使用保存的每根收盘权益，不以单笔涨幅叠加，不把1H与4H账户收益相加。下方回撤相对全期此前权益高点；两列使用相同纵轴范围。年度虚线只表示分段位置，不触发平仓或重开。",
        "", "![三年山寨账户权益与回撤](%s)" % image_ref(curve_path),
        "", "## 年度结果继承原持仓", "",
        "年度范围依次为2023-09-09→2024-09-09、2024-09-09→2025-09-09、2025-09-09→2026-09-09。年度收益按连续权益首尾计算；本节最大回撤在各年度内重新建立高水位，不能与全期最大回撤直接相加。退出数与胜率按账本成交时序归属：年度边界的开盘退出归新年，盘中/收盘退出归旧年；未退出交易不借用未来结果。"]
    for category, selected in (("全部信号账户", [("burst_trail", "full"), ("focus_trail", "full")]),
                               ("SPIKE 配对账户", [("burst_trail", "paired_actual"), ("burst_trail", "paired_random")])):
        records = []
        for minutes, arm, account, label, _, _ in account_rows:
            if (arm, account) not in selected:
                continue
            for period in ("year1", "year2", "year3"):
                row = one(annual, minutes=minutes, cohort="altcoins", arm=arm, account=account, period=period)
                records.append([str(minutes // 60) + "H", label, period.replace("year", "第") + "年",
                    number(row.opening_equity), number(row.ending_equity), number(row.return_pct, signed=True),
                    number(row.max_drawdown_pct), int(row.exits), int(row.natural_exits),
                    number(100 * row.natural_win_rate) + "%" if pd.notna(row.natural_win_rate) else "不适用"])
        lines += ["", "### " + category, "", table(["周期", "账户", "年度", "起始权益 $", "结束权益 $", "年度收益 %", "年度内回撤 %", "退出/估值数", "自然退出数", "自然退出胜率"], records)]
    primary = events_summary.loc[events_summary.cohort.eq("altcoins")]
    if len(primary) != 4:
        raise ValueError("Exactly four primary Holm results required")
    records = []
    for row in primary.sort_values(["minutes", "arm"]).itertuples():
        subset = events.loc[events.minutes.eq(row.minutes) & events.arm.eq(row.arm) & ~events.asset.isin(["BTC", "ETH"]) & events.valid.eq(True)]
        matched = controls.loc[controls.valid.eq(True) & controls.matched_event_id.isin(subset.event_id)]
        matched = matched.loc[np.isfinite(pd.to_numeric(matched.net_bp, errors="coerce"))]
        n = matched.matched_event_id.nunique()
        records.append([str(row.minutes // 60) + "H", ARM_NAMES[row.arm], int(row.candidates), int(row.valid), n,
            int(row.paired0), number(row.mean_net_bp), number(row.paired_actual_net_bp), number(row.paired_random_net_bp),
            number(row.asset_balanced_excess_bp), int(row.permutation_assets), number(row.permutation_p, 6), number(row.holm_p, 6)])
    lines += ["", "## 四项主检验：收益是否超出条件匹配随机", "",
        "每个实际事件最多匹配3个同币、同周期、同UTC周、同因果ATR百分位桶随机时点。随机时点只排除本根及此前12根信号；不借用未来信号排除。控制可跨事件复用，不能当独立新样本。配对账户固定使用有效的control0；事件统计使用该事件全部有效控制的均值，两个分母可能不同。",
        "", table(["周期", "入场", "候选", "有效", "有有效匹配 n", "control0配对 n", "全有效均净 bp", "匹配实际净 bp", "匹配随机净 bp", "资产均衡超额 bp", "置换资产数", "原始 p", "四项 Holm p"], records),
        "", "主检验仅山寨池2周期×2入场，共4项；缺失检验保留在校正家族中。超额先按资产×周平均，再按资产平均，置换单位是资产。只有净超额为正且Holm p<0.01才满足预登记的强于随机证据门槛；这里不自动把显著性解释为未来盈利保证。未匹配事件不能补零，也不能外推成全池超额。",
        "", "## 胜率、自然大赢家和边界估值", ""]
    records = []
    for row in primary.sort_values(["minutes", "arm"]).itertuples():
        records.append([str(row.minutes // 60) + "H", ARM_NAMES[row.arm], int(row.valid), number(100 * row.win_rate),
            int(row.natural_exits), int(row.boundary_marks), int(row.natural_20pct), int(row.natural_50pct),
            int(row.natural_5r), number(row.peak_r_max), number(row.net_r_max)])
    lines += [table(["周期", "入场", "有效事件 n", "事件净胜率 %", "自然退出 n", "边界估值 n", "自然净≥20%", "自然净≥50%", "自然净≥5R", "最大峰值 R", "最大净 R"], records),
        "", "本表是有效事件层，包含因现金、10资产上限或容量约束而未被账户买入的候选；不是实际持仓胜率。峰值R与净R的最大值也可能来自不同事件。自然大赢家计数明确排除尚未自然退出的边界估值。",
        "", "## 量比与TR扩张：描述性排序，不是训练模型", ""]
    records = []
    if len(scores):
        for row in scores.loc[scores.cohort.eq("altcoins")].sort_values(["minutes", "arm", "feature"]).itertuples():
            top_n, matched_n, control_n = score_counts(events, controls, row)
            records.append([str(row.minutes // 60) + "H", ARM_NAMES[row.arm], "量比" if row.feature == "relative_volume" else "TR扩张",
                int(row.n), number(row.descriptive_auc, 4), top_n, matched_n, control_n,
                number(row.top_gross_bp), number(row.top_net_bp), number(100 * row.top_win_rate),
                number(row.top_matched_actual_bp), number(row.top_random_bp), number(row.top_excess_bp)])
    lines += [table(["周期", "入场", "单特征", "有限值 n", "描述AUC", "Top10% n", "Top有匹配 n", "有效控制条数", "Top毛 bp", "Top净 bp", "Top胜率 %", "Top匹配实际 bp", "Top随机 bp", "Top超额 bp"], records),
        "", "没有训练或验证分类模型，模型val AUC不适用。这里AUC只描述同一历史样本中特征与净盈利标签的排序关系。Top10%按既定特征降序、event_id稳定打破同分；均值基于有效事件，并非因果可交易的当时横截面组合。Top全部净收益与Top匹配净收益的分母不同，上表显式列出实际匹配数，不把缺失控制当零。",
        "", "## BTC与ETH单列：少信号也是结果", ""]
    records = []
    for cohort in ("BTC", "ETH"):
        for minutes in (60, 240):
            for arm in ("burst_trail", "focus_trail"):
                row = one(accounts, minutes=minutes, cohort=cohort, arm=arm, account="full")
                event = one(events_summary, minutes=minutes, cohort=cohort, arm=arm)
                counts = denominator_rows(evidence.account(minutes, cohort, arm, "full", "ledger"))
                records.append([cohort, str(minutes // 60) + "H", ARM_NAMES[arm], int(event.candidates), int(event.valid),
                    int(row.trades), rate(*counts["natural"]), rate(*counts["marked"]), number(row.return_pct, signed=True), number(row.max_drawdown_pct)])
    lines += [table(["资产", "周期", "入场", "候选 n", "有效 n", "成交 n", "自然退出胜率", "边界估值为正比例", "净收益 %", "最大回撤 %"], records),
        "", "BTC、ETH分别建诊断账户，不计入山寨主检验；单资产不足以支持资产置换显著性结论。零信号时胜率为不适用，不能记为0%或100%；不能为了增加ETH信号而在本轮放宽参数。",
        "", "## 实际账户成交的完整路径", "",
        "按固定事后规则选择每周期自然退出利润最高与最低各一例，再选择两周期自然退出峰值回吐最大的一例，按event_id去重，最多5例。所有图都是账户确实持有的历史模拟成交；它们用于解释路径，不代表全部信号成功率。紫线为信号、绿三角为下一根开盘模拟入场、橙线为本根有效保护、红叉为保护成交。浅蓝区域是信号当时看不到的后续价格。"]
    for index, item in enumerate(examples, 1):
        lines += ["", "### %d · %s %dH · %s" % (index, item["asset"], item["minutes"] // 60, item["reason"]), "",
            "北京时间：%s 入场，%s 退出。" %
            (pd.Timestamp(item["entry_time"]).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M"),
             pd.Timestamp(item["exit_time"]).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")), "",
            "已由山寨全部账户实际选入。入场 %s → 退出 %s；最高浮盈 **%sR**，退出扣成本后 **%sR**，单笔净收益 %s%%。该笔名义仓位 $%s，账户盈亏 $%s；单笔涨幅不能冒充账户收益。" %
            (format(item["entry_price"], ".10g"), format(item["exit_price"], ".10g"), number(item["peak_r"]), number(item["net_r"], signed=True),
             number(100 * item["net_return"], signed=True), number(item["notional"]), number(item["account_pnl"], signed=True)),
            "", "![%s 完整成交与保护路径](%s)" % (item["symbol"], image_ref(item["path"]))]
    if not examples:
        lines += ["", "没有符合上述条件的实际自然退出成交，因此不生成虚构案例图。"]
    cost_rows = [[str(m // 60) + "H", label, number(row.return_pct, signed=True), number(row.stress40_static_pct, signed=True),
        number(row.stress60_static_pct, signed=True), number(100 * row.top1_positive_profit_share), number(100 * row.top5_positive_profit_share)]
        for m, _, account, label, row, _ in account_rows if account == "full"]
    lines += ["", "## 成本、集中度与覆盖局限", "",
        table(["周期", "账户", "基础20bp净收益 %", "静态40bp %", "静态60bp %", "最大1笔占正利润 %", "最大5笔占正利润 %"], cost_rows),
        "", "40/60bp是对已固定成交的成本归因压力测试，不重新分配仓位；不会模拟加成本后改变后续现金、容量及入场排序。基础成本按入场名义计，出场名义手续费差额、资金费率、真实价差及冲击并未完整覆盖。正利润集中度不是全账户净利润占比。",
        "", "每笔不超过账户权益10%，初始风险不超过0.5%，受现金、信号根成交额1%与此前24小时成交额0.1%限制；最多同时10资产。没有杠杆放大，选单优先级只用此前24小时成交额。"]
    market_status = Counter(r["status"] for r in evidence.history["markets"])
    frame_status = Counter(coverage.status)
    missing = sum(len(r.get("missing_months", [])) for r in evidence.history["markets"])
    clean = evidence.history["tradability_audit"]
    kept_rows = sum(int(row["rows"]) for row in evidence.history["segments"])
    removed_rows = sum(int(clean[key]) for key in ("rows_removed_delivery", "rows_removed_leading_zero", "rows_removed_all_zero"))
    if kept_rows + removed_rows != int(clean["source_rows"]) or len(evidence.history["segments"]) != int(clean["output_segments"]):
        raise ValueError("Tradable coverage and removal decomposition do not reconcile")
    lines += ["", table(["覆盖项目", "数量或说明"], [
        ["目录市场数", len(evidence.history["markets"])], ["完整 / 排除 / 空 / 拒绝市场", " / ".join(str(market_status[k]) for k in ("complete", "excluded", "empty", "rejected"))],
        ["清理前源小时", clean["source_rows"]], ["清理后源小时", kept_rows],
        ["已知交割边界移除小时", clean["rows_removed_delivery"]],
        ["其中正成交量小时", clean["positive_rows_removed_delivery"]],
        ["段首零活动占位移除小时", clean["rows_removed_leading_zero"]],
        ["全零活动段移除小时", clean["rows_removed_all_zero"]],
        ["已知上市快照前有正量小时（保留并报告）", clean["positive_rows_before_onboard"]],
        ["清理前 / 后连续1H源段", "%d / %d" % (clean["input_segments"], clean["output_segments"])],
        ["完全移除的源段", clean["wholly_removed_segments"]],
        ["上市快照冲突币数", len(clean["onboard_conflict_symbols"])],
        ["周期段状态", json.dumps(dict(frame_status), ensure_ascii=False)],
        ["记录的缺失月项", missing], ["候选 / 随机控制条数", "%d / %d" % (len(events), len(controls))]]),
        "", "目录是既有档案收录与冻结近期目录的并集，包含部分非交易状态，仍不是完整历史退市全集，存在存活者和档案可得性偏差。上市不足三年的币没有凭空补足三年。缺失不插值，断档拆段并重新预热。上市前或退市后的零成交量固定价占位段不是实际交易覆盖；上表按清理后源段统计，不沿用原markets的archive/recent原始行数。段首零活动和全零段单列剔除，已知交割仅保留完整收盘不晚于交割边界的K线；尚有仓位的段尾估值不冒充结算成交。若上市快照之前已有正量，保留并报告冲突，不能仅凭当前上市快照删掉真实历史。",
        "", "档案的15分钟数据仅以完整四根合成1H；4H仅由完整UTC四根1H合成。档案与近期1H数据只检查接缝相邻，没有共同覆盖窗可作跨源价格一致性验证。历史公开数据的真实发布时间延迟未知，历史tick变化未重建；使用冻结目录步长近似，不能宣称历史撮合精度完全一致。",
        "", "## 风险与诚实声明", "",
        "本次全量结论不能直接与前次61天三交易所研究作因果比较：日期和品种/交易所构成都变了，本次还加入可交易边界清理。此前61天账户的已分配记录按已知币安交割日补查，没有在这些交割日之后入场或继续持有；但旧focus事件和控制中存在未被账户买入的交割后零量记录，因此不能将此前的事件对照称为完全无污染，也不推断其它交易所退市覆盖完整。所有年度都是同一冻结规则的历史切片，后年度不自动成为新的未见样本外。保留亏损、未匹配、少信号、拒单和边界估值，不把峰值R当已兑现收益。",
        "", "## 下一步与待审问题", "",
        "下一步先核对最终表格、图和账户拒单原因，再决定是否开展独立前向观察。本轮保持参数冻结，未据这些结果重选阈值或改变线上策略。需要额外证据的问题包括：扣除资金费率与实际冲击后是否仍成立、未被目录收录的退市币影响、边界估值对结论的影响、少数大赢家和账户容量的贡献。",
        "", "## 复现命令与来源", "", "先提交适配器、研究运行器、报告builder和预登记计划，再执行；输出目录拒绝覆盖旧产物。以下路径默认在仓库根目录。", "", "```bash",
        ".venv/bin/python -m yoyo.data.spike_burst_history --output experiments/active/exp-spike-burst-three-year-20260910-v1/data --start 2023-05-01T00:00:00Z --end 2026-09-09T00:00:00Z",
        "SPIKE_RAW_MANIFEST_SHA=$(.venv/bin/python -c 'import hashlib; from pathlib import Path; print(hashlib.sha256(Path(\"experiments/active/exp-spike-burst-three-year-20260910-v1/data/history_manifest.json\").read_bytes()).hexdigest())')",
        '.venv/bin/python -m yoyo.data.spike_burst_history sanitize --input experiments/active/exp-spike-burst-three-year-20260910-v1/data/history_manifest.json --input-sha "$SPIKE_RAW_MANIFEST_SHA" --output experiments/active/exp-spike-burst-three-year-20260910-v1/data_tradable',
        ".venv/bin/python -m yoyo.evaluation.spike_burst_long_history prepare --history experiments/active/exp-spike-burst-three-year-20260910-v1/data_tradable/history_manifest.json",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_long_history evaluate",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_long_report",
        ".venv/bin/python scripts/md_to_html.py analysis/p1_spike_burst_three_year_20260910.md --out-dir analysis/html", "```", "",
        "来源：已完成的 validation_manifest、其认证的账户/年度/事件/单特征表和账本，以及与之链接的 dataset/history manifest。读取文件和图的SHA记录在 report_manifest.json。主结论由最终审阅者结合全部对照决定。"]
    return "\n".join(lines) + "\n"


def committed_sources(evidence):
    paths = [Path(__file__), Path(case_figures.__file__)]
    for name in ("spike_burst_execution", "spike_burst_replay"):
        relative = "yoyo/evaluation/" + name + ".py"
        path = ROOT / relative
        check(path, evidence.validation["source_hashes"][relative])
        paths.append(path)
    for path in paths:
        saved = subprocess.check_output(["git", "show", "HEAD:" + str(path.resolve().relative_to(ROOT))], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError("Commit exact report/display builder before rendering: " + str(path))
    if pd.Timestamp(case_figures.END) != END:
        raise ValueError("Existing figure helper uses a different final cutoff")
    return [artifact(path) for path in paths]


def run(folder=EXP / "results", report=REPORT, figures=None, summary_file=None):
    folder, report = Path(folder).resolve(), Path(report).resolve()
    figures = Path(figures).resolve() if figures else folder / "report_figures"
    receipt = folder / "report_manifest.json"
    if report.exists() or figures.exists() or receipt.exists():
        raise ValueError("Refusing frozen report/figure overwrite")
    evidence = Evidence(folder)
    sources = committed_sources(evidence)
    summary = Path(summary_file).read_text() if summary_file else None
    figures.mkdir(parents=True)
    case_figures.style()
    curve = neutral_curves(evidence, figures)
    examples = render_cases(evidence, figures)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_text(evidence, curve, examples, report, summary))
    evidence.finish()
    for item in sources:
        check(item["path"], item["sha256"])
    manifest = dict(status="complete", generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        sources=sources, input_artifacts=list(evidence.read_sources.values()),
        config=CONFIG, full_study_rescored=False, selected_display_paths_replayed=True,
        display_path_reconciliation="Existing helper only; selected frozen fills checked field-by-field",
        examples=[dict(item, path=str(item["path"].resolve())) for item in examples],
        chart_contract=dict(maximum_pngs=6, account_chart="saved continuous NAV and drawdown; linear shared axes",
            examples="actual SPIKE altcoin natural fills; highest/lowest PnL per timeframe plus global largest giveback",
            retrospective_selection=True, no_rule_selection=True),
        artifacts=[artifact(path) for path in [report, curve] + [item["path"] for item in examples]])
    if summary_file:
        manifest["reviewer_summary"] = artifact(summary_file)
    receipt.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=EXP / "results")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--summary-file", type=Path)
    args = parser.parse_args()
    run(args.results, args.report, args.figures, args.summary_file)
