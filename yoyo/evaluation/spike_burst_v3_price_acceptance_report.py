"""Read-only report of the sixth fixed V3 structural hypothesis.

The caller supplies the completed F validation receipt SHA before any result is
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
EXP = ROOT / "experiments/active/exp-spike-v3-price-acceptance-20260910-v1"
OUT = EXP / "results"
MD = ROOT / "analysis/p1_spike_v3_price_acceptance_20260910.md"
HTML = ROOT / "analysis/html/p1_spike_v3_price_acceptance_20260910.html"
AB = ROOT / "experiments/active/exp-spike-v3-focus-20260910-v1/results"
D_SOURCE = ROOT / "experiments/active/exp-spike-v3-formation-gate-20260910-v1/results"
D_VALIDATION_SHA = "e9847380c17d74f24cc1db8b065d387d8e621286dd46069376e36162d5c005f9"
E_SOURCE = ROOT / "experiments/active/exp-spike-v3-htf-gate-20260910-v1/results"
E_VALIDATION_SHA = "d8966ebbe3453fe01c821d37c51b8f5dd0bdd3f5f45f02420b218281ff1716c6"
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
NAMES = {"v1": "原版 V1（历史对照）", "v3": "V3 基准", "price_acceptance": "F · 下一根价格接受", "htf_gate": "E · 已闭合4H背景（已失败）",
         "formation_gate": "D · 突破前收拢（已失败）",
         "confirmation_gate": "C · 确认后抑制（已失败）",
         "reference": "A · 参考占用（已失败）", "near_box": "B · 完整近零盒（已失败）"}
STAGES = {"early": "原预警/公开接受", "confirmed": "确认升级", "original": "原版信号"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, digest, inputs):
    path = Path(path).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(path) != digest:
        raise ValueError("Changed authenticated input: " + str(path))
    inputs[str(path)] = digest
    return path


def authenticate(validation_sha):
    """Link F receipts and authenticate historical hypotheses before CSV reads."""
    inputs = {}
    vp = checked(OUT / "validation_manifest.json", validation_sha, inputs)
    v = json.loads(vp.read_text())
    pp = checked(OUT / "prepared_manifest.json", v["prepared_sha"], inputs)
    p = json.loads(pp.read_text())
    if p["status"] != "complete" or v["status"] != "complete":
        raise ValueError("Completed globally prepared/evaluated F receipts required")
    if p["config"] != v["config"] or p["source_pins"] != v["source_pins"]:
        raise ValueError("Prepared and evaluated F configurations differ")
    if (p["config"]["arms"] != ["v3", "price_acceptance"] or v["prior_AB_validation_sha"] != AB_VALIDATION_SHA
            or v["prior_C_validation_sha"] != C_VALIDATION_SHA or v["prior_D_validation_sha"] != D_VALIDATION_SHA or v["prior_E_validation_sha"] != E_VALIDATION_SHA):
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
    ev = json.loads(checked(E_SOURCE / "validation_manifest.json", E_VALIDATION_SHA, inputs).read_text())
    ep = json.loads(checked(E_SOURCE / "prepared_manifest.json", ev["prepared_sha"], inputs).read_text())
    if ev["status"] != "complete" or ep["status"] != "complete" or ev["source_pins"] != ep["source_pins"]:
        raise ValueError("Historical E receipt linkage differs")
    for item in ev["artifacts"] + ep["artifacts"]:
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
    for ax, arm in zip(axes[:2], ("v3", "price_acceptance")):
        if arm == "price_acceptance":
            raw = events[events.arm.eq("v3") & events.stage.eq("early") & events.decision_i.isin(selected)]
            for candidate in raw.to_dict("records"):
                j = int(candidate["decision_i"]) - int(selected[0])
                ax.scatter(j, float(candidate["signal_close"]), marker="o", facecolors="none", edgecolors="#999DA3", s=26, lw=.9, zorder=5)
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
            label = ("接受+已有确认" if combined else "确认升级" if child else "价格接受") if arm == "price_acceptance" else ("预警+确认" if combined else "确认升级" if child else "新预警")
            ax.annotate(f"{local_clocks[j]:%m-%d %H:%M}\n{label} {price(point)}", (j, point),
                xytext=(0, 15 + rank % 3 * 18), textcoords="offset points", ha="center", fontsize=6.5,
                color=color, bbox=dict(boxstyle="round,pad=.2", fc="white", ec="none", alpha=.86),
                arrowprops=dict(arrowstyle="-", color=color, lw=.5), zorder=7)
            event_rows += [dict(arm=arm, stage=r["stage"], time=r["decision_time"], signal_close=r["signal_close"],
                               candidate_time=r["candidate_time"], candidate_close=r["candidate_close"],
                               original_child_time=r["original_child_time"], original_child_close=r["original_child_close"],
                               publication_time=r["publication_time"], publication_close=r["publication_close"],
                               confirm_age=r["confirm_age"])
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
    axes[1].legend(handles=[Line2D([], [], marker="o", markerfacecolor="none", color="#999DA3", ls="", label="原V3观察时点（保留）"),
        Line2D([], [], marker="^", color="#AA721C", ls="", label="实际下一根接受"),
        Line2D([], [], marker="D", color="#067B73", ls="", label="公开确认升级")], fontsize=8, loc="upper left")
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
    fig.suptitle(f"{asset} · OKX 1H · V3 / F 固定日期完整 96 根\n六条均线、IMACD 零轴与成交量；历史复盘图不作为检测器输入", fontsize=15, fontweight="bold")
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
    if MD.exists() or HTML.exists() or (OUT / "price_acceptance_report_manifest.json").exists():
        raise ValueError("Refusing to overwrite a completed report")
    prepared, validation, inputs = authenticate(validation_sha)
    signals, labels = read("signals.csv.gz"), read("labels.csv.gz")
    for frame in (signals, labels): frame["decision_time"] = pd.to_datetime(frame.decision_time, utc=True)
    retention, trades, scores = read("retention_summary.csv"), read("trade_summary.csv"), read("score_summary.csv")
    versions, family = read("version_comparison.csv"), read("structural_holm_family.csv")
    clocks = read("clock_summary.csv")
    assessment = json.loads((OUT / "assessment.json").read_text())
    selected = labels[labels.decision_time.ge(START) & labels.decision_time.lt(END)]
    if len(labels) != 14904 or len(selected) != 8046 or selected.label.eq("positive").sum() != 1660:
        raise ValueError("Fixed label denominator drift")
    full = retention[retention.period.eq("full") & retention.stage.eq("early")].set_index("arm")
    if (int(full.loc["v3", "signals"]), int(full.loc["v3", "hits_1"]), int(full.loc["v3", "large_positive_events"])) != (10386, 1463, 947):
        raise ValueError("Frozen V3 baseline drift")
    if set(family.arm) != {"reference", "near_box", "confirmation_gate", "formation_gate", "htf_gate", "price_acceptance"} or len(family) != 6:
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
    c = full.loc["price_acceptance"]
    economic = trades[trades.arm.eq("price_acceptance") & trades.period.eq("full")].iloc[0]
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
        dict(gate="公开标签减少（未知加回后也需通过）", threshold="≥50%且≤6021", value=pct(c.label_drop)+" / 保守 "+pct(assessment["conservative_label_drop"])+" · "+integer(assessment["conservative_labels"]), passed=assessment["label_drop_pass"]),
        dict(gate="947个大幅行情的及时新预警召回", threshold="≥80%", value=pct(c.large_recall_1), passed=assessment["large_recall_pass"]),
        dict(gate="V3原1463个及时正例的新预警保留", threshold="≥90%", value=pct(c.retention), passed=assessment["baseline_retention_pass"]),
        dict(gate="正匹配超额且六假设Holm p<.01", threshold="二者同时", value=f"{number(economic.mean_excess_bp)} bp / {number(economic.holm_p_six, 5)}", passed=assessment["economic_pass"]),
    ])
    v1trade = original_trades[original_trades.arm.eq("v1") & original_trades.period.eq("full")]
    body = ["# SPIKE V3：突破后等待一根，价格接受研究",
        f"**F {verdict}。** 同根合并公开标签减少{pct(c.label_drop)}；原V3及时正例保留{pct(c.retention)}；大幅行情及时召回{pct(c.large_recall_1)}。接受父相对共同随机的平均净超额{number(economic.mean_excess_bp)}bp，六假设Holm p={number(economic.holm_p_six, 5)}。这是研究事件结果，不是账户回报或已部署优化。",
        "唯一新变量：原V3父在t收盘发现后，只在完整连续t+1收盘检查价格仍高于该父冻结的前12根最高价。等于/低于拒绝；无下一根、断档或下一根OHLC非有限/不合法均未知。不要求盘中未跌破，也不要求比t的收盘更高；OHLC校验不增加成交量、ATR或ready门，也没有叠加均线或4H过滤。原V3候选、原子确认、raw上升沿和12根冷却全部保持。",
        "## 固定验收与原版本对照",
        table(gate_rows, [("gate", "固定门", None), ("threshold", "目标", None), ("value", "实际", None), ("passed", "结果", lambda x: "通过" if x else "未通过")]),
        table(pd.DataFrame(overview), [("arm", "版本", name), ("parent_signals", "原信号/公开父", integer), ("children", "公开确认升级", integer), ("distinct_labels", "同根合并公开标签", integer), ("label_drop", "公开标签减少", pct), ("recall_1", "1660正例及时召回", pct), ("large_recall_1", "947大幅行情召回", pct), ("v3_fresh_hit_retention", "原V3命中保留", pct)]),
        "固定门：公开合并标签≤6021且减少≥50%，加回unknown_original_labels后仍需≤6021且相对12042减少≥50%，因此未知不能帮主门过线；原1463及时正例至少保留1317；947大幅正例至少命中758；匹配净超额>0且六假设校正p<.01。原V1是认证历史对照，保留其原随机结果，不能冒称与本轮V3/F共用新对照。原V3观察事件仍完整保存在候选表，公开标签减少不是原始发现被抹掉。",
        "## 数据及不可移动的时钟",
        "278个固定历史OKX合约，1H、多头、不含BTC/ETH；UTC [2026-07-10,2026-09-09) 61天。837239源bar含预热；14904原标签逐字节保留，研究8046锚点=1660正例/6240负例/146未知，947大幅正例。没有新增行情或按未来涨幅换币。",
        "F接受发生在t+1实际收盘，价格为该根收盘，下一可执行入场是t+2开盘。原candidate时钟、接受时钟、原child真实时钟与公开时钟四者分别保存。t的逐根状态永远是当时的状态，最终候选表带明确裁决时间，只作研究描述，不能当t时特征。",
        "原子若在t或t+1发生，F在t+1接受时同根公开已有质量确认，保存原子真实时钟；原子若在t+2/t+3发生，在那根才升级。确认不再开仓、不重置参考。被拒绝或未知的父即使后来出现原质量确认也不复活。早子合并只是公开时序变化，不是消除了亏损开单。",
        "及时仍要求原标签锚点t到t+1出现真实F接受：原V3父如果已经晚1根，再等1根就超出及时窗口，必须算漏报。晚确认、提前旧参考和峰值覆盖不补分子。原正例是未来24根收盘先+4ATR而非-2ATR；大幅子组要求正例未来24根最高high相对锚点收盘≥8%，仅用于标签与复盘。",
        "## 候选裁决与公开标签守恒",
        table(clocks[clocks.period.isin(key_periods)], [("period", "时期", None), ("candidates_by_discovery", "按发现时钟候选", integer), ("accepted_by_discovery", "通过", integer), ("rejected_by_discovery", "拒绝", integer), ("unknown_by_discovery", "未知", integer), ("acceptances_by_publication", "按公开时钟接受", integer), ("accepted_shift_in", "跨起点流入", integer), ("accepted_shift_out", "跨终点流出", integer), ("parent_publication_drop", "父公开数量减少", pct)]),
        table(clocks[clocks.period.isin(key_periods)], [("period", "时期", None), ("raw_labels", "原合并标签", integer), ("accepted_public_labels", "F公开合并标签", integer), ("rejected_original_labels", "拒绝所属原标签", integer), ("unknown_original_labels", "未知所属原标签", integer), ("shift_out_labels", "时移流出", integer), ("shift_in_labels", "时移流入", integer), ("early_child_merges", "早子合并", integer), ("known_rejection_label_drop", "已知拒绝占原标签", pct), ("public_label_drop_excluding_unknown", "扣除未知后的减少", pct)]),
        "逐项核对：原标签−F公开标签 = 拒绝原标签 + 未知原标签 + 时移流出−流入 + 早子合并。原候选按发现时钟分期，接受/升级按真实公开时钟分期；起点前候选可在起点后接受。未知不能当假启动被成功过滤，确认合并不能当收益质量改善。原候选完整记录与公开事件共同保留，未用候选最终状态回填图表。",
        "## 三张固定完整走势图",
        "HYPE/NEAR/PEPE固定开盘北京时间8月18日00:00至21日23:00，各96根。上图原V3与原V1灰星，下图F公开接受/确认和保留的灰空心原观察；六条均线、IMACD零轴及成交量完整展示。横轴每根收盘北京时间；竖线对应用户所指根的收盘：NEAR/PEPE 19日23:00，HYPE 20日00:00。图中后续走势只用于复盘，不是检测输入。",
    ]
    event_columns = [("arm", "版本", name), ("stage", "类型", stage_name), ("time", "公开收盘", stamp), ("signal_close", "公开价", price), ("candidate_time", "原父发现", stamp), ("candidate_close", "原父价", price), ("original_child_time", "原子实际时钟", stamp), ("original_child_close", "原子价", price)]
    for image in images:
        body += ["### " + image["asset"], f"![{image['asset']}固定96根]({image['image']})",
            table(pd.DataFrame(image["signals"]), event_columns) if image["signals"] else "固定图窗没有保存事件，不补画箭头。"]
    body += ["## 分期召回与经济结果",
        "全期、前31天/后30天、固定案例夜均为已见历史描述，逐周及全部阶段见原始CSV。未匹配不等于亏损，匹配也不等于净盈利。",
        table(key_retention, [("arm", "版本", name), ("period", "时期", None), ("signals", "公开父", integer), ("distinct_labels", "合并标签", integer), ("hits_1", "及时命中", integer), ("positive_events", "正例分母", integer), ("retained_hit_events", "原命中保留", integer), ("baseline_hit_events", "原命中分母", integer), ("retention", "保留率", pct), ("large_hits_1", "大幅及时命中", integer), ("large_recall_1", "大幅召回", pct)]),
        "经济只算公开接受父，使用其实际决策位置；F不能复用原t入场结果。原执行器：下一根开盘，过去5根低点减0.2ATR与2ATR取宽，向外tick；收盘2R后启动4ATR跟踪，次根保护有效，无固定止盈。20bp往返成本及退出不改；既有缓存需源、tick、真实决策位置、截止及执行代码完全一致。",
        table(pd.concat([v1trade, trades[trades.period.eq("full")]], ignore_index=True), [("arm", "版本", name), ("valid", "有效事件", integer), ("natural_exits", "自然退出", integer), ("censored", "截尾", integer), ("win_rate", "全部净胜率", pct), ("natural_win_rate", "自然退出净胜率", pct), ("mean_gross_bp", "毛均值bp", number), ("mean_net_bp", "净均值bp", number), ("paired_actual_net_bp", "匹配事件bp", number), ("paired_random_net_bp", "随机bp", number), ("mean_excess_bp", "匹配超额bp", number), ("permutation_p", "置换p", lambda x: number(x,5)), ("holm_p_six", "六假设Holm p", lambda x: number(x,5))]),
        "共同控制：同币×UTC周×因果ATR桶最多3个，排除V3/F当前与过去12根公开父子，绝不看未来信号或收益选控制；同位置共享控制。V3原事件未变，共同匹配日程变化可能改变超额，不代表重调了V3。",
        table(trades[trades.period.isin(key_periods)], [("arm", "版本", name), ("period", "时期", None), ("valid", "有效", integer), ("matched", "有匹配", integer), ("natural_exits", "自然退出", integer), ("censored", "截尾", integer), ("natural_win_rate", "净胜率", pct), ("mean_gross_bp", "毛bp", number), ("mean_net_bp", "净bp", number), ("paired_random_net_bp", "随机bp", number), ("mean_excess_bp", "超额bp", number), ("permutation_p", "置换p", lambda x: number(x,5))]),
        "## 六项顺序探索与单特征描述",
        table(family, [("arm", "假设", name), ("raw_p", "冻结原p", lambda x:number(x,6)), ("correction_input_p", "校正输入", lambda x:number(x,6)), ("holm_p_six", "六假设Holm", lambda x:number(x,6)), ("origin", "来源", None)]),
        "A—E原p保持冻结，没有重评分；F加入Holm6。此前13项单门、反复研究同历史的选择偏差未被该局部校正完整消除。本轮第六项探索、首次使用本配置的授权holdout，仍不是盲OOS或最优参数。",
        "没有训练，训练val样本数与val AUC不适用。下表量比/TR描述AUC及按特征最高10%仅是固定事后描述，未用于调整本配置；必须连同匹配随机结果解读。",
        table(scores, [("arm", "版本", name), ("feature", "单特征", None), ("n", "样本", integer), ("descriptive_auc", "描述AUC", lambda x:number(x,4)), ("top_n", "最高10%数量", integer), ("top_gross_bp", "毛bp", number), ("top_net_bp", "净bp", number), ("top_win_rate", "净胜率", pct), ("top_random_bp", "随机bp", number), ("top_excess_bp", "超额bp", number)]),
        "## 风险与诚实声明",
        "- 下一根站稳检验针对立即跌回突破边界，但横盘里连续两根站上仍会通过，强趋势正常回踩可能被拒绝。不能把所有拒绝都称假启动，不能保证收益或未来山寨趋势覆盖。",
        "- 等待一根提高确认时延且改变真实入场价/风险输入；原父已经晚1根时，新接受会错过固定及时窗。保留原发现并不等于及时可交易接受。",
        "- 收盘确认不是收盘保证成交。[TradingView策略文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)说明默认订单在下一可用tick（通常下一根开盘）执行；process_orders_on_close属于另种配置。[barstate文档](https://www.tradingview.com/pine-script-docs/concepts/bar-states/)中isconfirmed对应实时根的closing update。本研究自定义Python次根开盘遵循该时序原则，不代表已证明原生Pine逐根一致。",
        "- 尚未移植F到Pine或验证TradingView逐根一致。本轮未部署、未改线上信号/通知/订单/ACTIVE；用户已有研究授权，未通过验收不等于未获研究授权。",
        "- 跨币同夜相关，重叠独立事件不等于账户组合；未计算账户NAV/最大回撤，不填0。20bp不能替代全部资金费/滑点/冲击，峰值R不是兑现收益。",
        "- 278个OKX多头1H与61天历史不能代表三年或全部交易所；三张图日期提前固定，不能替代全池统计。任何固定门未过均保持失败记录，不调整目标凑通过。",
        "## 证据与后续",
        "若任一门未过，不把F移植成默认线上优化。即使全部门过，也须未见数据前向验证与独立Pine时钟验证。下一项研究必须另立机制与计划，不能悄悄重跑阈值。",
        "此前研究：" + "、".join(f"[{label}]({ROOT/'analysis/html'/filename})" for label,filename in (("13单门","p1_spike_v3_gate_diagnostic_20260910.html"),("A/B","p1_spike_v3_focus_20260910.html"),("C","p1_spike_v3_confirmation_gate_20260910.html"),("D","p1_spike_v3_formation_gate_20260910_r2.html"),("E","p1_spike_v3_htf_gate_20260910.html"))) + "。",
        "原始证据：" + "、".join(f"[{label}]({OUT/filename})" for label,filename in (("原候选与裁决","candidate_registry.csv.gz"),("实际公开事件","signals.csv.gz"),("裁决与时钟分解","clock_summary.csv"),("召回","retention_summary.csv"),("经济","trade_summary.csv"),("原V1/V3/F","version_comparison.csv"),("六假设校正","structural_holm_family.csv"),("实际交易路径","trade_events.csv.gz"))) + "。",
        "独立检查：" + "、".join(f"[{filename}]({EXP/'qa'/filename})" for filename in ("preflight_review.json","independent_review.json") if (EXP/'qa'/filename).exists()) + "。",
        "### 从零复现",
        "准确计划/detector/study/测试/书面预审先提交再prepare，全部日程冻结后evaluate；报告源码也先提交。已有结果拒绝覆盖或重评分，不删除历史试验产物。缺缓存不静默换源。",
        "```bash\n.venv/bin/python -m pytest -q tests/test_spike_burst_v3_price_acceptance.py tests/test_spike_burst_v3_price_acceptance_study.py\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_price_acceptance_study prepare\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_price_acceptance_study evaluate --workers 3\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_price_acceptance_report --validation-sha " + validation_sha + "\n```",
        f"F prepared SHA256：{validation['prepared_sha']}。F validation SHA256：{validation_sha}。E缓存及历史p来源：{E_VALIDATION_SHA}。",
        "MD生成后立即用项目同一转换器转嵌图HTML，完整源/状态/图/文档SHA记录在results/price_acceptance_report_manifest.json。",
    ]
    text = "\n\n".join(body) + "\n"
    MD.write_text(text)
    from scripts.md_to_html import CSS, convert
    converter = ROOT / "scripts/md_to_html.py"
    inputs[str(converter)] = sha(converter)
    HTML.parent.mkdir(exist_ok=True)
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += "<title>SPIKE V3：下一根价格接受</title><style>" + CSS + "</style></head><body>"
    document += convert(text, asset_base=MD.parent, embed_images=True) + "</body></html>"
    HTML.write_text(document)
    for path, digest in list(inputs.items()): checked(path, digest, inputs)
    authenticate(validation_sha)
    if subprocess.check_output(["git", "show", "HEAD:"+relative],cwd=ROOT) != builder.read_bytes():
        raise ValueError("Report builder changed during rendering")
    manifest = dict(status="complete", prepared_sha256=validation["prepared_sha"], validation_sha256=validation_sha,
        prior_AB_validation_sha256=AB_VALIDATION_SHA, prior_C_validation_sha256=C_VALIDATION_SHA,
        prior_D_validation_sha256=D_VALIDATION_SHA, prior_E_validation_sha256=E_VALIDATION_SHA,
        report_builder=str(builder), report_builder_sha256=sha(builder),
        report_code_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        sources=prepared["source_pins"],inputs=[dict(path=k,sha256=v) for k,v in sorted(inputs.items())],figures=images,
        outputs=[dict(path=str(p),sha256=sha(p)) for p in (MD,HTML)],no_detection_or_rescoring=True,
        no_outcome_based_case_selection=True,no_online_changes=True,f_pine_status="not_ported_not_validated",
        hypotheses="sixth sequential exploratory hypothesis, not blind OOS")
    mp=OUT/"price_acceptance_report_manifest.json"
    mp.write_text(json.dumps(clean(manifest),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(md=str(MD),html=str(HTML),figures=3,manifest_sha256=sha(mp)),ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-sha",required=True,help="Frozen completed F validation receipt SHA256")
    build(parser.parse_args().validation_sha)
