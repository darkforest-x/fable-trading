"""Render the frozen stage-separated SPIKE study, never replaying or scoring.

All labels, arrows, parent links and trade outcomes come from authenticated
receipts. Historical feature pickles are authenticated before deserialization.
The owner cases always show the complete 96 hourly opening bars Aug18--21 BJT.
A failure is selected deterministically, after the study: the lowest saved net
return among valid natural early-warning events, event_id breaking ties.
Shared price anchors make coverage partly mechanical; no account PnL, successful
prediction, or production readiness is inferred from a high recall number.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-burst-early-warning-20260910-v3"
PRIOR = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2/results"
PRIOR_VALIDATION_SHA = "7a88b0f3603281d051c368c1f59d5a0def18f0d2636fa07e40e634b1428827a9"
REPORT = ROOT / "analysis/p1_spike_burst_early_warning_20260910.md"
HOUR = pd.Timedelta(hours=1)
TARGET_OPENS = (pd.Timestamp("2026-08-19T14:00Z"), pd.Timestamp("2026-08-19T15:00Z"))
MA_COLORS = {"s20": "#268F85", "e20": "#73B6A9", "s60": "#4B78AF",
             "e60": "#88A7D1", "s120": "#525F68", "e120": "#9CA5AA"}
ARMS = ("early", "confirmed")
ARM_NAMES = {"v1": "V1 原版", "v2": "V2 渐进", "early": "结构早预警", "confirmed": "后续动能确认"}
DATETIME_COLUMNS = ("bar_open", "decision_time", "confirmed_at", "entry_time", "exit_time",
                    "exit_time_lower", "exit_time_upper", "parent_time", "parent_decision_time",
                    "parent_bar_open", "confirm_time", "open_time")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), size_bytes=path.stat().st_size)


def check(path, expected):
    path = Path(path).resolve()
    if not path.is_file() or sha(path) != expected:
        raise ValueError("Missing or changed authenticated report input: " + str(path))
    return path


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def num(value, digits=2):
    return "不适用" if value is None or pd.isna(value) else format(float(value), ",." + str(digits) + "f")


def pct(value):
    return num(100 * value) + "%" if value is not None and pd.notna(value) else "不适用"


def ratio(hits, total):
    return "%s（%d/%d）" % (pct(hits / total), hits, total) if total else "不适用（0/0）"


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "／").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| " + " | ".join(["---"] * len(headers)) + " |"] +
                     ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows])


def bjt(stamp):
    return "无" if pd.isna(stamp) else pd.Timestamp(stamp).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def flag(values):
    """CSV booleans must not turn the string 'False' into Python True."""
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    mapped = values.map({True: True, False: False, "True": True, "False": False, "true": True, "false": False})
    if mapped.isna().any() and values.notna().any():
        bad = values.notna() & mapped.isna()
        if bad.any():
            raise ValueError("Unexpected saved boolean values")
    return mapped.fillna(False).astype(bool)


class Evidence:
    """Authenticate all consumed artifacts and sources against frozen receipts."""
    def __init__(self, folder, expected_validation_sha=None):
        self.folder, self.read, self.cache = Path(folder).resolve(), {}, {}
        vp = self.folder / "validation_manifest.json"
        if expected_validation_sha:
            check(vp, expected_validation_sha)
        self.validation = json.loads(vp.read_text())
        pp = check(self.folder / "prepared_manifest.json", self.validation["prepared_manifest_sha256"])
        self.prepared = json.loads(pp.read_text())
        if any(receipt.get("status") != "complete" for receipt in (self.prepared, self.validation)):
            raise ValueError("Completed preparation and evaluation required")
        if self.prepared["source_pins"] != self.validation["source_pins"] or self.prepared["config"] != self.validation["config"]:
            raise ValueError("Preparation and evaluation configuration differ")
        self.config, self.allowed = self.prepared["config"], {}
        for receipt in (self.prepared, self.validation):
            for item in receipt["artifacts"]:
                self.allow(item["path"], item["sha256"])
        for path, expected in self.prepared["sources"].items():
            self.allow(path, expected)
        for path in (vp, pp):
            self.read[str(path)] = artifact(path)
        self.jobs = json.loads(self.path("matching.json").read_text())["jobs"]
        for job in self.jobs:
            if self.allowed.get(str(Path(job["features_path"]).resolve())) != job["features_sha256"]:
                raise ValueError("Chart source is not authenticated by the study")

    def allow(self, path, expected):
        key = str(Path(path).resolve())
        if key in self.allowed and self.allowed[key] != expected:
            raise ValueError("Conflicting source identities")
        self.allowed[key] = expected

    def path(self, name):
        path = Path(name)
        path = path.resolve() if path.is_absolute() else (self.folder / path).resolve()
        key = str(path)
        if key not in self.allowed:
            raise ValueError("Input not registered by frozen receipts: " + key)
        check(path, self.allowed[key])
        self.read[key] = artifact(path)
        return path

    def csv(self, name):
        path = self.path(name)
        if str(path) not in self.cache:
            frame = pd.read_csv(path)
            for column in DATETIME_COLUMNS:
                if column in frame:
                    frame[column] = pd.to_datetime(frame[column], utc=True)
            self.cache[str(path)] = frame
        return self.cache[str(path)].copy()

    def frame(self, job):
        frame = pd.read_pickle(self.path(job["features_path"]))
        index = frame.index
        if (not isinstance(index, pd.DatetimeIndex) or index.tz is None or not index.is_unique
                or not index.is_monotonic_increasing or index.hasnans):
            raise ValueError("Invalid frozen source clock")
        return frame

    def finish(self):
        for item in self.read.values():
            check(item["path"], item["sha256"])


def committed(evidence):
    paths = [Path(__file__), ROOT / "scripts/md_to_html.py", EXP / "PROJECT_PLAN.md"]
    for relative, expected in evidence.prepared["source_pins"].items():
        path = check(ROOT / relative, expected)
        paths.append(path)
    for path in dict.fromkeys(paths):
        relative = str(path.resolve().relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact report builder/plan before rendering: " + relative)
    return [artifact(path) for path in dict.fromkeys(paths)]


def style():
    path = Path("/System/Library/Fonts/STHeiti Light.ttc")
    if path.is_file():
        font_manager.fontManager.addfont(str(path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "figure.facecolor": "#f6f8fa", "axes.facecolor": "white",
        "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#c4cdd3",
        "grid.color": "#e3e9ed", "grid.alpha": .8, "font.size": 10})


def chart(frame, signals, left, right, title, output, footnotes, focus=None, trade=None):
    """Draw only saved features and saved actual decision times, not new signals."""
    f, x = frame.iloc[left:right], np.arange(left, right)
    if f.empty:
        raise ValueError("Empty figure window")
    fig, (ax, vol, lower) = plt.subplots(3, 1, figsize=(17, 10.5), sharex=True, height_ratios=[3.6, .8, 1.25])
    span = float(f.high.max() - f.low.min())
    min_height = max(span * 1e-5, np.finfo(float).eps * float(f.close.max()))
    colors = np.where(f.close.ge(f.open), "#008E7C", "#CD5369")
    for i, row, color in zip(x, f.itertuples(), colors):
        ax.vlines(i, row.low, row.high, color=color, linewidth=.8)
        ax.add_patch(Rectangle((i-.32, min(row.open, row.close)), .64, max(abs(row.close-row.open), min_height),
                              facecolor=color, edgecolor=color, linewidth=.4))
    for name, color in MA_COLORS.items():
        ax.plot(x, f[name], color=color, linewidth=1, label=name.upper())
    for arm, color, offset, marker in (("v2", "#A078BD", .04, "v"), ("early", "#D18A24", .085, "o"), ("confirmed", "#087F87", .13, "^")):
        part = signals.loc[signals.arm.eq(arm) & signals.decision_i.ge(left) & signals.decision_i.lt(right)]
        indices = part.decision_i.astype(int).to_numpy()
        for row in part.itertuples():
            if pd.Timestamp(row.decision_time) != frame.index[int(row.decision_i)] + HOUR:
                raise ValueError("Figure signal timestamp differs from frozen opening bar")
        label = ARM_NAMES[arm] + ("：本窗口无新标记" if not len(indices) else "（实际收盘确认）")
        if len(indices):
            ax.scatter(indices, frame.low.iloc[indices].to_numpy() - span * offset, marker=marker, s=72,
                       color=color, zorder=6, label=label)
        else:
            ax.plot([], [], color=color, marker=marker, linestyle="none", label=label)
    if focus:
        for n, stamp in enumerate(focus):
            matches = np.flatnonzero(frame.index == stamp)
            if len(matches) != 1:
                raise ValueError("Missing owner-specified bar")
            i = int(matches[0])
            for a in (ax, vol, lower):
                a.axvline(i, color="#BE8837", linewidth=.85, linestyle="--")
            ax.annotate("开盘 " + bjt(stamp)[5:] + "\n确认 " + bjt(stamp+HOUR)[5:], xy=(i, frame.high.iloc[i]),
                xytext=(10, 42 + 40*n), textcoords="offset points", fontsize=9, color="#8C6527",
                arrowprops=dict(arrowstyle="-", color="#BE8837"))
    if trade is not None:
        entry_i, exit_i = int(trade.entry_i), int(trade.exit_i)
        ax.axvspan(int(trade.decision_i)+.5, exit_i+.5, color="#E9F2FF", alpha=.55,
                   label="本事件后续：预警当时未知")
        ax.scatter([entry_i], [trade.entry_price], marker="^", s=120, color="#254EAB", zorder=7, label="次根开盘模拟入场")
        ax.scatter([exit_i], [trade.exit_price], marker="X", s=105, color="#AE334C", zorder=7, label="保存的保护退出")
        ax.hlines(trade.initial_stop, entry_i, exit_i, color="#C9717F", linestyle=":", linewidth=1, label="冻结初始止损")
    vol.bar(x, f.volume, color=colors, width=.7, alpha=.65)
    vol.set_ylabel("成交量")
    lower.plot(x, f.md, color="#4778BA", linewidth=1.65, label="IMACD")
    lower.plot(x, f.sb, color="#C18B35", linewidth=1.4, label="信号线")
    lower.axhline(0, color="#626E75", linewidth=1)
    for a in (ax, vol, lower):
        a.grid(axis="y"); a.set_xlim(left-.8, right-.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: format(y, ".7g")))
    ax.set_title(title, loc="left", fontweight="bold", fontsize=15)
    ax.legend(loc="upper left", fontsize=8, ncol=3)
    lower.legend(loc="upper left", fontsize=9)
    ticks = np.unique(np.linspace(left, right-1, 9).astype(int))
    lower.set_xticks(ticks)
    lower.set_xticklabels([frame.index[k].tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for k in ticks])
    lower.set_xlabel("2026年北京时间 · 横轴为开盘时刻；每个标记最早在该根收盘（+1小时）确认，后续确认不回填")
    fig.text(.045, .025, "\n".join(footnotes), fontsize=9, color="#52616C", linespacing=1.65)
    fig.tight_layout(rect=[0, .085 + .018*max(0, len(footnotes)-2), 1, 1])
    fig.savefig(output, dpi=155)
    plt.close(fig)
    return Path(output)


def verify_saved_links(evidence, signals):
    """Validate saved linkage and clocks only; never evaluate feature gates."""
    if set(signals.arm.unique()) - set(ARMS) or signals.event_id.duplicated().any():
        raise ValueError("Unexpected signal arm or duplicate event identity")
    registry = evidence.csv("parent_registry.csv.gz")
    if registry.event_id.duplicated().any():
        raise ValueError("Duplicate causal parent registry identity")
    early = registry.set_index("event_id")
    children = signals.loc[signals.arm.eq("confirmed")]
    if children.parent_event_id.duplicated().any():
        raise ValueError("A parent has more than one saved confirmation")
    for row in signals.itertuples():
        if row.decision_time != row.bar_open + HOUR:
            raise ValueError("Signal confirmed before its bar closed")
        if row.parent_decision_time > row.decision_time:
            raise ValueError("A child precedes its parent")
        if row.arm == "early":
            if row.parent_event_id != row.event_id or row.parent_i != row.decision_i:
                raise ValueError("Early parent identity mismatch")
        else:
            age = int(row.decision_i) - int(row.parent_i)
            if age not in (0, 1, 2, 3) or row.confirm_age != age or row.decision_time-row.parent_decision_time != age*HOUR:
                raise ValueError("Saved child delay violates the frozen 0..3 window")
            if row.parent_event_id not in early.index:
                raise ValueError("Missing parent in complete causal registry")
            else:
                parent = early.loc[row.parent_event_id]
                if (parent.instrument != row.instrument or parent.decision_i != row.parent_i
                        or parent.decision_time != row.parent_decision_time
                        or parent.frozen_parent_high != row.frozen_parent_high):
                    raise ValueError("Child lost its frozen parent boundary")


def render_cases(evidence, prior, signals, trades, figures):
    old_signals = prior.csv("signals.csv.gz")
    old_signals = old_signals.loc[old_signals.arm.eq("v2")]
    all_signals = pd.concat([old_signals, signals], ignore_index=True)
    snapshots = evidence.csv("case_slices.csv")
    examples = []
    start = pd.Timestamp("2026-08-18T00:00:00+08:00").tz_convert("UTC")
    end = start + pd.Timedelta(days=4)
    for asset in ("HYPE", "NEAR", "PEPE"):
        jobs = [job for job in evidence.jobs if job["asset"] == asset]
        if len(jobs) != 1:
            raise ValueError("Exactly one authenticated source required for " + asset)
        job = jobs[0]
        frame = evidence.frame(job)
        idx = np.flatnonzero((frame.index >= start) & (frame.index < end))
        if len(idx) != 96 or not np.all(np.diff(frame.index[idx].asi8) == HOUR.value):
            raise ValueError("Case must contain all 96 contiguous opening bars")
        part = all_signals.loc[all_signals.instrument.eq(job["instrument"])]
        saved = snapshots.loc[snapshots.instrument.eq(job["instrument"])]
        if len(saved) != 96 or not saved.bar_open.sort_values().reset_index(drop=True).equals(pd.Series(frame.index[idx])):
            raise ValueError("Saved case slices differ from full chart window")
        targets = saved.loc[saved.bar_open.isin(TARGET_OPENS)].sort_values("bar_open")
        if len(targets) != 2:
            raise ValueError("Missing specified 22:00/23:00 case row")
        notes = []
        for row in targets.itertuples():
            direct = part.loc[part.decision_i.eq(row.decision_i)]
            types = [ARM_NAMES[arm] for arm in ("v2", "early", "confirmed") if direct.arm.eq(arm).any()]
            notes.append("%s 开盘 → %s 收盘确认｜保存的本根标记：%s" %
                         (bjt(row.bar_open)[5:], bjt(row.confirmed_at)[5:], " / ".join(types) or "无"))
        notes += ["金色圆点=结构早预警；青色三角=后续动能确认；紫色倒三角=旧 V2。三者按各自真实确认根绘制。",
                  "全图含后续走势供复盘，预警/确认只使用当时已收盘数据；早预警不是验证过的买点，确认也不是盈利保证。"]
        path = chart(frame, part, int(idx[0]), int(idx[-1])+1,
            "OKX · %s · 1H｜8月18—21日完整96根｜早预警与后续确认分开" % asset,
            figures/(asset.lower()+"_96bars.png"), notes, focus=TARGET_OPENS)
        arrows = part.loc[part.decision_i.isin(idx)].sort_values(["decision_time", "arm"])
        # A fixed local case window distinguishes nearby launches from prior unrelated warnings.
        around = arrows.loc[arrows.bar_open.ge(pd.Timestamp("2026-08-19T06:00Z"))
                            & arrows.bar_open.lt(pd.Timestamp("2026-08-20T02:00Z"))]
        examples.append(dict(asset=asset, path=str(path), instrument=job["instrument"],
            targets=targets.to_dict("records"), arrows=arrows.to_dict("records"), around_arrows=around.to_dict("records")))
    failures = trades.loc[trades.arm.eq("early") & flag(trades.valid) & flag(trades.natural_exit)
                          & pd.to_numeric(trades.net_return).lt(0)]
    failure = None
    if len(failures):
        row = failures.sort_values(["net_return", "event_id"], ascending=[True, True]).iloc[0]
        job = next(job for job in evidence.jobs if job["instrument"] == row.instrument)
        frame = evidence.frame(job)
        if pd.Timestamp(row.entry_time) != frame.index[int(row.entry_i)]:
            raise ValueError("Failure saved next-open entry clock differs from source")
        left, right = max(0, int(row.decision_i)-100), min(len(frame), int(row.exit_i)+25)
        notes = ["事后固定选择：有效、自然退出、净收益最低的 early 事件，event_id 破同分；不是账户最大亏损。",
            "入场 %s｜退出区间 %s—%s｜峰值 %sR，扣成本结果 %sR；单笔净收益 %s" %
            (bjt(row.entry_time), bjt(row.exit_time_lower), bjt(row.exit_time_upper), num(row.peak_r), num(row.net_r), pct(row.net_return)),
            "保持与主研究相同退出和20bp往返成本；单笔事件可与其他事件重叠，不代表真实账户执行。"]
        path = chart(frame, all_signals.loc[all_signals.instrument.eq(job["instrument"])], left, right,
                      "早预警失败案例 · %s｜完整模拟持有路径" % row.asset,
                      figures/"failure_early.png", notes, trade=row)
        failure = dict(event_id=row.event_id, asset=row.asset, instrument=row.instrument, path=str(path),
            net_return=float(row.net_return), net_r=float(row.net_r), peak_r=float(row.peak_r),
            entry_time=row.entry_time, exit_time_lower=row.exit_time_lower, exit_time_upper=row.exit_time_upper,
            selection="Post-hoc lowest net_return among valid natural early events; event_id tie break")
    return examples, failure


def recall_rows(frame):
    return [[row.period, ARM_NAMES[row.arm], int(row.positive_events), ratio(row.hits_0,row.positive_events),
             ratio(row.hits_1,row.positive_events), ratio(row.hits_2,row.positive_events), ratio(row.hits_6,row.positive_events),
             int(row.signals), pct(row.precision_all)] for row in frame.itertuples()]


def trade_rows(frame):
    return [[row.period, ARM_NAMES[row.arm], int(row.valid), int(row.invalid), int(row.natural_exits), int(row.censored),
             ratio(row.wins,row.valid), ratio(row.natural_wins,row.natural_exits), num(row.mean_net_bp), int(row.matched),
             num(row.paired_actual_net_bp), num(row.paired_random_net_bp), num(row.mean_excess_bp),
             num(row.asset_balanced_excess_bp), num(row.permutation_p,6), num(row.holm_p,6)] for row in frame.itertuples()]


def report_text(evidence, prior, examples, failure, report):
    recall = evidence.csv("recall_summary.csv")
    old_recall = prior.csv("recall_summary.csv")
    signals = evidence.csv("signals.csv.gz")
    outcomes = evidence.csv("trade_summary.csv")
    scores = evidence.csv("score_summary.csv")
    breadth = evidence.csv("breadth.csv.gz")
    labels, prior_labels = evidence.csv("labels.csv.gz"), prior.csv("labels.csv.gz")
    pd.testing.assert_frame_equal(labels, prior_labels, check_dtype=False, check_exact=True)
    if evidence.prepared.get("label_source_sha256") != sha(prior.path("labels.csv.gz")):
        raise ValueError("Reused label source hash mismatch")
    if sha(evidence.path("labels.csv.gz")) != sha(prior.path("labels.csv.gz")):
        raise ValueError("Original frozen label bytes were not preserved")
    full = recall.loc[recall.period.eq("full")].set_index("arm")
    if set(full.index) != set(ARMS) or full.index.duplicated().any():
        raise ValueError("Exactly two primary full-period arms required")
    for arm in ARMS:
        row = full.loc[arm]
        if bool(row.target_80_met) != bool(row.positive_events and row.hits_1 / row.positive_events >= .8):
            raise ValueError("Saved 80% result disagrees with frozen counts")
    primary = full.loc["early"]
    confirmed = full.loc["confirmed"]
    early_trade = outcomes.loc[outcomes.arm.eq("early") & outcomes.period.eq("full")].iloc[0]
    rel = lambda p: os.path.relpath(Path(p).resolve(), report.parent)
    lines = ["# SPIKE V3：结构早预警与动能确认分层", "", "## 先回答能否实现", "",
        "可以实现更早、更广的结构预警。本次固定规则在原价格代理分母上，早预警 +1 根召回 **%s**，后续确认 **%s**；**%s80%%早预警覆盖目标**。这不是80%%胜率，也不是80%%行情都能赚钱持有。" %
        (ratio(primary.hits_1,primary.positive_events),ratio(confirmed.hits_1,confirmed.positive_events),
         "达到本次回顾口径的" if bool(primary.target_80_met) else "尚未达到"), "",
        "全期共 %d 条早预警，平均全池每日 %s 条；后续确认 %d 条。早预警事件匹配精确率 %s，后续确认 %s。覆盖与打扰必须同时看，提醒数增加本身不代表质量提高。" %
        (primary.signals,num(primary.alerts_per_day),confirmed.signals,pct(primary.precision_all),pct(confirmed.precision_all)), "",
        "早预警模拟事件相对匹配随机平均超额 %s bp；资产均衡超额 %s bp，两臂主检验 Holm p=%s。该结果按事件计算，没有计算账户收益；是否存在可靠交易优势要结合下文对照与自然退出看。" %
        (num(early_trade.mean_excess_bp),num(early_trade.asset_balanced_excess_bp),num(early_trade.holm_p,6)), "",
        "**重要限制：价格突破预警与评价标签共享‘突破前12根高点’锚点。** 广覆盖有结构性原因，不是独立证明预测能力。若把全部同锚点突破按同样24根去重都通知，近100%召回可由定义直接得到，那只是机械上界，不是本轮候选成功。此次保留快均线和12根预警冷却，并同时评价所有提醒的代价与随机对照。", "",
        "本次是既有278个OKX 1H历史来源、UTC [2026-07-10,2026-09-09) 的61天回顾；来源预热历史一并保留，BTC/ETH仍为原池背景排除。不是当前全市场普查，也不是新盲测。本配置首次消耗holdout（固定边界2026-05-04），不代表首次见到这段历史。", "",
        "## 原版对照：同一标签，不改分母", ""]
    key_periods = ("full", "case_night")
    comparison = pd.concat([old_recall.loc[old_recall.period.isin(key_periods)],recall.loc[recall.period.isin(key_periods)]])
    headers = ["期间","版本","正事件数","当根","+1根","+2根","+6根","提醒数","事件匹配精确率"]
    lines += [table(headers,recall_rows(comparison)), "",
        "案例夜按确认时间北京时间 **[8月19日18:00,8月20日06:00)**，右端不含。横轴的22:00开盘1H根最早23:00确认；23:00开盘根最早次日00:00确认。旧V1/V2是原实验保存数字，原随机对照与这次两臂的联合候选匹配不同，不冒充同一批控制。", "",
        "原事件：首次收盘突破此前12根高点，在所有候选上因果去重24根，再观察未来完整24根收盘是否先达到+4ATR、未先达到−2ATR。该分母没有要求密集或全上六线，不能称为%d个均线密集金标。%d个未知事件不被写成负例。新标记只按其实际确认时刻匹配，既有参考仓不冒充新预警。" %
        (primary.positive_events,primary.unknown_events), "",
        "## 强劲上涨子组：未来峰值至少8%", "",
        "这是上一轮已固定的描述子组：仍须满足原正事件条件，并且未来24根最高价相对事件收盘至少+8%。不替换主分母，不按涨幅榜重新选币，不宣称8%是最优门槛。峰值不是可实现收益。", "",
        table(["期间","版本","≥8%正事件数","+1根召回"],
            [[row.period,ARM_NAMES[row.arm],int(row.large_positive_events),pct(row.large_recall_1)] for row in comparison.itertuples()]), "",
        "## 提醒负担与延迟", ""]
    burden=[]
    for row in recall.loc[recall.period.isin(key_periods)].itertuples():
        duplicate_density=2400*row.duplicates/row.eligible_asset_hours if row.eligible_asset_hours else np.nan
        burden.append([row.period,ARM_NAMES[row.arm],int(row.signals),num(row.alerts_per_day),num(row.alerts_per_100_asset_days),
            int(row.unique_matched),int(row.unmatched),int(row.duplicates),int(row.unknown_signals),pct(row.precision_adjudicated),
            num(row.false_alerts_per_100_asset_days),num(duplicate_density),num(row.matched_median_lag),num(row.matched_p90_lag)])
    lines += [table(["期间","版本","全部","全池每天","每100合约日全部","唯一匹配","无匹配","重复","未知","已判断精确率",
                     "每100合约日无匹配","每100合约日重复","匹配延迟中位根","延迟P90"],burden), "",
        "唯一匹配只在原正事件[0,+6]窗口内，一事件取最早一条；重复不重复增加召回。无匹配不是每条都亏钱，但不能冒充捕获定义内的启动。全部精确率保留未知提醒；已判断精确率排除未知；无匹配密度与重复密度分开给出。", ""]
    delay=[]
    for arm in ARMS:
        part=signals.loc[signals.arm.eq(arm)&signals.match_status.eq("matched")]
        children=signals.loc[signals.arm.eq(arm)]
        delay.append([ARM_NAMES[arm],len(part),num(part.move_since_anchor_pct.median()),num(part.move_since_anchor_atr.median()),
                      num(children.confirm_age.median()) if arm=="confirmed" else "不适用"])
    lines += [table(["版本","匹配条数","相对事件锚点追价中位%","推进中位ATR","父子确认等待中位根"],delay), "",
        "## 父子时钟与案例逐根核对", "",
        "早预警为完整条件的上升沿：ready、收盘突破前12根高点且站上SMA20/EMA20；接纳预警至少隔12根，与参考持仓独立。冷却中出现但未接纳的上升沿不延期补发。",
        "确认须有父预警，只允许父根到后3根，收盘仍高于父根冻结突破边界，近期密集、三根推进≥1.5ATR、三根量比≥1.5、MD≥SB、ZLEMA上升。一父最多确认一次；过期不复活，实际确认根和价格不回填。", ""]
    case_rows=[]
    first_rows=[]
    for example in examples:
        for target in example["targets"]:
            direct=[row for row in example["arrows"] if row["decision_i"]==target["decision_i"]]
            child=next((row for row in direct if row["arm"]=="confirmed"),None)
            case_rows.append([example["asset"],bjt(target["bar_open"]),bjt(target["confirmed_at"]),format(target["close"],".8g")]+[
                "有" if any(row["arm"]==arm for row in direct) else "无" for arm in ("v2","early","confirmed")]+
                [bjt(child["parent_decision_time"]) if child else "—",int(child["confirm_age"]) if child else "—"])
        for arm in ("v2","early","confirmed"):
            arrows=[row for row in example["around_arrows"] if row["arm"]==arm]
            first=min(arrows,key=lambda row:row["decision_time"]) if arrows else None
            first_rows.append([example["asset"],ARM_NAMES[arm],len(arrows),bjt(first["bar_open"]) if first else "无",
                bjt(first["decision_time"]) if first else "无",format(first["signal_close"],".8g") if first else "—"])
    lines += [table(["币种","目标根开盘BJT","最早确认BJT","收盘价","旧V2","早预警","后续确认","确认的父预警时刻","等待根数"],case_rows), "",
        "下表‘附近’固定为开盘北京时间[8月19日14:00,8月20日10:00)，不从整段96根中挑更早但无关的预警冒充本次启动。", "",
        table(["币种","版本","附近条数","附近首根开盘","实际确认时刻","当根收盘价"],first_rows), ""]
    children=signals.loc[signals.arm.eq("confirmed")]
    lines += [table(["父子等待根数","确认条数"],[[age,int(children.confirm_age.eq(age).sum())] for age in range(4)]), ""]
    for example in examples:
        lines += ["### %s：完整96根全局图"%example["asset"], "", "![%s 全局图](%s)"%(example["asset"],rel(example["path"])), ""]
    lines += ["## 单笔事件结果与匹配随机对照", "",
        "两臂使用同一未改动的次根真实开盘模拟：初始5根低点−0.2ATR与信号收盘−2ATR取更宽者，达到2R后采用4ATR跟踪且下一根生效，无固定止盈，固定20bp往返成本。资金费率、真实冲击未建模。允许事件重叠，所以以下不是账户收益或账户回撤。", ""]
    trade_headers=["期间","版本","有效","无效","自然退出","边界估值","全部净胜率","自然净胜率","均净bp","配对n","配对实际净bp",
                   "随机净bp","均超额bp","资产均衡超额bp","置换p","两臂Holm p"]
    lines += [table(trade_headers,trade_rows(outcomes.loc[outcomes.period.isin(("full","first31","last30","case_night"))])), "",
        "随机入口在同币×UTC周×因果ATR%桶中匹配最多3个，排除两新臂当前或过去12根信号，缺配保留。共同候选已全局冻结；不扩大池子凑控制。同期资产共振、同币重复事件及控制复用仍造成相关性。Holm只修正预登记的两臂全期主检验，其他期间为描述。", "",
        "另给每个提醒之后完整24根的+4ATR先于−2ATR结果，避免把与稀疏事件锚点未匹配直接解释成失败；该数同样不是扣费交易胜率。", "",
        table(["期间","版本","未来窗完整n","自身未来障碍成功率","随机未来窗n","随机障碍成功率"],
              [[row.period,ARM_NAMES[row.arm],int(row.forward_known),pct(row.forward_success_rate),int(row.control_forward_known),pct(row.control_forward_success_rate)]
               for row in outcomes.loc[outcomes.period.isin(key_periods)].itertuples()]), "",
        "### 原版收益背景：保留原随机控制", "",
        table(trade_headers,trade_rows(prior.csv("trade_summary.csv").loc[lambda f:f.period.eq("full")])), "",
        "旧表只保留原实验事实；两轮条件池和随机控制不同，不能把两个均超额之差解释为严格配对因果提升。", "",
        "### 单特征排序基线", "",
        "本轮没有模型训练或参数拟合，因此val AUC不适用；下表仅是已见事件上量比/TR排序的描述AUC和最高10%结果，不是可部署筛选结论。", "",
        table(["版本","特征","n","top10%n","描述AUC","top毛bp","top净bp","top净胜率","配对实际bp","随机bp","超额bp"],
            [[ARM_NAMES[row.arm],row.feature,int(row.n),int(row.top_n),num(row.descriptive_auc,4),num(row.top_gross_bp),num(row.top_net_bp),
              pct(row.top_win_rate),num(row.top_matched_actual_bp),num(row.top_random_bp),num(row.top_excess_bp)] for row in scores.itertuples()]), "",
        "## 失败预警：同样展示后续", ""]
    if failure:
        lines += ["固定事后选取全部有效且自然退出的early事件中最低net_return，event_id破同分。%s：净结果%sR、%s，峰值%sR。不是最差账户交易，峰值也不是实际落袋。" %
                  (failure["asset"],num(failure["net_r"]),pct(failure["net_return"]),num(failure["peak_r"])), "",
                  "![失败早预警完整路径](%s)"%rel(failure["path"]), ""]
    else:
        lines += ["本次保存结果没有有效自然退出且净亏损的early事件，未伪造失败图；不能据此忽略尾部未知和边界估值。", ""]
    lines += ["## 逐周稳定性与全部召回口径", "",table(headers,recall_rows(recall)), "",
        "前31天/后30天按8月10日UTC划分，周为UTC周并裁到研究窗口；全为已见回顾，不把后30天称新样本外。", "",
        "## 同小时市场广度：只作背景", "",
        "广度=该已收盘小时可用ready合约中，同时站上SMA20/EMA20且收盘高于前收的比例。只用各时点真实有数据的合约，公开分母，不以未来仍上市币补全，也未用广度过滤这轮信号。因此这里不能据此宣称‘市场共振预测有效’。", ""]
    selected_breadth=breadth.loc[breadth.decision_time.isin([stamp+HOUR for stamp in TARGET_OPENS])]
    if len(selected_breadth)!=2:
        raise ValueError("Both owner specified confirmation hours require breadth denominators")
    lines += [table(["确认时刻BJT","可用分母","上两快线数","正收益数","同时满足数","同时满足比例"],
        [[bjt(row.decision_time),int(row.valid_denominator),int(row.above_fast),int(row.positive_return),int(row.joint),pct(row.share_joint)] for row in selected_breadth.itertuples()]), "",
        "## 风险与诚实声明", "",
        "- 80%若达到，仅表示当前价格代理正事件的早预警覆盖，不表示密集金标、后续确认、胜率或赚钱目标达到80%。",
        "- 共享价格锚点让覆盖有机械成分；质量标签没有看结果后选阈值，市场广度未被验证为过滤器。",
        "- 高绝对涨幅子组保持之前的≥8%定义；不能从这次最好的子组倒推出接下来山寨季必然有效。",
        "- 只验证OKX历史1H多头；没有把结论扩展到4H、空头、其他交易所、所有历史上市合约或真实盘口。",
        "- 假信号、控制缺配、自然退出与尾部估值同时报告；交易所资金费率、盘口滑点与组合容量仍可能改变实盘结果。",
        "- 这份报告及独立Pine不切换线上监控、Bark、TG、ACTIVE或真实仓位；旧V2和旧报告保留。", "",
        "## 复现与证据", "", "先提交固定计划、引擎及报告builder，认证现有来源后分阶段运行；输出目录必须为空，不覆盖已有结果。", "", "```bash",
        "cd /Users/zhangzc/fable-trading",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_early_warning prepare",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_early_warning evaluate",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_early_warning_report",
        "```", "",
        "数据依赖是已认证V2来源和标签；本报告不下载或修改来源、不调用信号回放或交易模拟。prepared/validation两份manifest认证读入，pickle在反序列化前验SHA；完整输入输出身份见同目录report_manifest.json。已有结果不重跑；若需从零复现，应先单独登记全新输出目录。", "",
        "## 下一步", "",
        "保留这一版规则，逐条审阅早预警与后续确认的差别，再用新的时间段检查覆盖、提醒负担与随机超额。是否将广度变成过滤、是否接入通知、是否修改风险或成本需要独立决策；本轮不在同一批历史上继续刷阈值。", ""]
    return "\n".join(lines)


def run(folder=EXP/"results", report=REPORT):
    folder, report = Path(folder).resolve(), Path(report).resolve()
    html = ROOT/"analysis/html"/(report.stem+".html")
    receipt, figures = folder/"report_manifest.json", folder/"report_figures"
    if receipt.exists() or report.exists() or html.exists() or (figures.exists() and any(figures.iterdir())):
        raise ValueError("Refusing to overwrite a report or prior figure artifact")
    evidence = Evidence(folder)
    prior = Evidence(PRIOR, PRIOR_VALIDATION_SHA)
    if evidence.config.get("schema") != "spike-early-warning-v3" or evidence.config.get("arms") != list(ARMS):
        raise ValueError("Report requires the frozen early-warning V3 experiment")
    sources = committed(evidence)
    signals, trades = evidence.csv("signals.csv.gz"), evidence.csv("trade_events.csv.gz")
    verify_saved_links(evidence,signals)
    figures.mkdir(parents=True,exist_ok=True)
    style()
    examples,failure = render_cases(evidence,prior,signals,trades,figures)
    text = report_text(evidence,prior,examples,failure,report)
    evidence.finish(); prior.finish()
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(text)
    subprocess.run([sys.executable,str(ROOT/"scripts/md_to_html.py"),str(report),"--out-dir",str(html.parent)],cwd=ROOT,check=True)
    if not html.is_file():
        raise ValueError("HTML conversion did not produce output")
    evidence.finish(); prior.finish()
    for source in sources:
        check(source["path"],source["sha256"])
    images=[Path(item["path"]) for item in examples]+([Path(failure["path"])] if failure else [])
    manifest=dict(status="complete",config=evidence.config,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        code_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        sources=sources,input_artifacts=list(evidence.read.values())+list(prior.read.values()),
        no_signal_replay=True,no_trade_scoring=True,no_account_returns=True,
        shared_anchor_recall_is_not_predictive_validation=True,
        chart_contract="Three complete96bar cases, saved V2/early/confirmed markers at actual close; deterministic worst natural early failure",
        examples=examples,failure=failure,artifacts=[artifact(path) for path in [report,html]+images])
    receipt.write_text(json.dumps(clean(manifest),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    return manifest


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,default=EXP/"results")
    parser.add_argument("--report",type=Path,default=REPORT)
    args=parser.parse_args()
    run(args.results,args.report)
