"""Read-only report of the third fixed V3 structural hypothesis.

The caller supplies the completed C validation receipt SHA before any result is
read. Its prepared receipt, sources, outputs and the frozen historical A/B and
original V1 inputs are authenticated. Charts use stored signals/states and
cached causal features, with fixed HYPE/NEAR/PEPE dates; no detector, simulator,
scorer, market fetch or outcome-dependent case selection is imported or called.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v3-confirmation-gate-20260910-v1"
OUT = EXP / "results"
MD = ROOT / "analysis/p1_spike_v3_confirmation_gate_20260910.md"
HTML = ROOT / "analysis/html/p1_spike_v3_confirmation_gate_20260910.html"
AB = ROOT / "experiments/active/exp-spike-v3-focus-20260910-v1/results"
AB_VALIDATION_SHA = "b94367b06517fd4639473dbf6a4806cdf9d12dddbb7cfa59c3a1f98f5f8d121b"
ORIGINAL = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2/results"
ORIGINAL_HASHES = {
    "recall_summary.csv": "1eca062ab758fb4ea86f0936bfcd459e241c80aad7a97585b2ef74f66f145056",
    "trade_summary.csv": "07762c6f1f50e2ffb03917b3304f908605f27dbef9fd69b10d20aff5138d0d02",
    "signals.csv.gz": "308bbe4d2d1f8622aba5b10ee0463d01fbebf54cb48e2aed21341f19924c80bd",
}
START, END = pd.Timestamp("2026-07-10T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")
BJT = "Asia/Shanghai"
NAMES = {"v1": "原版 V1（历史对照）", "v3": "V3 基准", "confirmation_gate": "C · 确认后抑制",
         "reference": "A · 参考占用（已失败）", "near_box": "B · 完整近零盒（已失败）"}
STAGES = {"early": "新预警", "confirmed": "确认升级", "original": "原版信号"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, digest, inputs):
    path = Path(path).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(path) != digest:
        raise ValueError("Changed authenticated input: " + str(path))
    inputs[str(path)] = digest
    return path


def authenticate(validation_sha):
    """Link C receipts and authenticate historical hypotheses before CSV reads."""
    inputs = {}
    vp = checked(OUT / "validation_manifest.json", validation_sha, inputs)
    v = json.loads(vp.read_text())
    pp = checked(OUT / "prepared_manifest.json", v["prepared_sha"], inputs)
    p = json.loads(pp.read_text())
    if p["status"] != "complete" or v["status"] != "complete":
        raise ValueError("Completed globally prepared/evaluated C receipts required")
    if p["config"] != v["config"] or p["source_pins"] != v["source_pins"]:
        raise ValueError("Prepared and evaluated C configurations differ")
    if p["config"]["arms"] != ["v3", "confirmation_gate"] or v["prior_AB_validation_sha"] != AB_VALIDATION_SHA:
        raise ValueError("Wrong structural experiment or historical hypothesis family")
    for item in p["artifacts"] + v["artifacts"]:
        checked(item["path"], item["sha256"], inputs)
    for path, digest in p["sources"].items():
        checked(path, digest, inputs)
    for relative, digest in p["source_pins"].items():
        checked(ROOT / relative, digest, inputs)
    av = json.loads(checked(AB / "validation_manifest.json", AB_VALIDATION_SHA, inputs).read_text())
    ap = json.loads(checked(AB / "prepared_manifest.json", av["prepared_sha"], inputs).read_text())
    if av["status"] != "complete" or ap["status"] != "complete" or av["source_pins"] != ap["source_pins"]:
        raise ValueError("Historical A/B receipts differ")
    for item in av["artifacts"] + ap["artifacts"]:
        checked(item["path"], item["sha256"], inputs)
    for name, digest in ORIGINAL_HASHES.items():
        checked(ORIGINAL / name, digest, inputs)
    return p, v, inputs


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)): return value.item()
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA: return None
    if isinstance(value, (pd.Timestamp, Path)): return str(value)
    return value


def number(value, digits=2):
    return "不适用/未知" if pd.isna(value) else f"{float(value):,.{digits}f}"


def pct(value): return "不适用/未知" if pd.isna(value) else f"{100 * float(value):.2f}%"
def integer(value): return "不适用/未知" if pd.isna(value) else str(int(value))
def name(value): return NAMES.get(value, str(value))
def stage_name(value): return STAGES.get(value, str(value))
def price(value): return "未知" if pd.isna(value) else f"{float(value):.8g}"


def stamp(value):
    if pd.isna(value): return "未知"
    time = pd.Timestamp(value)
    if time.tzinfo is None: time = time.tz_localize("UTC")
    return time.tz_convert(BJT).strftime("%m-%d %H:%M")


def table(frame, columns):
    lines = ["| " + " | ".join(label for _, label, _ in columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in frame.to_dict("records"):
        cells = []
        for key, _, formatter in columns:
            value = row.get(key, np.nan)
            cells.append(str(formatter(value) if formatter else value).replace("|", "/").replace("\n", " "))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def segments(mask):
    return zip(np.flatnonzero(mask & ~np.r_[False, mask[:-1]]),
               np.flatnonzero(mask & ~np.r_[mask[1:], False]))


def render_case(asset, frame, state, events, original_events, destination):
    """Render fixed 96 bars, with every marker taken from stored event rows."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle
    from matplotlib.ticker import FuncFormatter
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    begin = pd.Timestamp("2026-08-18T00:00", tz=BJT).tz_convert("UTC")
    selected = np.flatnonzero((frame.index >= begin) & (frame.index < begin + pd.Timedelta(hours=96)))
    if len(selected) != 96 or np.any(np.diff(selected) != 1):
        raise ValueError("Fixed case must contain 96 complete hourly bars: " + asset)
    f, s = frame.iloc[selected], state.iloc[selected]
    clocks = f.index + pd.Timedelta(hours=1)
    if not np.array_equal(s.decision_i.to_numpy(int), selected):
        raise ValueError("Saved state indices differ from feature positions")
    if not np.array_equal(pd.DatetimeIndex(pd.to_datetime(s.decision_time, utc=True)).as_unit("ns").asi8,
                          clocks.as_unit("ns").asi8):
        raise ValueError("Saved signal close clocks differ from feature clocks")
    focus = pd.Timestamp("2026-08-20T00:00" if asset == "HYPE" else "2026-08-19T23:00", tz=BJT)
    focus_x = int(np.argmin(np.abs(clocks.asi8 - focus.value)))
    x, local_clocks = np.arange(96), clocks.tz_convert(BJT)
    up = f.close.ge(f.open).to_numpy()
    colors = np.where(up, "#069782", "#D15E68")
    span = float(f.high.max() - f.low.min())
    ma_colors = ("#318B87", "#6CA7A0", "#456EB1", "#8FA4C8", "#555966", "#9A9DA5")
    fig, axes = plt.subplots(4, 1, figsize=(16, 12), sharex=True,
        gridspec_kw={"height_ratios": [3.3, 3.3, 1.5, 1.1]}, constrained_layout=True)
    fig.patch.set_facecolor("white")
    event_rows = []
    for ax, arm in zip(axes[:2], ("v3", "confirmation_gate")):
        if arm == "confirmation_gate":
            active = s.confirmation_gate_reference_active.eq(True).to_numpy()
            suppressing = s.confirmation_gate_suppression_active.eq(True).to_numpy()
            for mask, color, alpha in ((active & ~suppressing, "#D0B57A", .17), (suppressing, "#9C8BC7", .17)):
                for start, end in segments(mask): ax.axvspan(start - .5, end + .5, color=color, alpha=alpha, zorder=0)
        for j, bar in enumerate(f.itertuples()):
            ax.vlines(j, bar.low, bar.high, color=colors[j], lw=.9, zorder=2)
            ax.add_patch(Rectangle((j - .3, min(bar.open, bar.close)), .6,
                max(abs(bar.close - bar.open), span * .0007, 1e-12),
                facecolor=colors[j], edgecolor=colors[j], lw=.4, zorder=3))
        for key, color in zip(("s20", "e20", "s60", "e60", "s120", "e120"), ma_colors):
            ax.plot(x, f[key].to_numpy(), lw=.9, color=color, alpha=.78, zorder=1)
        local = events[events.arm.eq(arm) & events.decision_i.isin(selected)]
        for stage in ("early", "confirmed"):
            actual = set(local.loc[local.stage.eq(stage), "decision_i"].astype(int))
            stored = set(selected[s[arm + "_" + stage].eq(True).to_numpy()])
            if actual != stored: raise ValueError("Stored markers differ from stored event identities")
        for rank, (i, group) in enumerate(local.groupby("decision_i", sort=True)):
            j = int(i) - int(selected[0])
            point = float(f.close.iloc[j])
            if not np.all(group.signal_close.to_numpy(float) == point):
                raise ValueError("Saved signal price differs from actual signal bar")
            combined = len(group) == 2
            child = group.stage.eq("confirmed").any()
            color, marker = ("#067B73", "D") if child else ("#AA721C", "^")
            ax.scatter(j, point, s=45, color=color, marker=marker, edgecolors="white", lw=.5, zorder=6)
            label = "预警+确认" if combined else "确认升级" if child else "新预警"
            ax.annotate(f"{local_clocks[j]:%m-%d %H:%M}\n{label} {price(point)}", (j, point),
                xytext=(0, 15 + rank % 3 * 18), textcoords="offset points", ha="center", fontsize=6.5,
                color=color, bbox=dict(boxstyle="round,pad=.2", fc="white", ec="none", alpha=.86),
                arrowprops=dict(arrowstyle="-", color=color, lw=.5), zorder=7)
            event_rows += [dict(arm=arm, stage=r["stage"], time=r["decision_time"], signal_close=r["signal_close"],
                               parent_time=r["parent_decision_time"], confirm_age=r["confirm_age"]) for r in group.to_dict("records")]
        historical = original_events[original_events.decision_i.isin(selected)]
        if arm == "v3":
            for event in historical.to_dict("records"):
                j = int(event["decision_i"]) - int(selected[0])
                point = float(event["signal_close"])
                if point != float(f.close.iloc[j]): raise ValueError("Original V1 price/position mismatch")
                if pd.Timestamp(event["decision_time"]) != clocks[j]: raise ValueError("Original V1 clock mismatch")
                ax.scatter(j, point, marker="*", s=115, color="#525969", edgecolors="white", lw=.5, zorder=8)
                ax.annotate(f"V1 {stamp(event['decision_time'])}", (j, point), xytext=(0, -20),
                    textcoords="offset points", ha="center", fontsize=7, color="#525969")
                event_rows.append(dict(arm="v1", stage="original", time=event["decision_time"], signal_close=point,
                                       parent_time=pd.NaT, confirm_age=np.nan))
        ax.set_title(f"{NAMES[arm]} · {int(local.stage.eq('early').sum())} 次新预警 / {int(local.stage.eq('confirmed').sum())} 次升级"
                     + (f" · V1 灰星 {len(historical)} 次" if arm == "v3" else ""),
                     loc="left", fontsize=11, fontweight="bold", color="#3F5968")
        ax.set_ylim(float(f.low.min()) - span * .07, float(f.high.max()) + span * .20)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.6g}"))
    axes[0].legend(handles=[Line2D([], [], marker="^", color="#AA721C", ls="", label="新预警"),
        Line2D([], [], marker="D", color="#067B73", ls="", label="确认升级（不再开仓）"),
        Line2D([], [], marker="*", color="#525969", ls="", markersize=10, label="原V1真实保存信号")], fontsize=8, loc="upper left")
    axes[1].legend(handles=[Patch(color="#D0B57A", alpha=.25, label="临时收盘参考：没有抑制权"),
        Patch(color="#9C8BC7", alpha=.25, label="活跃已确认参考：有抑制权")], fontsize=8, loc="upper left")
    axes[2].plot(x, f.md.to_numpy(), color="#416FBD", lw=1.5, label="IMACD")
    axes[2].plot(x, f.sb.to_numpy(), color="#BF8027", lw=1.4, label="信号线")
    axes[2].axhline(0, color="#677780", lw=.8)
    axes[2].legend(loc="upper left", fontsize=8, ncol=2)
    axes[2].set_ylabel("IMACD")
    axes[3].bar(x, f.volume.to_numpy(), color=colors, width=.65, alpha=.78)
    axes[3].set_ylabel("成交量")
    for ax in axes:
        ax.axvline(focus_x, color="#607584", ls="--", lw=.8)
        ax.grid(alpha=.14)
        ax.spines[["top", "right"]].set_visible(False)
    axes[-1].set_xticks(np.arange(0, 96, 12), [local_clocks[j].strftime("%m-%d\n%H:%M") for j in range(0, 96, 12)], fontsize=9)
    axes[-1].set_xlabel("每根收盘北京时间；同根合并显示，确认绝不回填到父预警根")
    fig.suptitle(f"{asset} · OKX 1H · V3 / C 固定日期完整 96 根\n六条均线、IMACD 零轴与成交量；历史复盘图不作为检测器输入", fontsize=15, fontweight="bold")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=145, facecolor="white")
    plt.close(fig)
    return dict(asset=asset, bars=96, first_open=f.index[0], last_open=f.index[-1], first_close=clocks[0],
                last_close=clocks[-1], focus_close=focus, image=str(destination), image_sha256=sha(destination),
                signals=event_rows, selection="fixed owner assets and dates; no future-outcome selection")


