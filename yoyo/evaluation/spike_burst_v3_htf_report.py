"""Read-only report of the fifth fixed V3 structural hypothesis.

The caller supplies the completed E validation receipt SHA before any result is
read. Its prepared receipt, sources, outputs and the frozen historical A/B/C and
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
EXP = ROOT / "experiments/active/exp-spike-v3-htf-gate-20260910-v1"
OUT = EXP / "results"
MD = ROOT / "analysis/p1_spike_v3_htf_gate_20260910.md"
HTML = ROOT / "analysis/html/p1_spike_v3_htf_gate_20260910.html"
AB = ROOT / "experiments/active/exp-spike-v3-focus-20260910-v1/results"
D_SOURCE = ROOT / "experiments/active/exp-spike-v3-formation-gate-20260910-v1/results"
D_VALIDATION_SHA = "e9847380c17d74f24cc1db8b065d387d8e621286dd46069376e36162d5c005f9"
C_SOURCE = ROOT / "experiments/active/exp-spike-v3-confirmation-gate-20260910-v1/results"
C_VALIDATION_SHA = "ae9c40bf2e264763dfc517f3ff8afa6d64903800cd145bbdc50b0a09961e9209"
AB_VALIDATION_SHA = "b94367b06517fd4639473dbf6a4806cdf9d12dddbb7cfa59c3a1f98f5f8d121b"
ORIGINAL = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2/results"
ORIGINAL_HASHES = {
    "recall_summary.csv": "1eca062ab758fb4ea86f0936bfcd459e241c80aad7a97585b2ef74f66f145056",
    "trade_summary.csv": "07762c6f1f50e2ffb03917b3304f908605f27dbef9fd69b10d20aff5138d0d02",
    "signals.csv.gz": "308bbe4d2d1f8622aba5b10ee0463d01fbebf54cb48e2aed21341f19924c80bd",
}
START, END = pd.Timestamp("2026-07-10T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")
BJT = "Asia/Shanghai"
NAMES = {"v1": "原版 V1（历史对照）", "v3": "V3 基准", "htf_gate": "E · 已闭合4H背景",
         "formation_gate": "D · 突破前收拢（已失败）",
         "confirmation_gate": "C · 确认后抑制（已失败）",
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
    """Link E receipts and authenticate historical hypotheses before CSV reads."""
    inputs = {}
    vp = checked(OUT / "validation_manifest.json", validation_sha, inputs)
    v = json.loads(vp.read_text())
    pp = checked(OUT / "prepared_manifest.json", v["prepared_sha"], inputs)
    p = json.loads(pp.read_text())
    if p["status"] != "complete" or v["status"] != "complete":
        raise ValueError("Completed globally prepared/evaluated E receipts required")
    if p["config"] != v["config"] or p["source_pins"] != v["source_pins"]:
        raise ValueError("Prepared and evaluated E configurations differ")
    if (p["config"]["arms"] != ["v3", "htf_gate"] or v["prior_AB_validation_sha"] != AB_VALIDATION_SHA
            or v["prior_C_validation_sha"] != C_VALIDATION_SHA or v["prior_D_validation_sha"] != D_VALIDATION_SHA):
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
    cv = json.loads(checked(C_SOURCE / "validation_manifest.json", C_VALIDATION_SHA, inputs).read_text())
    cp = json.loads(checked(C_SOURCE / "prepared_manifest.json", cv["prepared_sha"], inputs).read_text())
    if cv["status"] != "complete" or cp["status"] != "complete" or cv["source_pins"] != cp["source_pins"]:
        raise ValueError("Historical C receipt linkage differs")
    for item in cv["artifacts"] + cp["artifacts"]:
        checked(item["path"], item["sha256"], inputs)
    dv = json.loads(checked(D_SOURCE / "validation_manifest.json", D_VALIDATION_SHA, inputs).read_text())
    dp = json.loads(checked(D_SOURCE / "prepared_manifest.json", dv["prepared_sha"], inputs).read_text())
    if dv["status"] != "complete" or dp["status"] != "complete" or dv["source_pins"] != dp["source_pins"]:
        raise ValueError("Historical D receipt linkage differs")
    for item in dv["artifacts"] + dp["artifacts"]:
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
    for ax, arm in zip(axes[:2], ("v3", "htf_gate")):
        if arm == "htf_gate":
            unknown = s.htf_gate_htf_unknown.eq(True).to_numpy()
            opposed = s.htf_gate_htf_opposed.eq(True).to_numpy()
            for mask, color in ((unknown, "#A5ABB3"), (opposed, "#CF8F94"), (~unknown & ~opposed, "#8CBCAF")):
                for start, end in segments(mask):
                    ax.axvspan(start - .5, end + .5, color=color, alpha=.17, zorder=0)
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
                               parent_time=r["parent_decision_time"], confirm_age=r["confirm_age"],
                               htf_close=r["htf_parent_close"], htf_count=r["htf_parent_count"],
                               htf_state="未知：不加过滤" if r["htf_parent_unknown"] else "已知反对" if r["htf_parent_opposed"] else "已知不反对")
                           for r in group.to_dict("records")]
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
    axes[1].legend(handles=[Patch(color="#A5ABB3", alpha=.3, label="4H未知：保留V3，非通过"),
        Patch(color="#CF8F94", alpha=.3, label="已闭合4H反对：md<sb"),
        Patch(color="#8CBCAF", alpha=.3, label="已知不反对（含双零中性）")], fontsize=8, loc="upper left")
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
    fig.suptitle(f"{asset} · OKX 1H · V3 / E 固定日期完整 96 根\n六条均线、IMACD 零轴与成交量；历史复盘图不作为检测器输入", fontsize=15, fontweight="bold")
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
    if MD.exists() or HTML.exists() or (OUT / "htf_report_manifest.json").exists():
        raise ValueError("Refusing to overwrite a completed report")
    prepared, validation, inputs = authenticate(validation_sha)
    signals, labels = read("signals.csv.gz"), read("labels.csv.gz")
    for frame in (signals, labels): frame["decision_time"] = pd.to_datetime(frame.decision_time, utc=True)
    retention, trades, scores = read("retention_summary.csv"), read("trade_summary.csv"), read("score_summary.csv")
    versions, family = read("version_comparison.csv"), read("structural_holm_family.csv")
    background = read("background_summary.csv")
    assessment = json.loads((OUT / "assessment.json").read_text())
    selected = labels[labels.decision_time.ge(START) & labels.decision_time.lt(END)]
    if len(labels) != 14904 or len(selected) != 8046 or selected.label.eq("positive").sum() != 1660:
        raise ValueError("Fixed label denominator drift")
    full = retention[retention.period.eq("full") & retention.stage.eq("early")].set_index("arm")
    if (int(full.loc["v3", "signals"]), int(full.loc["v3", "hits_1"]), int(full.loc["v3", "large_positive_events"])) != (10386, 1463, 947):
        raise ValueError("Frozen V3 baseline drift")
    if set(family.arm) != {"reference", "near_box", "confirmation_gate", "formation_gate", "htf_gate"} or len(family) != 5:
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
    c = full.loc["htf_gate"]
    economic = trades[trades.arm.eq("htf_gate") & trades.period.eq("full")].iloc[0]
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
        dict(gate="正匹配超额且五假设Holm p<.01", threshold="二者同时", value=f"{number(economic.mean_excess_bp)} bp / {number(economic.holm_p_five, 5)}", passed=assessment["economic_pass"]),
    ])
    v1trade = original_trades[original_trades.arm.eq("v1") & original_trades.period.eq("full")]
    body = ["# SPIKE V3：只用已收盘4H背景，能否减少假启动",
        f"**E {verdict}。** 合并标签减少{pct(c.label_drop)}，V3原及时正例保留{pct(c.retention)}，大幅行情及时召回{pct(c.large_recall_1)}。父预警相对匹配随机的平均超额为{number(economic.mean_excess_bp)}bp，五假设Holm p={number(economic.holm_p_five, 5)}。这些是固定历史事件结果，不是账户收益或已部署指标。",
        "本轮唯一变化：只在信号时已经结束且预热足够的4H IMACD满足md<sb时否决V3父预警。原长度34/9不变，md>=sb包含双零中性。历史不足341根完整4H时保持原V3资格，明确标为未知，不能称为高周期通过。原raw上升沿、接受后12根冷却、1H确认及风险退出保持。没有叠加A—D。",
        "## 固定验收与原版对照",
        table(gate_rows, [("gate", "固定门", None), ("threshold", "目标", None), ("value", "实际", None), ("passed", "结果", lambda value: "通过" if value else "未通过")]),
        table(pd.DataFrame(overview), [("arm", "版本", name), ("parent_signals", "父预警/原信号", integer), ("children", "确认升级", integer), ("distinct_labels", "同根合并标签", integer), ("label_drop", "标签减少", pct), ("recall_1", "1660正例及时召回", pct), ("large_recall_1", "947大幅行情召回", pct), ("v3_fresh_hit_retention", "V3旧命中保留", pct)]),
        "固定目标：合并标签≤6021；1463原及时正例至少保留1317；947大幅正例至少命中758；匹配随机净超额>0且校正p<.01。原V1为认证历史结果，未重新检测，其历史对照组不等同于本轮V3/E共同随机匹配。",
        "历史失败研究：" + "、".join(f"[{label}]({ROOT / 'analysis/html' / filename})" for label, filename in (
            ("13项单门", "p1_spike_v3_gate_diagnostic_20260910.html"), ("A/B结构", "p1_spike_v3_focus_20260910.html"),
            ("C确认后抑制", "p1_spike_v3_confirmation_gate_20260910.html"), ("D均线形成", "p1_spike_v3_formation_gate_20260910_r2.html"))) + "。本轮是第五项固定探索，不能因换机制就假定成功。",
        "## 原数据与严格时钟",
        "同一认证278个OKX合约，不含BTC/ETH，仅多头1H；UTC [2026-07-10,2026-09-09) 共61天。8046研究锚点含1660正例、6240负例、146未知；含预热14904行原标签全部保留。不取新行情、不按今日榜单选币。",
        "每4根连续完整1H按UTC00/04/08等边界聚合：开盘取首、最高取最大、最低取最小、收盘取末、成交量求和。丢弃头尾不完整桶，断档不继承旧状态。用聚合OHLC重新计算原SMMA34/ZLEMA34/SMA9，不能平均1H的MD。",
        "1H决策时刻=该根开盘+1小时；只用4H close<=决策时刻的最新完整4H，包含精确同刻收盘。例：11:00开盘的1H于12:00确认，可以用08—12刚结束的4H；11:00确认只能用04—08，不能偷看08—12未结束值。元数据冻结在父预警根，child仍只用原1H确认条件，不重新按child时的4H筛选，也不回填父时间。",
        "原正例是未来24根收盘路径先触及+4ATR而非-2ATR；947大幅子组是在这些正例中未来24根最高high相对锚点收盘涨幅≥8%。未来只用于标签和复盘。及时新父仍要求原锚点t到t+1，提前参考和后续升级都不能补进该分子。",
        "## 未知背景与已知反对必须分开",
        table(background[background.period.isin(key_periods)], [("period", "时期", None), ("eligible_1h_hours", "可研究合约小时", integer), ("htf_ready_hours", "4H已就绪", integer), ("htf_unknown_hours", "4H未知", integer), ("unknown_fraction", "未知占比", pct), ("v3_early_unknown", "V3未知背景父", integer), ("e_early_unknown", "E未知仍保留父", integer), ("e_early_known", "E已知不反对父", integer), ("v3_early_opposed", "原V3父遇4H反对", integer), ("e_edges_blocked_by_known_opposition", "E实际拒绝边沿", integer)]),
        "未知保留是数据覆盖策略，绝不是共振通过。E实际拒绝数只含raw边沿已满足冷却且4H明确反对；它与原V3父中遇反对的数量可能不同，因为接受后的冷却时间会重新演进。这两种数量不能相减当独立收益，也不能把未知算成成功过滤。",
        table(retention[retention.period.eq("full")], [("arm", "版本", name), ("stage", "阶段", stage_name), ("signals", "阶段事件", integer), ("htf_unknown_signals", "父根4H未知", integer), ("htf_known_signals", "父根4H已知", integer), ("htf_unknown_positive_events", "锚点时4H未知正例", integer), ("htf_opposed_positive_events", "锚点时4H反对正例", integer), ("positive_events", "固定正例分母", integer)]),
        "上表事件背景取父根；正例背景覆盖取锚点根，两者时钟不同，均不改变1660/947/1463固定分母。已知md>=sb也不等于4H已成多头：两条线都负但主线相对改善仍可通过。",
        "## 三张固定日期完整走势图",
        "固定HYPE/NEAR/PEPE，开盘北京时间8月18日00:00到21日23:00，各96根。两幅价格图显示V3/E实际保存标记、原V1灰星、六均线，另有IMACD零轴/信号线及成交量。E背景灰=未知（保留原V3），红=已知反对，绿=已知不反对；不是持仓背景。竖线是用户所指K线的收盘：NEAR/PEPE 19日23:00、HYPE 20日00:00。",
    ]
    event_columns = [("arm", "版本", name), ("stage", "类型", stage_name), ("time", "真实收盘北京时间", stamp), ("signal_close", "当根报价", price), ("parent_time", "父预警时间", stamp), ("confirm_age", "升级等待根", integer), ("htf_close", "父根所用4H收盘", stamp), ("htf_state", "父根4H状态", lambda value: "不适用" if pd.isna(value) else value)]
    for image in images:
        body += ["### " + image["asset"],
            f"![{image['asset']}固定96根完整走势](../experiments/active/exp-spike-v3-htf-gate-20260910-v1/results/figures/{image['asset'].lower()}.png)",
            table(pd.DataFrame(image["signals"]), event_columns) if image["signals"] else "固定窗口没有实际保存信号；不补画信号。"]
    body += ["## 关键分期保留与漏报",
        "前后期和案例夜是已见历史的描述性切片，不是盲OOS；完整逐周和确认阶段在原始CSV保留。",
        table(key_retention, [("arm", "版本", name), ("period", "时期", None), ("signals", "父预警", integer), ("distinct_labels", "合并标签", integer), ("label_drop", "标签减少", pct), ("hits_1", "及时命中", integer), ("positive_events", "正例分母", integer), ("retained_hit_events", "保留旧命中", integer), ("baseline_hit_events", "旧命中分母", integer), ("retention", "保留率", pct), ("large_recall_1", "大幅行情召回", pct)]),
        table(key_retention, [("arm", "版本", name), ("period", "时期", None), ("same_time_kept", "旧时点保留", integer), ("same_time_removed", "旧时点删除", integer), ("new_times", "新时点", integer), ("newly_caught", "新增捕获原漏例", integer), ("large_positive_events", "大幅分母", integer), ("large_hits_1", "大幅及时命中", integer)]),
        table(retention[retention.period.eq("full")], [("arm", "版本", name), ("stage", "阶段", stage_name), ("matched", "+6内匹配", integer), ("unmatched", "未匹配", integer), ("duplicates", "同锚点重复", integer), ("unknown", "未知标签", integer), ("alerts_per_day", "每日事件", number), ("labels_per_100_asset_days", "每100合约日标签", number)]),
        "未匹配不等于实际亏损，匹配也不等于净盈利；背景未知和标签未知又是不同维度，不能混合。",
        "## 经济结果及共同随机对照",
        "经济只计算真实接受父预警，确认升级不重复开仓；统一次根开盘。过去5根结构低点减0.2ATR与2ATR下限取宽，向外tick；收盘达到2R后启用4ATR跟踪，保护次根有效，无固定止盈。按入场名义20bp往返成本，执行契约完全不变。",
        table(pd.concat([v1trade, trades[trades.period.eq("full")]], ignore_index=True), [("arm", "版本", name), ("valid", "有效事件", integer), ("natural_exits", "自然退出", integer), ("censored", "截尾", integer), ("win_rate", "全部净胜率", pct), ("natural_win_rate", "自然退出净胜率", pct), ("mean_gross_bp", "毛均值bp", number), ("mean_net_bp", "净均值bp", number), ("paired_actual_net_bp", "匹配事件bp", number), ("paired_random_net_bp", "随机bp", number), ("mean_excess_bp", "匹配超额bp", number), ("permutation_p", "置换p", lambda value: number(value, 5)), ("holm_p_five", "五假设Holm p", lambda value: number(value, 5))]),
        "V3/E采用同币×UTC周×因果ATR百分比桶最多3个随机控制，统一排除两组当前与过去12根父/确认，不按未来信号或收益排除；同位置共享控制。缓存源/tick/位置/截止/执行契约一致才复用。V3原事件不变，共同随机日程不同可使超额改变，不能误称V3重调有效。",
        table(trades[trades.period.isin(key_periods)], [("arm", "版本", name), ("period", "时期", None), ("valid", "有效", integer), ("matched", "有对照", integer), ("natural_exits", "自然退出", integer), ("censored", "截尾", integer), ("natural_win_rate", "净胜率", pct), ("mean_gross_bp", "毛bp", number), ("mean_net_bp", "净bp", number), ("paired_random_net_bp", "随机bp", number), ("mean_excess_bp", "超额bp", number), ("permutation_p", "置换p", lambda value: number(value, 5))]),
        "### 五项固定结构假设",
        table(family, [("arm", "假设", name), ("raw_p", "冻结原始p", lambda value: number(value, 6)), ("correction_input_p", "校正输入p", lambda value: number(value, 6)), ("holm_p_five", "五假设Holm p", lambda value: number(value, 6)), ("origin", "来源", None)]),
        "A—D全期原p来自各自冻结结果，没有重新评分；新E加入五假设Holm，缺失p保守按1。此前13项单门探索和反复看过历史的选择偏差未被该局部校正全部消除。E是第五项顺序探索，不是盲测、最优参数或普遍盈利证据。",
        "## 单特征描述对照",
        "没有训练，训练val样本数与val AUC不适用。量比/TR AUC只是描述对事后净盈利排序，最高10%按特征而非收益选择；未用于调整E，不能代替匹配随机净超额。",
        table(scores, [("arm", "版本", name), ("feature", "单特征", None), ("n", "样本", integer), ("descriptive_auc", "描述AUC", lambda value: number(value, 4)), ("top_n", "最高10%数量", integer), ("top_gross_bp", "毛bp", number), ("top_net_bp", "净bp", number), ("top_win_rate", "净胜率", pct), ("top_matched_actual_bp", "匹配事件bp", number), ("top_random_bp", "随机bp", number), ("top_excess_bp", "超额bp", number)]),
        "## 风险、Pine边界与诚实声明",
        "- 真正1H启动可能领先4H改善，因此E会挡住部分早期成功首发；多周期有机制不等于能满足90%保留。未知不作过滤，也不算背景通过。失败保持原分母，不调参数追达标。",
        "- 尚未移植E到Pine，不宣称TradingView与Python逐bar一致。官方常规非重绘HTF写法expr[1]+lookahead_on在新HTF开始时取得上一完整HTF；本研究冻结4H close<=1H decision，包含精确收盘边界，两者不能直接声称一致。未来移植须单独验证精确4H边界更新，或在1H本地聚合；当前不偷偷增加延迟。来源：[Pine重绘文档](https://www.tradingview.com/pine-script-docs/concepts/repainting/)与[跨周期FAQ](https://www.tradingview.com/pine-script-docs/faq/other-data-and-timeframes/)。",
        "- 用户已授权继续研究优化。本轮live_deployed=false、accepted_for_deployment=false表示未完成上线验收，不是未获研究授权。没有修改线上指标、监控、Bark/TG、ACTIVE、订单或其他任务。",
        "- 固定278个OKX合约多头1H不能推及其他市场或未来。跨币同夜行情相关，三张固定复盘图仅解释机制，不能估计胜率。每配置首次消费授权历史holdout不等于未见数据。",
        "- 事件重叠，不是账户组合。未计算账户收益、NAV或最大回撤，不填零；20bp不能完整替代资金费、滑点和冲击。未兑现峰值不等于可实现回报。",
        "## 后续与证据",
        "固定门任何一项未过，E照失败研究保留，不移植上线；即便全过也需未见数据前向验证及独立Pine时钟一致性验证。同历史不断改门得到的好数字不能直接推出未来收益。",
        "完整原始CSV：" + "、".join(f"[{label}]({OUT / filename})" for label, filename in (
            ("召回与背景", "retention_summary.csv"), ("未知覆盖/已知拒绝", "background_summary.csv"), ("收益", "trade_summary.csv"),
            ("原V1/V3/E", "version_comparison.csv"), ("五假设校正", "structural_holm_family.csv"), ("实际信号CSV.gz", "signals.csv.gz"), ("交易事件CSV.gz", "trade_events.csv.gz"))) + "。",
        "独立检查：" + "、".join(f"[{filename}]({EXP / 'qa' / filename})" for filename in ("preflight_review.json", "independent_review.json") if (EXP / 'qa' / filename).exists()) + "。",
        "### 复现",
        "准确计划、detector、study、测试与书面预审均先提交，再prepare全局冻结、再evaluate；报告源码也先提交。已完成产物核验SHA，不删除或覆盖重新评分，缺失缓存不得静默换新数据。",
        "```bash\n.venv/bin/python -m pytest -q tests/test_spike_burst_v3_htf_gate.py tests/test_spike_burst_v3_htf_study.py\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_htf_study prepare\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_htf_study evaluate --workers 3\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_htf_report --validation-sha " + validation_sha + "\n```",
        f"E prepared SHA256：{validation['prepared_sha']}。E validation SHA256：{validation_sha}。A/B：{AB_VALIDATION_SHA}。C：{C_VALIDATION_SHA}。D：{D_VALIDATION_SHA}。",
        "先MD，立即用项目scripts/md_to_html.py同一转换器生成嵌图HTML；完整输入、状态、图片与文档SHA见results/htf_report_manifest.json。",
    ]
    text = "\n\n".join(body) + "\n"
    MD.write_text(text)
    from scripts.md_to_html import CSS, convert
    converter = ROOT / "scripts/md_to_html.py"
    inputs[str(converter)] = sha(converter)
    HTML.parent.mkdir(exist_ok=True)
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += "<title>SPIKE V3：已收盘4H背景研究</title><style>" + CSS + "</style></head><body>"
    document += convert(text, asset_base=MD.parent, embed_images=True) + "</body></html>"
    HTML.write_text(document)
    for path, digest in list(inputs.items()): checked(path, digest, inputs)
    authenticate(validation_sha)
    if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != builder.read_bytes():
        raise ValueError("Report builder changed during rendering")
    manifest = dict(status="complete", prepared_sha256=validation["prepared_sha"], validation_sha256=validation_sha,
        prior_AB_validation_sha256=AB_VALIDATION_SHA, prior_C_validation_sha256=C_VALIDATION_SHA, prior_D_validation_sha256=D_VALIDATION_SHA, report_builder=str(builder), report_builder_sha256=sha(builder),
        report_code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        sources=prepared["source_pins"], inputs=[dict(path=k, sha256=v) for k, v in sorted(inputs.items())], figures=images,
        outputs=[dict(path=str(path), sha256=sha(path)) for path in (MD, HTML)],
        no_detection_or_rescoring=True, no_outcome_based_case_selection=True, no_online_changes=True,
        e_pine_status="not_ported_not_validated", hypotheses="fifth sequential exploratory hypothesis, not blind OOS")
    (OUT / "htf_report_manifest.json").write_text(json.dumps(clean(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(dict(md=str(MD), html=str(HTML), figures=3,
        manifest_sha256=sha(OUT / "htf_report_manifest.json")), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-sha", required=True, help="SHA256 of the independently frozen completed E validation receipt")
    build(parser.parse_args().validation_sha)
