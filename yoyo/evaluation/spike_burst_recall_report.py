"""Render authenticated V2 recall/event evidence without replaying or scoring.

The denominator, matching, indicators, arrows and trade outcomes are read from
frozen receipts; this module never calls replay(), label_events(), or execution.
Three owner-specified cases always show all96 BJT opening bars Aug18--21, six
moving averages, volume and zero-axis IMACD. One post-hoc failure is selected by
lowest saved net_return among valid natural V2-only events, event_id tie-break.
Its chart includes100 prior bars, complete holding and24 available post-exit bars.
These charts and event returns do not establish account returns or model quality.
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
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_recall_study as study
from yoyo.evaluation.spike_burst_figures import style

ROOT, EXP = study.ROOT, study.EXPERIMENT
REPORT = ROOT / "analysis/p1_spike_burst_launch_recall_20260910.md"
AUDIT = EXP / "diagnostics/owner_missed_launches_audit.json"
AUDIT_SHA = "f8e7187f8afcb79cd7829f4997749244c23219a76d8faeaf07f2a7388d7d3b76"
TV_QA = EXP / "qa/tradingview_delivery.json"
TV_QA_SHA = "79fedc085abac345de1f111da499b5464d73b9fe22692db7f3bc725a2d9db117"
TV_FOLLOWUP = EXP / "qa/tradingview_followup.json"
TV_FOLLOWUP_SHA = "16a7758f029133210c625b30f6c85705a9824d6c07223546c377282c3e5f12d2"
GATE_MANIFEST = EXP / "qa/gates/diagnostic_manifest.json"
GATE_MANIFEST_SHA = "9a359637f2c3bc682625b84a9edd0d1fbf3d38a0b3ef2442dc8889a8e0108ba1"
MA_COLORS = {"s20": "#268F85", "e20": "#73B6A9", "s60": "#4B78AF",
             "e60": "#88A7D1", "s120": "#525F68", "e120": "#9CA5AA"}
ARM = {"v1": "V1 原版", "v2": "V2 增强"}
GATE_NAMES = {"ready":"预热就绪", "recent_density":"近期六MA密集", "break_prior12":"突破前12根高点",
    "above_six_ma":"站上六MA", "green_candle":"阳线", "zlema_rising":"ZLEMA上升", "md_ge_signal":"主线≥信号线",
    "advance_3bar":"三根推进≥1.5ATR", "volume_3bar":"三根量比≥1.5", "efficiency_3bar":"方向效率≥55%",
    "close_position":"方向侧收盘≥65%", "no_existing_hold":"无现存参考仓", "not_exit_bar":"非退出当根", "no_v1_priority":"无V1同根优先"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(path):
    p = Path(path).resolve()
    return dict(path=str(p), sha256=sha(p), size_bytes=p.stat().st_size)


def check(path, expected):
    p = Path(path).resolve()
    if not p.is_file() or sha(p) != expected:
        raise ValueError("Changed or missing report source: " + str(p))
    return p


def num(value, digits=2):
    return "不适用" if value is None or pd.isna(value) else format(float(value), ",." + str(digits) + "f")


def pct(value):
    return num(100 * value) + "%" if value is not None and pd.notna(value) else "不适用"


def ratio(hits, denominator):
    return "%s（%d/%d）" % (pct(hits / denominator), hits, denominator) if denominator else "不适用（0/0）"


def table(headers, rows):
    def cell(x):
        return str(x).replace("|", "／").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"] +
        ["| " + " | ".join(cell(x) for x in row) + " |" for row in rows])


def bjt(stamp):
    return pd.Timestamp(stamp).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def gate_reasons(value):
    if value is None or pd.isna(value) or value == "":
        return "无"
    return "、".join(GATE_NAMES.get(k,k) for k in str(value).split(";"))


class Evidence:
    """Require completed matching/evaluation and check every consumed SHA."""
    def __init__(self, folder):
        self.folder, self.read = Path(folder).resolve(), {}
        vp = self.folder / "validation_manifest.json"
        self.validation = json.loads(vp.read_text())
        pp = check(self.folder / "prepared_manifest.json", self.validation["prepared_manifest_sha256"])
        self.prepared = json.loads(pp.read_text())
        for receipt in (self.prepared, self.validation):
            if receipt.get("status") != "complete" or receipt["config"] != study.CONFIG:
                raise ValueError("A complete frozen V2 receipt is required")
        if self.prepared["source_pins"] != self.validation["source_pins"]:
            raise ValueError("Prepared/evaluated sources differ")
        self.allowed = {}
        for receipt in (self.prepared, self.validation):
            for item in receipt["artifacts"]:
                p = str(Path(item["path"]).resolve())
                if p in self.allowed and self.allowed[p] != item["sha256"]:
                    raise ValueError("Conflicting artifact identity")
                self.allowed[p] = item["sha256"]
        for p, expected in self.prepared["sources"].items():
            key = str(Path(p).resolve())
            if key in self.allowed and self.allowed[key] != expected:
                raise ValueError("Conflicting upstream identity")
            self.allowed[key] = expected
        for p in (vp, pp):
            self.read[str(p)] = artifact(p)
        check(AUDIT, AUDIT_SHA)
        self.audit = json.loads(AUDIT.read_text())
        if self.audit.get("status") != "complete" or self.audit.get("rules_changed") is not False:
            raise ValueError("Completed non-mutating miss audit required")
        self.read[str(AUDIT)] = artifact(AUDIT)
        check(TV_QA, TV_QA_SHA)
        self.tv = json.loads(TV_QA.read_text())
        if self.tv.get("status") != "native_compile_and_chart_add_verified" or self.tv.get("live_monitor_rules_changed") is not False:
            raise ValueError("Expected separate verified TradingView delivery without monitor changes")
        check(ROOT / "yoyo/evaluation/pine/spike_burst_v2_progressive.pine", self.tv["local_payload_sha256"])
        self.read[str(TV_QA)] = artifact(TV_QA)
        for item in self.tv["artifacts"]:
            p = Path(item["path"])
            p = p if p.is_absolute() else ROOT / p
            check(p, item["sha256"]); self.read[str(p)] = artifact(p)
        check(TV_FOLLOWUP, TV_FOLLOWUP_SHA)
        self.tv_followup = json.loads(TV_FOLLOWUP.read_text())
        if (self.tv_followup.get("status") != "native_settings_and_pepe_render_verified"
                or self.tv_followup.get("compile_errors_observed") is not False
                or self.tv_followup["source_sha256"] != self.tv["local_payload_sha256"]):
            raise ValueError("Native settings followup disagrees with first delivery")
        self.read[str(TV_FOLLOWUP)] = artifact(TV_FOLLOWUP)
        for item in self.tv_followup["artifacts"]:
            p = Path(item["path"])
            p = p if p.is_absolute() else ROOT / p
            check(p,item["sha256"]); self.read[str(p)] = artifact(p)
        for item in self.audit["artifacts"]:
            self.allowed[str(Path(item["path"]).resolve())] = item["sha256"]
        check(GATE_MANIFEST,GATE_MANIFEST_SHA)
        self.gates = json.loads(GATE_MANIFEST.read_text())
        if (self.gates.get("status") != "complete" or self.gates.get("kind") != "fixed_configuration_explanation_no_rescoring"
                or self.gates["inputs"].get(str(pp)) != sha(pp) or self.gates["inputs"].get(str(vp)) != sha(vp)):
            raise ValueError("Gate explanation must belong to this completed frozen study")
        for relative, expected in self.prepared["source_pins"].items():
            if self.gates["source_pins"].get(relative) != expected:
                raise ValueError("Gate explanation used different frozen rules")
        for p, expected in self.gates["inputs"].items():
            key = str(Path(p).resolve())
            known = self.allowed.get(key, self.read.get(key, {}).get("sha256"))
            if known != expected:
                raise ValueError("Gate explanation input is not authenticated by this study")
        self.read[str(GATE_MANIFEST)] = artifact(GATE_MANIFEST)
        for item in self.gates["artifacts"]:
            key = str(Path(item["path"]).resolve())
            if key in self.allowed and self.allowed[key] != item["sha256"]:
                raise ValueError("Conflicting gate artifact")
            self.allowed[key] = item["sha256"]
        self.jobs = json.loads(self.path("matching.json").read_text())["jobs"]
        for job in self.jobs:
            if self.allowed.get(str(Path(job["features_path"]).resolve())) != job["features_sha256"]:
                raise ValueError("Chart feature is not in the authenticated study")
        self.cache = {}

    def path(self, path):
        p = Path(path)
        p = p.resolve() if p.is_absolute() else (self.folder / p).resolve()
        key = str(p)
        if key not in self.allowed:
            raise ValueError("Unregistered source: " + key)
        check(p, self.allowed[key]); self.read[key] = artifact(p)
        return p

    def csv(self, name):
        p = self.path(name)
        if str(p) not in self.cache:
            f = pd.read_csv(p)
            for c in ("bar_open", "decision_time", "confirmed_at", "entry_time", "exit_time", "exit_time_lower", "exit_time_upper", "open_time", "confirm_time", "active_entry_confirmed_at"):
                if c in f:
                    f[c] = pd.to_datetime(f[c], utc=True)
            self.cache[str(p)] = f
        return self.cache[str(p)].copy()

    def frame(self, job):
        f = pd.read_pickle(self.path(job["features_path"]))
        if not isinstance(f.index, pd.DatetimeIndex) or f.index.tz is None or not f.index.is_unique or not f.index.is_monotonic_increasing:
            raise ValueError("Authenticated chart needs ordered timezone-aware bars")
        return f

    def finish(self):
        for item in self.read.values():
            check(item["path"], item["sha256"])


def committed(evidence):
    paths = [Path(__file__), ROOT / "yoyo/evaluation/spike_burst_figures.py", ROOT / "scripts/md_to_html.py"]
    for relative, expected in evidence.gates["source_pins"].items():
        check(ROOT / relative, expected)
        paths.append(ROOT / relative)
    for p in paths:
        if subprocess.check_output(["git", "show", "HEAD:" + str(p.resolve().relative_to(ROOT))], cwd=ROOT) != p.read_bytes():
            raise ValueError("Commit exact report sources before rendering: " + str(p))
    return [artifact(p) for p in paths]


def chart(frame, signals, left, right, title, output, footnotes, focus=None, trade=None):
    """Plot saved bars and saved signals; no indicator, trade or rule recompute."""
    f = frame.iloc[left:right]
    x = np.arange(left, right)
    if not len(f):
        raise ValueError("Empty chart window")
    fig, (ax, vol, lower) = plt.subplots(3, 1, figsize=(16, 10), sharex=True, height_ratios=[3.6, .8, 1.25])
    span = float(f.high.max() - f.low.min())
    min_height = max(span * 1e-5, np.finfo(float).eps * float(f.close.max()))
    colors = np.where(f.close.ge(f.open), "#008E7C", "#CD5369")
    for k, row, color in zip(x, f.itertuples(), colors):
        ax.vlines(k, row.low, row.high, color=color, linewidth=.8)
        ax.add_patch(Rectangle((k-.32, min(row.open, row.close)), .64, max(abs(row.close-row.open), min_height),
                              facecolor=color, edgecolor=color, linewidth=.4))
    for name, color in MA_COLORS.items():
        ax.plot(x, f[name], color=color, linewidth=1, label=name.upper())
    for arm, color, offset, marker in (("v1", "#9862BF", .04, "v"), ("v2", "#087F87", .085, "^")):
        part = signals.loc[signals.arm.eq(arm) & signals.decision_i.ge(left) & signals.decision_i.lt(right)]
        indices = part.decision_i.astype(int).to_numpy()
        if len(indices):
            for row in part.itertuples():
                if pd.Timestamp(row.decision_time) != frame.index[int(row.decision_i)] + study.HOUR:
                    raise ValueError("Arrow clock differs from its frozen signal bar")
            ax.scatter(indices, frame.low.iloc[indices].to_numpy() - span * offset, marker=marker, s=75,
                       color=color, zorder=5, label=ARM[arm] + " 收盘确认箭头")
        else:
            ax.plot([], [], color=color, marker=marker, linestyle="none", label=ARM[arm] + "：本窗口无箭头")
    if focus is not None:
        for index, stamp in enumerate(focus):
            positions = np.flatnonzero(frame.index == stamp)
            if len(positions) != 1:
                raise ValueError("Missing specified opening bar")
            i = int(positions[0])
            for a in (ax, vol, lower):
                a.axvline(i, color="#BE8837", linewidth=.85, linestyle="--")
            ax.annotate("开盘 " + bjt(stamp)[5:] + "\n确认 " + bjt(stamp+study.HOUR)[5:],
                xy=(i, frame.high.iloc[i]), xytext=(10, 45 + 38*index), textcoords="offset points",
                color="#8C6527", fontsize=9, arrowprops=dict(arrowstyle="-", color="#BE8837"))
    if trade is not None:
        i, entry_i, exit_i = int(trade.decision_i), int(trade.entry_i), int(trade.exit_i)
        ax.axvspan(i+.5, exit_i+.5, color="#E9F2FF", alpha=.6, label="信号后持有区间：当时未知")
        ax.scatter([entry_i], [trade.entry_price], marker="^", s=110, color="#254EAB", zorder=6, label="下根开盘模拟入场")
        ax.scatter([exit_i], [trade.exit_price], marker="X", s=95, color="#AE334C", zorder=6, label="已保存保护退出")
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
    ax.legend(loc="upper left", fontsize=8, ncol=4)
    lower.legend(loc="upper left", fontsize=9)
    ticks = np.unique(np.linspace(left, right-1, 9).astype(int))
    lower.set_xticks(ticks)
    lower.set_xticklabels([frame.index[k].tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for k in ticks])
    lower.set_xlabel("2026年北京时间 · 横轴为K线开盘时刻，箭头仅在该根收盘（+1小时）才能确认")
    fig.text(.045, .025, "\n".join(footnotes), fontsize=9, color="#52616C", linespacing=1.65)
    fig.tight_layout(rect=[0, .085 + .018*max(0, len(footnotes)-2), 1, 1])
    fig.savefig(output, dpi=155)
    plt.close(fig)
    return Path(output)


def gate_text(row):
    return ("%s开盘→%s确认｜近零 %s/12｜均线宽度 %s≤3 / 交织 %s≥2｜RV %s/4 / TR %s/3｜实体 %s / 55%% / 收盘位置 %s /75%%" %
        (bjt(row["open_time"])[11:], bjt(row["confirm_time"])[5:], row["quiet_before"], num(row["past_width"]),
         num(row["past_crosses"], 0), num(row["rv"]), num(row["expansion"]), pct(row["body_ratio"]), pct(row["close_location"])))


def render_cases(evidence, signals, trades, changes, figures):
    examples = []
    snapshots = evidence.csv("snapshots.csv")
    for asset in ("HYPE", "NEAR", "PEPE"):
        case = next(x for x in evidence.audit["cases"] if x["asset"] == asset)
        source = str(Path(case["cached_features"]["path"]).resolve())
        matches = [j for j in evidence.jobs if str(Path(j["features_path"]).resolve()) == source]
        if len(matches) != 1 or matches[0]["features_sha256"] != case["cached_features"]["sha256"]:
            raise ValueError("Owner case source differs from frozen study")
        job = matches[0]; frame = evidence.frame(job)
        start = pd.Timestamp("2026-08-18T00:00:00+08:00").tz_convert("UTC")
        end = start + pd.Timedelta(days=4)
        idx = np.flatnonzero((frame.index >= start) & (frame.index < end))
        if len(idx) != 96 or not np.all(np.diff(frame.index[idx].asi8) == study.HOUR.value):
            raise ValueError("Specified case must include all96 contiguous bars")
        gates_path = next(x["path"] for x in evidence.audit["artifacts"] if Path(x["path"]).name.startswith(asset.lower()+"_"))
        gates = evidence.csv(gates_path)
        targets = gates.loc[gates.open_time.isin(study.SNAPSHOT_OPENS)]
        if len(targets) != 2:
            raise ValueError("Both specified opening bars need old gate diagnostics")
        notes = [gate_text(row) for row in targets.to_dict("records")]
        target_states = snapshots.loc[snapshots.instrument.eq(job["instrument"]) & snapshots.bar_open.isin(study.SNAPSHOT_OPENS)]
        if len(target_states) != 2:
            raise ValueError("Both owner opening bars need frozen V1/V2 states")
        state_note = "；".join(bjt(r.bar_open)[11:] + "开盘根：V1" + ("有参考仓" if r.v1_trend_side == 1 else "空参考仓") +
            " / V2" + ("有参考仓" if r.v2_trend_side == 1 else "空参考仓") for r in target_states.itertuples())
        notes += [state_note + "（参考仓≠交易所真实持仓，也不等于本根新箭头）。"]
        notes += ["旧版门槛显示的是这根历史K线；右上角最新状态表不代表光标历史。箭头来自完整源历史逐根回放的冻结结果。"]
        part = signals.loc[signals.instrument.eq(job["instrument"])]
        path = chart(frame, part, int(idx[0]), int(idx[-1])+1,
            "OKX · %s · 1H｜8月18—21日完整96根｜V1 / V2" % asset,
            figures/(asset.lower()+"_96bars.png"), notes, focus=study.SNAPSHOT_OPENS)
        arrows = part.loc[part.decision_i.isin(idx)].copy()
        examples.append(dict(asset=asset, path=str(path), instrument=job["instrument"],
            gates=targets.to_dict("records"), arrows=arrows.to_dict("records"),
            target_states=target_states.to_dict("records"),
            screenshot_prices_match=case["screenshot_check"]["matches_all_displayed_prices"]))
    gained = changes.loc[changes.change.eq("gained"), ["instrument", "decision_i"]]
    failures = trades.loc[trades.arm.eq("v2") & trades.valid.eq(True) & trades.natural_exit.eq(True) & trades.net_return.lt(0)].merge(gained, on=["instrument", "decision_i"], how="inner", validate="one_to_one")
    failure = None
    if len(failures):
        row = failures.sort_values(["net_return", "event_id"], ascending=[True, True]).iloc[0]
        job = next(j for j in evidence.jobs if j["instrument"] == row.instrument)
        frame = evidence.frame(job)
        if pd.Timestamp(row.entry_time) != frame.index[int(row.entry_i)]:
            raise ValueError("Failure saved entry clock does not match source")
        left, right = max(0, int(row.decision_i)-100), min(len(frame), int(row.exit_i)+25)
        notes = ["事后固定选择：V2独有、有效、自然退出且单笔净收益最低；不是账户最差交易，也不代表全部失败。",
            "入场 %s｜退出区间 %s—%s｜峰值 %sR ≠ 扣成本净值 %sR；单笔净收益 %s" %
            (bjt(row.entry_time), bjt(row.exit_time_lower), bjt(row.exit_time_upper), num(row.peak_r), num(row.net_r), pct(row.net_return))]
        path = chart(frame, signals.loc[signals.instrument.eq(job["instrument"])], left, right,
            "失败案例 · %s · V2独有｜完整持有路径" % row.asset, figures/"failure_new_only.png", notes, trade=row)
        failure = dict(event_id=row.event_id, asset=row.asset, net_return=float(row.net_return),
                       peak_r=float(row.peak_r), net_r=float(row.net_r), path=str(path),
                       entry_time=row.entry_time, exit_time_lower=row.exit_time_lower, exit_time_upper=row.exit_time_upper,
                       exit_timing=row.exit_timing, selection="lowest natural V2-only net_return, event_id tie-break; post hoc")
    return examples, failure


def report_text(evidence, examples, failure, report):
    recall = evidence.csv("recall_summary.csv")
    signals = evidence.csv("signals.csv.gz")
    trades, controls = evidence.csv("trade_events.csv.gz"), evidence.csv("trade_controls.csv.gz")
    outcomes, scores = evidence.csv("trade_summary.csv"), evidence.csv("score_summary.csv")
    changes = evidence.csv("signal_changes.csv.gz")
    timing = evidence.csv("event_timing_changes.csv.gz")
    snapshots = evidence.csv("snapshots.csv")
    coverage = evidence.csv("coverage.csv")
    gate_cases = evidence.csv(EXP / "qa/gates/case_gates.csv")
    gate_counts = evidence.csv(EXP / "qa/gates/miss_gate_counts.csv")
    full = recall.loc[recall.period.eq("full")].set_index("arm")
    if set(full.index) != {"v1", "v2"}:
        raise ValueError("Exactly two full-period recall rows required")
    target = full.loc["v2"]
    night = recall.loc[recall.period.eq("case_night")].set_index("arm")
    v2_trade = outcomes.loc[outcomes.arm.eq("v2") & outcomes.period.eq("full")].iloc[0]
    met = bool(target.positive_events and target.hits_1/target.positive_events >= .8)
    if met != bool(target.target_80_met):
        raise ValueError("80% status differs from frozen numerator")
    if (evidence.gates["observed_positive_events"] != int(target.positive_events)
            or evidence.gates["observed_hits_1"] != int(target.hits_1)
            or evidence.gates["missed_events"] != int(target.positive_events-target.hits_1)):
        raise ValueError("Gate explanations changed the frozen recall denominator")
    target_gates = gate_cases.loc[gate_cases.observation_roles.str.contains("target_open_", na=False)]
    if len(target_gates) != 6:
        raise ValueError("Exactly two target opening bars per specified asset required")
    efficiency_miss = gate_counts.loc[gate_counts.period.eq("case_night") & gate_counts.cohort.eq("not_already_tracking") & gate_counts.gate.eq("efficiency_3bar")]
    if len(efficiency_miss)!=1:
        raise ValueError("Exactly one fixed night efficiency denominator required")
    efficiency_miss = efficiency_miss.iloc[0]
    rel = lambda p: os.path.relpath(Path(p).resolve(), report.parent)
    lines = ["# SPIKE V2：渐进启动补漏与全池召回验证", "", "## 结论与80%目标", "",
        "V2 在**最多延后1根收盘确认**的主口径下，召回 %s；V1为 %s。**%s80%%目标。**这个分母来自全池独立价格事件，不是三张成功截图，也不是全部上涨币种。" %
        (ratio(target.hits_1, target.positive_events), ratio(full.loc["v1"].hits_1, full.loc["v1"].positive_events), "达到本次价格代理定义下的" if met else "尚未达到"),
        "", "当晚北京时间8/19 18:00—8/20 06:00，同口径从V1的%s提高到V2的%s，仍不代表及时覆盖了所有启动。全期箭头从%d增至%d；提醒变多必须同时检查精确率和误报，不能只看补到几个案例。" %
        (ratio(night.loc["v1"].hits_1,night.loc["v1"].positive_events),ratio(night.loc["v2"].hits_1,night.loc["v2"].positive_events),full.loc["v1"].signals,target.signals),
        "", "V2的全期事件匹配实际相对随机平均超额为%s bp，两项主检验Holm p=%s。**新增路径尚未证明改善盈利。**这是单笔事件对照，不能换算成账户收益。" %
        (num(v2_trade.mean_excess_bp),num(v2_trade.holm_p,6)),
        "", "三个指定漏报的直接原因：", "",
        *["- **%s**：%s" % (c["asset"],
            ("量比%s、TR扩张%s以及密集/方向/实体已通过，但此前仅%s根近零，未达到原版12根资格。" % (num(c["target"]["rv"]),num(c["target"]["expansion"]),c["target"]["quiet_before"])) if c["asset"]=="HYPE" else
            ("主线仍在零轴下恢复（MD=%s），虽可高于信号线，但近零仅%s根，单根量比%s、TR扩张%s也低于4/3门槛。" % (format(c["target"]["md"],".7g"),c["target"]["quiet_before"],num(c["target"]["rv"]),num(c["target"]["expansion"]))) if c["asset"]=="NEAR" else
            ("此前近零%s根不足12，量比%s仍小于4；不是只要这根站上均线就一定满足V1资格。" % (c["target"]["quiet_before"],num(c["target"]["rv"])))) for c in evidence.audit["cases"]],
        "", "这次增加的是一条**渐进启动**通路：此前12根曾形成六均线密集，当前收盘突破前12根高点并站上六均线；三根净推进≥1.5ATR、累计量比≥1.5、方向效率≥55%，当前收盘位置≥65%，且ZLEMA上升、主线≥信号线。不再要求连续近零12根或已进入6根释放窗。V1同根优先，宽止损和趋势退出不变。它是一项固定假设，并非已找到最优参数。",
        "", "三例的22:00/23:00开盘根共6根中，V2实际新箭头%d根、收盘仍有参考仓%d根；已有参考仓不是本根新发现。96根图窗内，%s。完整门槛解释在后文，迟到确认不会回填成当晚及时捕获。" %
        (int(target_gates.v2_burst.sum()),int(target_gates.v2_trend_side.eq(1).sum()),"；".join(
            item["asset"]+"首个V2新确认 "+min(bjt(a["decision_time"]) for a in item["arrows"] if a["arm"]=="v2")
            if any(a["arm"]=="v2" for a in item["arrows"]) else item["asset"]+"无V2新箭头" for item in examples)),
        "", "这是已见历史上的一次固定工程改版，本V2配置首次消耗holdout（正式边界2026-05-04），不是盲测或新的样本外。只增加预登记的渐进通路，没有看结果后继续挪阈值。Python只评价多头；Pine空头镜像未获收益验证。",
        "", "主池是既有OKX 1H历史研究池：%d个连续源段、%d个独立币、%d个合约，%s个合格bar。评价收盘为2026-07-10至09-09 UTC，期末不含；BTC/ETH为原池背景排除。它不等于当前OKX全部合约，缺口不补、缺失和预热不足不伪造覆盖。" %
        (evidence.prepared["segments"], evidence.prepared["unique_assets"], evidence.prepared["unique_symbols"], num(target.eligible_asset_hours, 0)),
        "", "## 全部延迟口径与误报", ""]
    rows = []
    for arm in study.ARMS:
        r = full.loc[arm]
        rows.append([ARM[arm], int(r.positive_events)] + [ratio(r["hits_"+str(k)], r.positive_events) for k in (0,1,2,6)] +
            [int(r.already_tracking), int(r.large_positive_events), pct(r.large_recall_1)])
    lines += [table(["版本", "正事件分母", "当根召回", "+1根", "+2根", "+6根", "之前已持有参考仓", "峰值≥8%正事件 n", "大涨子组+1召回"], rows), "",
        "独立标签全期共有%d个候选：正事件%d、负事件%d、未知%d。未知不足24根完整后续窗口不算负例，也不进入正事件召回分母。" %
        (target.anchors,target.positive_events,target.negative_events,target.unknown_events), "",
        "原始事件是收盘首次突破此前12根最高价；先按时间去重24根，再观察未来完整24根收盘是否先到+4ATR且之前未到−2ATR。标签障碍只定义评价分母，不改变交易退出。尾部不完整为未知。未来最高价≥8%只作正事件子组，不把事后榜单当事前选币。",
        "", "新箭头累计召回只计事件当根至规定延迟范围；更早已经存在的参考持仓单列，不混入新提醒。价格代理正例不等于Owner逐张金标，也不保证开仓获利。", ""]
    rows=[]
    for arm in study.ARMS:
        r=full.loc[arm]
        rows.append([ARM[arm],int(r.signals),int(r.unique_matched),int(r.unmatched),int(r.duplicates),int(r.unknown_signals),
            pct(r.precision_all),pct(r.precision_adjudicated),pct(r.unmatched/r.signals) if r.signals else "不适用",num(r.false_alerts_per_100_asset_days)])
    lines += [table(["版本","全部箭头","唯一匹配","无匹配","重复","未知尾部","全部精确率","已判断精确率","无匹配占全部比例","每100合约日无匹配提醒"],rows), "",
        "精确率是全部箭头中唯一匹配正事件[0,+6]窗口的比例，不是交易胜率。全部分母保留未知信号；已判断分母另列。误报密度的分子仅无匹配提醒，重复和未知未偷偷并入或删除；合约日由真实合格小时除24。"]
    delay=[]
    for arm in study.ARMS:
        s=signals.loc[signals.arm.eq(arm)&signals.match_status.eq("matched")]
        delay.append([ARM[arm],len(s),num(s.lag.median()),num(s.lag.quantile(.9)),num(s.move_since_anchor_pct.median()),num(s.move_since_anchor_atr.median())])
    lines += ["",table(["版本","匹配提醒 n","延迟中位根数","延迟P90","确认前已涨幅中位 %","确认前推进中位 ATR"],delay),"",
        "## 分期与逐周稳定性", ""]
    rows=[]
    for r in recall.itertuples():
        rows.append([r.period,ARM[r.arm],int(r.positive_events),ratio(r.hits_0,r.positive_events),ratio(r.hits_1,r.positive_events),ratio(r.hits_2,r.positive_events),ratio(r.hits_6,r.positive_events),int(r.signals),pct(r.precision_all),num(r.false_alerts_per_100_asset_days)])
    lines += [table(["期间","版本","正事件 n","当根","+1","+2","+6","箭头","精确率","每100合约日无匹配"],rows),"",
        "first31为7/10—8/10，last30为8/10—9/9；week按UTC周切片并裁到评价窗，case_night为北京时间8/19 18:00—8/20 06:00。所有期间都是已见回顾，同一夜不同币并非独立重复。", "", "## 新增、被替代与更早", ""]
    counts=changes.change.value_counts()
    positives=timing.loc[timing.in_study.eq(True)&timing.label.eq("positive")]
    comparable=positives.loc[positives.first_signal_lag_v1.notna()&positives.first_signal_lag_v2.notna()]
    lines += [table(["同根共有","V2独有","V1独有","两版均匹配正事件","其中V2更早"],[[int(counts.get("shared",0)),int(counts.get("gained",0)),int(counts.get("lost",0)),len(comparable),int(comparable.earlier_v2.sum())]]),"",
        "新增通路进入同一个参考持有状态，可能提前启动并压制后来的V1箭头。因此‘V1独有’不自动等于漏掉行情，‘V2独有’也不自动等于优质新交易；必须同时看独立事件召回和持仓时序。", "", "## V2为什么仍然漏报", "",
        "以下诊断只解释同一套冻结规则，未改标签或重新评分。在没有+1根内新箭头的正事件上，分别检查事件根和下一根。条件可同时失败，**各行不互斥、不能相加，也不能当作放宽该门槛后的因果收益**。正在跟踪旧参考仓与刚退出禁入单列，不误写成价格条件失败。", ""]
    for period, title in (("full","全期"),("case_night","案例当晚")):
        rows=[]
        for cohort, label in (("all","全部漏报"),("already_tracking","已有参考仓"),("not_already_tracking","此前无参考仓")):
            part=gate_counts.loc[gate_counts.period.eq(period)&gate_counts.cohort.eq(cohort)].sort_values(["fail_both","gate"],ascending=[False,True])
            if len(part)!=14 or part.mutually_exclusive.ne(False).any():
                raise ValueError("All fourteen nonexclusive gate counts are required")
            for r in part.itertuples():
                rows.append([label,GATE_NAMES.get(r.gate,r.gate),int(r.missed_events),int(r.fail_at_anchor),int(r.fail_at_plus1),int(r.fail_both),pct(r.fail_both_fraction)])
        lines += ["### "+title+"的阻断条件", "",table(["漏报组","条件","该组分母","事件根未过","+1根未过","两根均未过","两根均未过比例"],rows),""]
    lines += ["### 三例目标根的渐进条件与参考状态", "",
        table(["资产","开盘BJT","确认BJT","三根推进ATR","三根量比","方向效率","收盘位置","未过的价格条件","未过的状态条件","本根新箭头","当前参考仓起点确认BJT"],
            [[r.asset,bjt(r.bar_open),bjt(r.confirmed_at),num(r.prog_advance),num(r.prog_volume_ratio),pct(r.prog_efficiency),pct(r.prog_close_position),
              gate_reasons(r.failed_criteria),gate_reasons(r.failed_state_gates),bool(r.v2_burst),bjt(r.active_entry_confirmed_at) if pd.notna(r.active_entry_confirmed_at) else "无"] for r in target_gates.itertuples()]),"",
        "若价格条件全过但‘无现存参考仓’未过，表示已有旧信号在跟踪，不能发成新的捕获；若效率/突破等仍不足，则新增渐进通路也不会出箭头。上述时点来自完整历史状态，未在这两根单独重置指标。", "",
        "## 单笔交易结果与同条件随机对照", "",
        "这里没有现金、容量或组合持仓分配，**不计算或宣称账户收益**。每个箭头独立按下一根真实开盘模拟入场，原5根结构/0.2ATR缓冲/2ATR最低距离、2R启动4ATR保护不变；固定20bp成本，资金费率和实际冲击未建模。"]
    rows=[]
    for r in outcomes.loc[outcomes.period.eq("full")].itertuples():
        rows.append([ARM[r.arm],int(r.candidates),int(r.valid),int(r.invalid),int(r.natural_exits),int(r.censored),ratio(r.wins,r.valid),ratio(r.natural_wins,r.natural_exits),num(r.mean_gross_bp),num(r.mean_net_bp),int(r.matched),num(r.paired_actual_net_bp),num(r.paired_random_net_bp),num(r.asset_balanced_excess_bp),num(r.permutation_p,6),num(r.holm_p,6)])
    lines += ["",table(["版本","候选","有效","无效","自然退出","边界估值","全部净胜率","自然净胜率","均毛bp","均净bp","有匹配n","匹配实际净bp","随机净bp","资产均衡超额bp","资产置换p","两项Holm p"],rows),"",
        "随机按同币/同UTC周/同因果ATR桶最多3个，只排除当根及此前12根箭头，不排除未来信号。可以复用控制时点，是回顾条件零假设，不能当可部署随机系统；不以它代替召回分母。两个全期版本是预登记两项主检验，period表不另外宣称显著性。自然退出与边界估值胜率分母分开。"]
    valid_controls=controls.loc[controls.valid.eq(True)]
    lines += ["", "有效控制共%d条，独立instrument/decision_i时点%d个；跨事件或跨版本复用已保留。" % (len(valid_controls),len(valid_controls.drop_duplicates(["instrument","decision_i"]))),"",
        table(["版本","期间","有效交易n","自然退出n","边界n","均净bp","匹配实际bp","匹配随机bp"],
            [[ARM[r.arm],r.period,int(r.valid),int(r.natural_exits),int(r.censored),num(r.mean_net_bp),num(r.paired_actual_net_bp),num(r.paired_random_net_bp)] for r in outcomes.itertuples()]),"",
        "## 单特征基线：量比与TR扩张", ""]
    rows=[]
    for r in scores.itertuples():
        group=trades.loc[trades.arm.eq(r.arm)&trades.valid.eq(True)].copy()
        group=group.loc[np.isfinite(pd.to_numeric(group[r.feature],errors="coerce"))].sort_values([r.feature,"event_id"],ascending=[False,True]).head(int(r.top_n))
        matched=valid_controls.loc[valid_controls.matched_event_id.isin(group.event_id)]
        rows.append([ARM[r.arm],r.feature,int(r.n),num(r.descriptive_auc,4),int(r.top_n),matched.matched_event_id.nunique(),len(matched),num(r.top_gross_bp),num(r.top_net_bp),pct(r.top_win_rate),num(r.top_matched_actual_bp),num(r.top_random_bp),num(r.top_excess_bp)])
    lines += [table(["版本","特征","有限值n","描述AUC","Top10%n","Top有匹配n","有效控制条数","Top毛bp","Top净bp","Top胜率","匹配实际bp","随机bp","超额bp"],rows),"",
        "无模型训练，模型val AUC不适用；表中只是同历史样本的描述排序。Top全部净收益与匹配净收益分母不同，未匹配不补零，不据此选新阈值。", "", "## 当晚的两个时钟", ""]
    rows=[]
    for flag,label in (("requested_bar_open","22/23点开盘的信号根"),("requested_confirmed_cutoff","22/23点已收盘可知")):
        for stamp in study.SNAPSHOT_OPENS:
            time_col="bar_open" if flag=="requested_bar_open" else "confirmed_at"
            part=snapshots.loc[snapshots[flag].eq(True)&snapshots[time_col].eq(stamp)]
            for arm in study.ARMS:
                selected=part.loc[part[arm+"_burst"].eq(True)]
                rows.append([label,bjt(stamp),ARM[arm],len(selected),", ".join(selected.asset.astype(str)) or "无"])
    lines += [table(["口径","北京时间","版本","数量","币种"],rows),"",
        "截图标22:00的1H K线，最早23:00收盘确认；23:00开盘那根到次日00:00才确认。不能把箭头画在信号根开盘位置，便当作开盘时已知。", "", "## 三个指定案例：完整96根全局图", ""]
    lines += ["TradingView表格显示的是脚本最近一次绘制的状态，移动光标到历史K线不会令它变成那根的历史值。V2面板已标明‘最新收盘’，历史量比、TR与渐进指标应看Data Window。[TradingView官方Tables说明](https://www.tradingview.com/pine-script-docs/visuals/tables/)",""]
    for item in examples:
        lines += ["", "### " + item["asset"], "",
            "截图显示的OHLC与认证OKX小时源%s；截图交易所文字不清，价格吻合仍不能独立证明场所或脚本参数。" % ("一致" if item["screenshot_prices_match"] else "不一致，不能当同一来源"), "",
            table(["开盘BJT","确认BJT","近零根数","宽度ATR","交织","量比","TR扩张","V1阻断原因"],
                [[bjt(r["open_time"]),bjt(r["confirm_time"]),r["quiet_before"],num(r["past_width"]),num(r["past_crosses"],0),num(r["rv"]),num(r["expansion"]),r["blocking_reasons"]] for r in item["gates"]]),"",
            table(["开盘BJT","V1本根新箭头","V1参考持仓","V2本根新箭头","V2参考持仓"],
                [[bjt(r["bar_open"]),bool(r["v1_burst"]),int(r["v1_trend_side"]),bool(r["v2_burst"]),int(r["v2_trend_side"])] for r in item["target_states"]]),"",
            "参考持仓1表示当前仍在跟踪之前的启动，0表示没有；它不是交易所实际仓位，不能把之前的跟踪计为本根新提醒。", "",
            table(["版本","信号根开盘BJT","收盘确认BJT","路径"],[[ARM[r["arm"]],bjt(r["bar_open"]),bjt(r["decision_time"]),r["route"]] for r in item["arrows"]]),"",
            "![%s完整96根](%s)" % (item["asset"],rel(item["path"]))]
    lines += ["", "## 失败案例也保留", ""]
    if failure:
        lines += ["事后固定选出V2独有、自然退出且净收益最低的事件：%s。单笔净收益%s，峰值%sR，扣成本净值%sR。%s入场；退出在%s—%s区间，盘中精确时刻未知（开盘退出时两端相同）。不表示实际账户曾持有。" %
            (failure["asset"],pct(failure["net_return"]),num(failure["peak_r"]),num(failure["net_r"]),bjt(failure["entry_time"]),bjt(failure["exit_time_lower"]),bjt(failure["exit_time_upper"])),"",
            "![V2独有失败例完整路径](%s)" % rel(failure["path"])]
    else:
        lines += ["冻结结果中没有符合该选择规则的自然退出亏损例，不另换规则挑图。"]
    lines += ["", "## TradingView交付状态", "",
        "独立私有脚本“%s”已原生编译并添加到图表，界面版本显示“%s”，模式为“%s”。原V1云脚本保留，仅旧图表实例被移除；盈亏框、六均线、零轴及风险规则保留。线上监控和推送未切换。证据核对了本地粘贴内容SHA与原生界面；无法导出完整远程源码，因此不把界面验收冒充远程源码逐字节认证。" %
        (evidence.tv["private_script_name"],evidence.tv["script_version_observed"],evidence.tv["mode_observed"]),
        "", "后续另在%s %s核对完整设置与盈亏双框，保留独立follow-up回执及截图，未覆盖首次HYPE验收记录。" % (evidence.tv_followup["symbol"],evidence.tv_followup["interval"]),
        "", "## 风险与诚实声明", "",
        "三张图是Owner事先指出的历史案例，不是随机独立样本；80%以完整研究池预登记正事件为分母。价格事件定义可能漏掉慢趋势、反复回踩或先下后上的走势，召回提升不等于盈利提升。未覆盖合约、预热不足、未知尾部与同夜相关性都限制外推。", "",
        "成交量为同一市场相对自身历史的量比，跨市场单位不混用。真实数据发布时间延迟、逐笔滑点、资金费率、历史tick变化未完整模拟。盘中路径由OHLC不能确定精确先后，本轮沿用固定保守保护规则。截图价格一致不代表自动同步TradingView参数。", "",
        "本次仅报告已冻结候选的标签与单笔结果；不训练模型，不修改线上监控、推送、实盘仓位或ACTIVE。若未达到80%，继续诚实报告差距，不能靠删除失败币、排除困难事件或挪动时间窗口达标。", "",
        "覆盖状态：" + json.dumps(coverage.status.value_counts().to_dict(),ensure_ascii=False) + "。完整来源和每张图的SHA存入report_manifest.json。", "",
        "## 下一步：独立冻结验证，而非本轮继续调阈值", "",
        "当晚此前没有参考仓的%d个+1根内漏报正事件中，%d个在事件根与下一根都未过55%%方向效率。三根净推进除以三根TR，会排除部分长影线或来回波动的初始突破；这提示当前‘推进必须整齐’的多条件组合值得重新检验。计数非互斥，不代表只放开效率就会多抓%d个，更不代表这些新增提醒能盈利。" %
        (efficiency_miss.missed_events,efficiency_miss.fail_both,efficiency_miss.fail_both),"",
        "可以另行冻结‘更早的结构突破’假设，再独立检验仅用当时已收盘市场数据的广度确认。两项分别预登记，并同时检查正例召回、误报、成本和随机超额；保留新的前向观察窗口。本轮不按NEAR或PEPE逐个放宽阈值，不进行新网格搜索，也不承诺收益改善。", "",
        "## 复现命令", "", "先提交计划、检测/研究/报告builder；输出目录拒绝覆盖。运行顺序为先全池冻结，再统一评分，最后纯展示报告：", "", "```bash",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_recall_study prepare",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_recall_study evaluate",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_recall_gates",
        ".venv/bin/python -m yoyo.evaluation.spike_burst_recall_report", "```", "",
        "报告命令会立即调用 scripts/md_to_html.py，生成同名自包含HTML。输入包括 prepared/validation_manifest 链、matching.json、独立标签与检测表、事件交易及控制表、指定案例旧门槛审计。报告不重新回放信号、不调用交易模拟、不选择新参数。"]
    return "\n".join(lines)+"\n"


def run(folder=EXP/"results", report=REPORT):
    folder, report = Path(folder).resolve(), Path(report).resolve()
    figures, receipt = folder/"report_figures", folder/"report_manifest.json"
    html = report.parent/"html"/(report.stem+".html")
    if any(p.exists() for p in (report, html, figures, receipt)):
        raise ValueError("Refusing frozen report/image overwrite")
    evidence=Evidence(folder)
    sources=committed(evidence)
    signals=evidence.csv("signals.csv.gz")
    trades=evidence.csv("trade_events.csv.gz")
    changes=evidence.csv("signal_changes.csv.gz")
    figures.mkdir(parents=True)
    style()
    examples,failure=render_cases(evidence,signals,trades,changes,figures)
    text=report_text(evidence,examples,failure,report)
    evidence.finish()
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(text)
    subprocess.run([sys.executable,str(ROOT/"scripts/md_to_html.py"),str(report),"--out-dir",str(html.parent)],cwd=ROOT,check=True)
    if not html.is_file():
        raise ValueError("HTML conversion did not produce its artifact")
    evidence.finish()
    for source in sources:
        check(source["path"],source["sha256"])
    images=[Path(x["path"]) for x in examples]+([Path(failure["path"])] if failure else [])
    manifest=dict(status="complete",config=study.CONFIG,
        code_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),sources=sources,input_artifacts=list(evidence.read.values()),
        no_signal_replay=True,no_trade_scoring=True,no_account_returns=True,
        chart_contract="Three fixed96-bar specified cases; sixMAs/volume/zero-axisIMACD; saved V1/V2 arrows and BJT open/confirm clocks; one lowest-net natural new-only V2 event if present",
        examples=examples,failure=failure,artifacts=[artifact(p) for p in [report,html]+images])
    receipt.write_text(json.dumps(study.clean(manifest),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,default=EXP/"results")
    parser.add_argument("--report",type=Path,default=REPORT)
    args=parser.parse_args()
    run(args.results,args.report)