def read(name): return pd.read_csv(OUT / name, float_precision="round_trip")


def build(validation_sha):
    builder = Path(__file__).resolve()
    relative = str(builder.relative_to(ROOT))
    if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != builder.read_bytes():
        raise ValueError("Commit the exact report builder before generating products")
    if MD.exists() or HTML.exists() or (OUT / "confirmation_report_manifest.json").exists():
        raise ValueError("Refusing to overwrite a completed report")
    prepared, validation, inputs = authenticate(validation_sha)
    signals, labels = read("signals.csv.gz"), read("labels.csv.gz")
    for frame in (signals, labels): frame["decision_time"] = pd.to_datetime(frame.decision_time, utc=True)
    retention, trades, scores = read("retention_summary.csv"), read("trade_summary.csv"), read("score_summary.csv")
    versions, family = read("version_comparison.csv"), read("structural_holm_family.csv")
    assessment = json.loads((OUT / "assessment.json").read_text())
    selected = labels[labels.decision_time.ge(START) & labels.decision_time.lt(END)]
    if len(labels) != 14904 or len(selected) != 8046 or selected.label.eq("positive").sum() != 1660:
        raise ValueError("Fixed label denominator drift")
    full = retention[retention.period.eq("full") & retention.stage.eq("early")].set_index("arm")
    if (int(full.loc["v3", "signals"]), int(full.loc["v3", "hits_1"]), int(full.loc["v3", "large_positive_events"])) != (10386, 1463, 947):
        raise ValueError("Frozen V3 baseline drift")
    if set(family.arm) != {"reference", "near_box", "confirmation_gate"} or len(family) != 3:
        raise ValueError("The full structural multiplicity family is required")
    original_recall = pd.read_csv(ORIGINAL / "recall_summary.csv", float_precision="round_trip")
    original_trades = pd.read_csv(ORIGINAL / "trade_summary.csv", float_precision="round_trip")
    original_signals = pd.read_csv(ORIGINAL / "signals.csv.gz", float_precision="round_trip")
    original_signals = original_signals[original_signals.arm.eq("v1")]
    for r in versions[versions.arm.eq("v1")].to_dict("records"):
        historical = original_recall[original_recall.arm.eq("v1") & original_recall.period.eq(r["period"])].iloc[0]
        if r["parent_signals"] != historical.signals or r["hits_1"] != historical.hits_1:
            raise ValueError("Preserved original V1 comparison differs from authenticated history")
    matching = json.loads((OUT / "matching.json").read_text())
    if len(matching["jobs"]) != 278 or len(matching["state_artifacts"]) != 278:
        raise ValueError("Fixed instrument universe drift")
    images = []
    for asset in ("HYPE", "NEAR", "PEPE"):
        located = [(j, a) for j, a in zip(matching["jobs"], matching["state_artifacts"]) if j["asset"] == asset]
        if len(located) != 1: raise ValueError("Ambiguous fixed illustration source: " + asset)
        job, state_artifact = located[0]
        feature = checked(job["features_path"], job["features_sha256"], inputs)
        frame = pd.read_pickle(feature)
        frame = frame.loc[frame.index + pd.Timedelta(hours=1) <= END]
        state = pd.read_csv(checked(state_artifact["path"], state_artifact["sha256"], inputs), float_precision="round_trip")
        if len(frame) != len(state): raise ValueError("Stored full state length differs from source feature length")
        images.append(render_case(asset, frame, state, signals[signals.instrument.eq(job["instrument"])],
            original_signals[original_signals.instrument.eq(job["instrument"])], OUT / "figures" / (asset.lower() + ".png")))
    c = full.loc["confirmation_gate"]
    economic = trades[trades.arm.eq("confirmation_gate") & trades.period.eq("full")].iloc[0]
    key_periods = ("full", "first31", "last30", "case_night")
    key_retention = retention[retention.stage.eq("early") & retention.period.isin(key_periods)]
    structural_pass = all(assessment[k] for k in ("label_drop_pass", "large_recall_pass", "baseline_retention_pass"))
    verdict = "三项固定结构目标均达到，但仍需检查经济门与独立前向验证" if structural_pass else "没有同时达到少提示与保留正确启动的固定目标"
    overview = []
    for row in versions[versions.period.eq("full")].to_dict("records"):
        arm = row["arm"]
        child = retention[retention.arm.eq(arm) & retention.stage.eq("confirmed") & retention.period.eq("full")]
        row.update(children=int(child.signals.iloc[0]) if len(child) else np.nan,
                   label_drop=full.loc[arm, "label_drop"] if arm in full.index else np.nan)
        overview.append(row)
    gate_rows = pd.DataFrame([
        dict(gate="同根合并标签减少", threshold="≥50%", value=pct(c.label_drop), passed=assessment["label_drop_pass"]),
        dict(gate="947个大幅行情的及时新预警召回", threshold="≥80%", value=pct(c.large_recall_1), passed=assessment["large_recall_pass"]),
        dict(gate="V3原1463个及时正例的新预警保留", threshold="≥90%", value=pct(c.retention), passed=assessment["baseline_retention_pass"]),
        dict(gate="正匹配超额且三假设Holm p<.01", threshold="二者同时", value=f"{number(economic.mean_excess_bp)} bp / {number(economic.holm_p_three, 5)}", passed=assessment["economic_pass"]),
    ])
    v1trade = original_trades[original_trades.arm.eq("v1") & original_trades.period.eq("full")]
    body = ["# SPIKE V3：确认之后再抑制重复预警",
        f"**C {verdict}。** 合并标签减少{pct(c.label_drop)}，原V3及时捕获正例保留{pct(c.retention)}，大幅行情及时召回{pct(c.large_recall_1)}。父预警事件相对匹配随机的平均超额为{number(economic.mean_excess_bp)}bp，三假设Holm p={number(economic.holm_p_three, 5)}。这些是固定历史事件研究结果，不是账户收益或已部署策略。",
        "本轮只改一件事：未确认的弱预警不再长期占用趋势参考；在原0—3根确认窗内确认且旧参考仍存活，才取得抑制后续新预警的权利。第4根仍未确认就撤销临时参考；确认只是升级，不重新开仓、不改初始入场价，也不复活已经结束的参考。没有改变原量能、价格、密集、风险和冷却阈值。",
        "## 固定目标及原版对照",
        table(gate_rows, [("gate", "验收门", None), ("threshold", "预登记阈值", None), ("value", "实际", None), ("passed", "结果", lambda value: "通过" if value else "未通过")]),
        table(pd.DataFrame(overview), [("arm", "版本", name), ("parent_signals", "父预警/原信号", integer), ("children", "确认升级", integer), ("distinct_labels", "同根合并标签", integer), ("label_drop", "标签减少", pct), ("recall_1", "1660正例及时召回", pct), ("large_recall_1", "947大幅行情召回", pct), ("v3_fresh_hit_retention", "V3原及时命中保留", pct)]),
        "原V1是原版强劲爆发指标的认证历史结果，未重新检测。V1没有V3父子分层，其原本的随机对照没有加入本轮共同匹配；因此V1超额只作历史基准，不能与新C对照组视作同一配对实验。",
        "[此前A/B固定研究报告](p1_spike_v3_focus_20260910.html)：A参考占用与B完整近零箱体已经失败，结果保留不改。C是随后提出的第三项结构假设，不能把它称为新的盲测。",
        "## 数据与口径",
        "同一认证池：278个OKX历史合约，不含BTC/ETH，仅多头1H；UTC 2026-07-10（含）至2026-09-09（不含），共61天。研究窗8046个锚点：1660正例、6240负例、146未知；完整含预热标签14904行均保留。没有用今日涨幅榜选币，也没有缩小分母。",
        "正例是原冻结未来24根收盘路径满足在先触及−2ATR之前达到+4ATR的标签，未来只用于标签和复盘。大幅行情947例是这些正例中未来24根最高high相对锚点收盘涨幅≥8%的子组，不是24根收盘涨幅。及时命中是锚点t到t+1内出现新信号；确认升级使用真实确认时间，不回填父预警根。",
        "### 新信号保留与覆盖分别列出",
        table(retention[retention.period.eq("full")], [("arm", "版本", name), ("stage", "阶段", stage_name), ("hits_1", "及时新命中", integer), ("positive_events", "正例分母", integer), ("retained_hit_events", "保留原命中", integer), ("baseline_hit_events", "V3原命中", integer), ("retention", "保留率", pct), ("reference_tracking_positive_events", "旧参考覆盖另列", integer), ("suppression_tracking_positive_events", "已确认抑制覆盖另列", integer)]),
        "旧参考覆盖要求锚点前根与当前根参考均仍有效且当前无该阶段新信号；已确认抑制覆盖再要求两根均有抑制权。临时参考可能覆盖，但没有抑制权。两种覆盖存在包含关系，也可能与t+1新信号重叠，不能相加、不能补回新预警分子。两者都是信号收盘参考状态，不是已验证实际成交持仓；本报告没有把参考覆盖换算成实际仓位覆盖。",
        "## 三张固定日期的完整走势",
        "固定HYPE、NEAR、PEPE，开盘北京时间8月18日00:00至8月21日23:00，各96根1H K线。沿用用户8月19日晚的例子，不根据本轮盈亏选图。价格图分列V3/C，灰星为已保存原V1信号；六均线为SMA/EMA20、60、120，副图保留IMACD、信号线与零轴，下方成交量。竖线对应用户所指K线收盘：NEAR/PEPE为19日23:00，HYPE为20日00:00。",
    ]
    event_columns = [("arm", "版本", name), ("stage", "类型", stage_name), ("time", "真实收盘北京时间", stamp), ("signal_close", "当根报价", price), ("parent_time", "父预警收盘", stamp), ("confirm_age", "升级等待根数", integer)]
    for image in images:
        body += ["### " + image["asset"],
            f"![{image['asset']}固定96根完整走势](../experiments/active/exp-spike-v3-confirmation-gate-20260910-v1/results/figures/{image['asset'].lower()}.png)",
            table(pd.DataFrame(image["signals"]), event_columns) if image["signals"] else "固定窗口没有任何已保存信号；图上不补画信号。"]
    body += ["## 各时段提示、保留、漏报",
        "时期分组沿用预登记窗口。逐周及案例夜是同一历史的描述性切片，不是独立样本外验证。确认阶段的保留率以V3原确认命中的正例为分母；主验收门始终使用父预警阶段。",
        table(key_retention, [("arm", "版本", name), ("stage", "阶段", stage_name), ("period", "时期", None), ("signals", "事件数", integer), ("distinct_labels", "合并标签", integer), ("label_drop", "标签减少", pct), ("hits_1", "及时命中", integer), ("positive_events", "正例分母", integer), ("recall_1", "及时召回", pct), ("retained_hit_events", "保留原命中", integer), ("baseline_hit_events", "原命中分母", integer), ("retention", "保留率", pct)]),
        "### 时点变化与大幅行情",
        table(key_retention, [("arm", "版本", name), ("stage", "阶段", stage_name), ("period", "时期", None), ("large_positive_events", "大幅行情分母", integer), ("large_hits_1", "及时新命中", integer), ("large_recall_1", "大幅行情召回", pct), ("same_time_kept", "同时间保留", integer), ("same_time_removed", "原时点移除", integer), ("new_times", "新时点", integer), ("newly_caught", "新增捕获原漏例", integer), ("reference_tracking_positive_events", "旧参考覆盖", integer), ("suppression_tracking_positive_events", "确认抑制覆盖", integer)]),
        "### 匹配与提示负担",
        table(key_retention, [("arm", "版本", name), ("stage", "阶段", stage_name), ("period", "时期", None), ("matched", "+6内匹配", integer), ("unmatched", "未匹配", integer), ("duplicates", "同锚点重复", integer), ("unknown", "未知", integer), ("alerts_per_day", "每日事件", number), ("labels_per_100_asset_days", "每100有效合约日标签", number)]),
        "未匹配不等于实际亏损，匹配也不是可执行盈利；标签窗口、事件去重、入场成本和最终退出分别影响这些口径。",
        "## 收益与匹配随机对照",
        "只将实际接受的父预警作为独立入场事件，确认升级不重复计交易。所有经济结果统一次根开盘入场；原5根结构低点减0.2ATR与2ATR最小风险取更宽者，收盘到2R启用4ATR跟踪，保护次根生效，无固定止盈。20bp按入场名义计往返成本。检测中的信号收盘参考与次根开盘成交是两条不同路径。",
        table(pd.concat([v1trade, trades[trades.period.eq("full")]], ignore_index=True), [("arm", "版本", name), ("valid", "有效事件", integer), ("natural_exits", "自然退出", integer), ("censored", "截止截尾", integer), ("win_rate", "全部事件净胜率", pct), ("natural_win_rate", "自然退出净胜率", pct), ("mean_gross_bp", "毛均值bp", number), ("mean_net_bp", "净均值bp", number), ("paired_actual_net_bp", "匹配事件净bp", number), ("paired_random_net_bp", "匹配随机净bp", number), ("mean_excess_bp", "匹配超额bp", number), ("permutation_p", "置换p", lambda value: number(value, 5)), ("holm_p_three", "三假设Holm p", lambda value: number(value, 5))]),
        "V3/C共同对照：同币×UTC周×因果ATR百分比桶，每事件最多3个随机日程；统一排除V3/C当前及过去12根父/确认，不看未来信号进行排除，不按未来收益挑控制。同位置共享控制；缓存仅在源hash、tick、时间框架、decision与截止一致时复用。V3实际入场不变，但本轮共同匹配不同，随机超额可与旧报告不同。",
        "以下统计是可能互相重叠的独立事件，不是可同时执行的账户组合。没有仓位、现金占用和组合权益模拟，因此账户收益、NAV、组合最大回撤均未计算，不能把事件R或峰值当已兑现账户回报。截止截尾与自然退出分别列出。",
        "### 三项结构假设的多重比较",
        table(family, [("arm", "假设", name), ("raw_p", "固定原始p", lambda value: number(value, 6)), ("correction_input_p", "校正输入p", lambda value: number(value, 6)), ("holm_p_three", "三假设Holm p", lambda value: number(value, 6)), ("origin", "来源", None)]),
        "A/B全期原p来自冻结旧结果，未重跑；C加入同一三假设Holm家族，缺失p保守按1处理。更早还进行过13项单门探索，且这段历史已被反复观察，三假设校正并未消除整个研究序列的选择偏差。C是顺序探索，绝不是盲OOS；即便局部校正通过，也不足以宣称最优或直接上线。",
        "### 关键分期收益",
        table(trades[trades.period.isin(key_periods)], [("arm", "版本", name), ("period", "时期", None), ("valid", "有效", integer), ("matched", "有对照", integer), ("natural_exits", "自然退出", integer), ("censored", "截尾", integer), ("natural_win_rate", "自然退出净胜率", pct), ("mean_gross_bp", "毛均值bp", number), ("mean_net_bp", "净均值bp", number), ("paired_random_net_bp", "随机净bp", number), ("mean_excess_bp", "超额bp", number), ("asset_balanced_excess_bp", "资产块平衡超额bp", number), ("permutation_p", "置换p", lambda value: number(value, 5))]),
        "## 单特征描述对照",
        "没有训练模型，所以训练val样本数与val AUC不适用。下表量比/TR的AUC只是对事后净盈利排序的描述；top-decile按特征高低而非收益选取，未据此改变C门槛。匹配随机与置换检验是本轮效果对照，不能用AUC代替正净超额。",
        table(scores, [("arm", "版本", name), ("feature", "单特征", None), ("n", "有效样本", integer), ("descriptive_auc", "描述AUC", lambda value: number(value, 4)), ("top_n", "最高10%数量", integer), ("top_gross_bp", "毛bp", number), ("top_net_bp", "净bp", number), ("top_win_rate", "净胜率", pct), ("top_matched_actual_bp", "匹配事件bp", number), ("top_random_bp", "随机bp", number), ("top_excess_bp", "超额bp", number)]),
        "## Pine和部署状态",
        "本报告读取已冻结的Python信号，不重新检测或评分。此前只有A/B研究Pine完成TradingView私有保存与原生UI冒烟检查；C尚未移植到Pine，因此不能称当前TradingView已实现C，也不能称已完成C的Pine/Python全序列一致性验收。没有替换当前指标、改变线上监控、Bark/TG或实盘。",
        "## 风险与诚实声明",
        "- 每配置本次首次消耗已获授权的历史holdout，历史已经被观察和用于形成假设，不是独立样本外。没有把失败目标改写成成功，也没有删掉漏报或用覆盖修复分子。",
        "- 只代表固定278个OKX合约多头1H；不能推及BTC/ETH、空头、其他周期、其他交易所或未来山寨行情。跨币同晚同涨使事件相关，固定三张图只能说明机制，不能估计胜率。",
        "- 20bp不能完整替代资金费、真实滑点和冲击成本。参考存活不是实际仓位存活；未确认第4根撤销参考不是模拟平仓或止损。",
        "- 降噪率、标签召回、旧命中保留、净胜率分别回答不同问题。峰值未兑现，不能从复盘峰值计算可实现收益。未知和截尾不填成零。",
        "## 下一步",
        "本配置按冻结门如实保留结果。若任何结构或经济门未过，继续按失败研究记录，不通过缩分母、叠条件或调同一历史阈值宣称达标。若门通过，也需另行登记未见数据前向验证和C Pine因果一致性验证，才有资格讨论替换当前指标；当前没有部署C。",
        "## 复现与证据",
        "原始表含全部阶段与逐周切片：" + "、".join(f"[{label}]({OUT / filename})" for label, filename in (
            ("完整召回与覆盖CSV", "retention_summary.csv"), ("完整收益CSV", "trade_summary.csv"),
            ("原V1/V3/C对照CSV", "version_comparison.csv"), ("三假设校正CSV", "structural_holm_family.csv"),
            ("实际信号CSV.gz", "signals.csv.gz"), ("实际交易事件CSV.gz", "trade_events.csv.gz"))) + "。",
        "先提交准确计划、detector、study与测试，再prepare全局冻结、再evaluate。已完成评分应核验SHA，不删除或覆盖产物重跑；缺失缓存不能静默替换成新数据。报告builder也须先提交。",
        "```bash\n.venv/bin/python -m pytest -q tests/test_spike_burst_v3_confirmation_gate.py tests/test_spike_burst_v3_confirmation_study.py\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_confirmation_study prepare\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_confirmation_study evaluate --workers 3\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_confirmation_report --validation-sha " + validation_sha + "\n```",
        "报告生成顺序是先MD，立即通过项目scripts/md_to_html.py的相同转换器生成自包含HTML，图片嵌入便于直接打开。完整输入、来源、状态和图片SHA见results/confirmation_report_manifest.json。",
        f"C prepared SHA256：{validation['prepared_sha']}。C validation SHA256：{validation_sha}。A/B validation SHA256：{AB_VALIDATION_SHA}。",
    ]
    text = "\n\n".join(body) + "\n"
    MD.write_text(text)
    from scripts.md_to_html import CSS, convert
    converter = ROOT / "scripts/md_to_html.py"
    inputs[str(converter)] = sha(converter)
    HTML.parent.mkdir(exist_ok=True)
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += "<title>SPIKE V3：确认后抑制研究</title><style>" + CSS + "</style></head><body>"
    document += convert(text, asset_base=MD.parent, embed_images=True) + "</body></html>"
    HTML.write_text(document)
    for path, digest in list(inputs.items()): checked(path, digest, inputs)
    authenticate(validation_sha)
    if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != builder.read_bytes():
        raise ValueError("Report builder changed during rendering")
    manifest = dict(status="complete", prepared_sha256=validation["prepared_sha"], validation_sha256=validation_sha,
        prior_AB_validation_sha256=AB_VALIDATION_SHA, report_builder=str(builder), report_builder_sha256=sha(builder),
        report_code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        sources=prepared["source_pins"], inputs=[dict(path=k, sha256=v) for k, v in sorted(inputs.items())], figures=images,
        outputs=[dict(path=str(path), sha256=sha(path)) for path in (MD, HTML)],
        no_detection_or_rescoring=True, no_outcome_based_case_selection=True, no_online_changes=True,
        c_pine_status="not_ported_not_validated", hypotheses="third sequential exploratory hypothesis, not blind OOS")
    (OUT / "confirmation_report_manifest.json").write_text(json.dumps(clean(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(dict(md=str(MD), html=str(HTML), figures=3,
        manifest_sha256=sha(OUT / "confirmation_report_manifest.json")), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-sha", required=True, help="SHA256 of the independently frozen completed C validation receipt")
    build(parser.parse_args().validation_sha)
