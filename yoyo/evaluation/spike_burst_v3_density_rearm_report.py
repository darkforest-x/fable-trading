"""Read-only, authenticated report fragment for the fixed G density quota.

The caller must pin the completed G validation receipt. All referenced prepared,
source, feature, state, event, historical V1 and A-F family bytes are checked
before reading them. No detector, simulator, label builder, scorer, market API
or parameter search is imported. Features and events are only rendered at their
stored decision clocks (hourly open + one hour). The fixed Aug18-21 examples
show 96 complete bars, including subsequent prices for retrospective inspection;
those subsequent bars never become new features or signal decisions here.

G rearm shading uses its saved prior12 source-window indices, known at the
rearm bar. Ownership spans are descriptive, right-censored observations, not
trades or credit for missed fresh alerts. Economics remain the frozen early
parent experiment, not the separately saved confirmation-only V4 indicator.
The fragment contains stored statistics and integrity checks, not new scoring.
Formal output refuses overwrite and requires this exact builder in HEAD first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v3-density-rearm-20260910-v1"
OUT = EXP / "results"
F_SOURCE = ROOT / "experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results"
F_PREPARED_SHA = "32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42"
F_VALIDATION_SHA = "a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570"
ORIGINAL = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2/results"
ORIGINAL_HASHES = {
    "recall_summary.csv": "1eca062ab758fb4ea86f0936bfcd459e241c80aad7a97585b2ef74f66f145056",
    "trade_summary.csv": "07762c6f1f50e2ffb03917b3304f908605f27dbef9fd69b10d20aff5138d0d02",
    "signals.csv.gz": "308bbe4d2d1f8622aba5b10ee0463d01fbebf54cb48e2aed21341f19924c80bd",
}
FIXED = dict(markets=278, source_bars=837239, source_labels=14904, anchors=8046,
    positive=1660, negative=6240, unknown=146, large_positive=947, parents=10386,
    children=3809, distinct_labels=12042, timely_positive=1463,
    timely_children=488, large_hits=798)
THRESHOLDS = dict(max_distinct_labels=6021, min_retained=1317, baseline_timely=1463,
    min_large_hits=758, large_denominator=947, min_label_drop=.5, holm_p=.01)
ARMS = ("v3", "density_rearm")
FAMILY = ("reference", "near_box", "confirmation_gate", "formation_gate",
          "htf_gate", "price_acceptance", "density_rearm")
START, END = pd.Timestamp("2026-07-10T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")
HOUR = pd.Timedelta(hours=1)
BJT = "Asia/Shanghai"
CASE_ASSETS = ("HYPE", "NEAR", "PEPE")
CASE_BEGIN = pd.Timestamp("2026-08-18T00:00", tz=BJT).tz_convert("UTC")
MA_COLUMNS = ("s20", "e20", "s60", "e60", "s120", "e120")
TABLE_NAMES = ("retention_summary", "trade_summary", "score_summary",
    "version_comparison", "rearm_summary", "structural_holm_family")
NAMES = {"v1": "原版 V1（历史随机对照）", "v3": "原 V3", "density_rearm": "G · 密集阶段单次资格"}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(2 ** 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked(path, digest, refs):
    path = Path(path).resolve()
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Expected explicit SHA256: " + str(path))
    if str(path) in refs and refs[str(path)] != digest:
        raise ValueError("Conflicting source hashes: " + str(path))
    if sha(path) != digest:
        raise ValueError("Authenticated bytes changed: " + str(path))
    refs[str(path)] = digest
    return path


def required(path, refs):
    path = Path(path).resolve()
    if str(path) not in refs:
        raise ValueError("Unpinned report input: " + str(path))
    return checked(path, refs[str(path)], refs)


def read_csv(path, refs):
    return pd.read_csv(required(path, refs), float_precision="round_trip")


def read_json(path, refs):
    return json.loads(required(path, refs).read_text())


def committed_builder():
    path = Path(__file__).resolve()
    relative = str(path.relative_to(ROOT))
    if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != path.read_bytes():
        raise ValueError("Commit this exact report builder before formal rendering")
    return dict(path=str(path), sha256=sha(path),
                commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())


def authenticate(validation_sha):
    """Authenticate the complete lineage before reading any market table."""
    refs = {}
    validation = json.loads(checked(OUT / "validation_manifest.json", validation_sha, refs).read_text())
    prepared = json.loads(checked(OUT / "prepared_manifest.json", validation["prepared_sha"], refs).read_text())
    if (prepared.get("status") != "complete" or validation.get("status") != "complete"
            or prepared["config"] != validation["config"]
            or prepared["source_pins"] != validation["source_pins"]):
        raise ValueError("Complete matching G prepare/evaluate receipts required")
    config = prepared["config"]
    if (config.get("schema") != "spike-v3-density-rearm-v1" or config.get("arms") != list(ARMS)
            or config.get("fixed_population") != FIXED or config.get("acceptance") != THRESHOLDS
            or config.get("cost_bp") != 20 or config.get("structural_hypothesis_number") != 7
            or config.get("prior_single_gate_explorations") != 13
            or config.get("holdout_per_new_config") != 1
            or config.get("production_eligible") is not False
            or config.get("training_eligible") is not False
            or validation.get("prior_F_validation_sha") != F_VALIDATION_SHA):
        raise ValueError("Wrong fixed G experiment, population, costs or lineage")
    for artifact in prepared["artifacts"] + validation["artifacts"]:
        checked(artifact["path"], artifact["sha256"], refs)
    for path, digest in prepared["sources"].items():
        checked(path, digest, refs)
    for relative, digest in prepared["source_pins"].items():
        checked(ROOT / relative, digest, refs)
    fp = json.loads(checked(F_SOURCE / "prepared_manifest.json", F_PREPARED_SHA, refs).read_text())
    fv = json.loads(checked(F_SOURCE / "validation_manifest.json", F_VALIDATION_SHA, refs).read_text())
    if (fp.get("status") != "complete" or fv.get("status") != "complete"
            or fv.get("prepared_sha") != F_PREPARED_SHA
            or fp["config"] != fv["config"] or fp["source_pins"] != fv["source_pins"]):
        raise ValueError("Frozen F schedule/economics provenance mismatch")
    for artifact in fp["artifacts"] + fv["artifacts"]:
        checked(artifact["path"], artifact["sha256"], refs)
    for path, digest in fp["sources"].items():
        checked(path, digest, refs)
    for relative, digest in fp["source_pins"].items():
        checked(ROOT / relative, digest, refs)
    if sha(OUT / "labels.csv.gz") != sha(F_SOURCE / "labels.csv.gz"):
        raise ValueError("G must preserve the exact frozen label bytes")
    for name, digest in ORIGINAL_HASHES.items():
        checked(ORIGINAL / name, digest, refs)
    return prepared, validation, refs


def clean(value):
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(clean(value), stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def one(frame, **keys):
    mask = pd.Series(True, index=frame.index)
    for key, value in keys.items():
        mask &= frame[key].eq(value)
    rows = frame.loc[mask]
    if len(rows) != 1:
        raise ValueError("Expected one table row: " + repr(keys))
    return rows.iloc[0]


def same(left, right, name):
    if pd.isna(left) and pd.isna(right):
        return
    if left != right:
        raise ValueError("Saved table mismatch: " + name)


def same_statistic(left, right, name):
    """Legacy summary copies may differ at CSV parser precision, not in events."""
    if pd.isna(left) and pd.isna(right):
        return
    if not np.isclose(float(left), float(right), rtol=1e-12, atol=1e-13, equal_nan=False):
        raise ValueError("Historical descriptive statistic differs: " + name)


def validate_holm(family, historical, trades):
    """Verify the stored seven-arm correction; never run a statistical test."""
    if (family.arm.duplicated().any() or set(family.arm) != set(FAMILY)
            or historical.arm.duplicated().any() or set(historical.arm) != set(FAMILY[:-1])):
        raise ValueError("Holm family must contain exactly frozen A-F plus G")
    for arm in FAMILY[:-1]:
        same(one(family, arm=arm).raw_p, one(historical, arm=arm).raw_p, "frozen raw p " + arm)
    g = one(trades, arm="density_rearm", period="full")
    same(one(family, arm="density_rearm").raw_p, g.permutation_p, "G raw p")
    raw = pd.to_numeric(family.raw_p).to_numpy(float)
    if np.any(np.isfinite(raw) & ((raw < 0) | (raw > 1))) or np.isinf(raw).any():
        raise ValueError("Invalid raw p value")
    inputs = np.where(np.isnan(raw), 1., raw)
    if not np.array_equal(inputs, family.correction_input_p.to_numpy(float)):
        raise ValueError("Unknown p must conservatively enter correction as one")
    order = np.argsort(inputs)
    expected = np.empty(7)
    expected[order] = np.minimum(1., np.maximum.accumulate(inputs[order] * np.arange(7, 0, -1)))
    if not np.allclose(expected, family.holm_p_seven.to_numpy(float), rtol=1e-14, atol=1e-15):
        raise ValueError("Stored Holm7 differs from its seven raw inputs")
    same(one(family, arm="density_rearm").holm_p_seven, g.holm_p_seven, "G corrected p")


def validate_tables(tables, signals, labels, baseline, assessment, historical, original_recall, original_trades):
    """Audit fixed denominators and copies, without recomputing performance."""
    r, trades = tables["retention_summary"], tables["trade_summary"]
    for key in ("parents", "children", "distinct_labels", "timely_positive", "timely_children", "large_hits", "source_bars", "markets"):
        same(baseline[key], FIXED[key], "baseline " + key)
    label_time = pd.to_datetime(labels.decision_time, utc=True)
    selected = labels[label_time.ge(START) & label_time.lt(END)]
    if labels.duplicated(["instrument", "event_i"]).any():
        raise ValueError("Duplicate original label identities")
    counts = dict(source_labels=len(labels), anchors=len(selected), positive=int(selected.label.eq("positive").sum()),
        negative=int(selected.label.eq("negative").sum()), unknown=int(selected.label.eq("unknown").sum()),
        large_positive=int((selected.label.eq("positive") & selected.large_peak.eq(True)).sum()))
    if any(value != FIXED[key] for key, value in counts.items()):
        raise ValueError("Original primary label denominator changed")
    if signals.event_id.duplicated().any() or set(signals.arm) - set(ARMS) or set(signals.stage) - {"early", "confirmed"}:
        raise ValueError("Unexpected or duplicate published event identity")
    clock = pd.to_datetime(signals.decision_time, utc=True)
    if not (clock.ge(START) & clock.lt(END)).all():
        raise ValueError("Published signal falls outside the fixed study window")
    if not (pd.to_datetime(signals.bar_open, utc=True) + HOUR).equals(clock):
        raise ValueError("Published open/close clocks differ")
    for arm in ARMS:
        events = signals[signals.arm.eq(arm)]
        union = len(events.drop_duplicates(["instrument", "decision_i"]))
        for stage in ("early", "confirmed"):
            row = one(r, arm=arm, stage=stage, period="full")
            same(row.signals, int(events.stage.eq(stage).sum()), "stored event count")
            same(row.distinct_labels, union, "same-bar union")
            same(row.positive_events, 1660, "positive denominator")
            same(row.large_positive_events, 947, "large denominator")
            same(row.same_time_kept + row.same_time_removed, row.baseline_signals, "removed event conservation")
            same(row.same_time_kept + row.new_times, row.signals, "added event conservation")
        parent = events[events.stage.eq("early")]
        if not (parent.decision_i.eq(parent.parent_i) & parent.economic_parent_i.eq(parent.parent_i)).all():
            raise ValueError("Research economics must retain original accepted-parent clock")
    b = one(r, arm="v3", stage="early", period="full")
    bc = one(r, arm="v3", stage="confirmed", period="full")
    for left, right, title in ((b.signals, 10386, "V3 parents"), (bc.signals, 3809, "V3 children"),
            (b.distinct_labels, 12042, "V3 union"), (b.hits_1, 1463, "V3 timely parents"),
            (bc.hits_1, 488, "V3 timely children"), (b.large_hits_1, 798, "V3 large hits")):
        same(left, right, title)
    for row in tables["version_comparison"].to_dict("records"):
        arm, period = row["arm"], row["period"]
        if arm not in ("v1",) + ARMS:
            raise ValueError("Only original V1, V3 and G are report benchmarks")
        rr = one(original_recall, arm="v1", period=period) if arm == "v1" else one(r, arm=arm, stage="early", period=period)
        tt = one(original_trades, arm="v1", period=period) if arm == "v1" else one(trades, arm=arm, period=period)
        for key in ("positive_events", "hits_1"):
            same(row[key], rr[key], "version " + arm + " " + key)
        for key in ("recall_1", "large_recall_1"):
            same_statistic(row[key], rr[key], "version " + arm + " " + key)
        same(row["parent_signals"], rr.signals, "version parent count")
        for key in ("natural_win_rate", "mean_net_bp", "mean_excess_bp"):
            same_statistic(row[key], tt[key], "version historical economics " + key)
    validate_holm(tables["structural_holm_family"], historical, trades)
    g = one(r, arm="density_rearm", stage="early", period="full")
    gt = one(trades, arm="density_rearm", period="full")
    tests = dict(label_drop_pass=bool(g.distinct_labels <= 6021 and g.label_drop >= .5),
        baseline_retention_pass=bool(g.retained_hit_events >= 1317), large_recall_pass=bool(g.large_hits_1 >= 758),
        economic_pass=bool(gt.mean_excess_bp > 0 and gt.holm_p_seven < .01))
    if assessment["thresholds"] != THRESHOLDS or any(assessment[key] != value for key, value in tests.items()):
        raise ValueError("Stored assessment differs from the fixed acceptance gates")
    if assessment.get("live_deployed") is not False or assessment.get("account_nav_not_computed") is not True:
        raise ValueError("Unexpected deployment/account interpretation")
    return dict(label_population=counts, fixed_baseline=baseline,
        fixed_gate_checks=tests, all_fixed_gates_pass=all(tests.values()),
        legacy_summary_comparison_tolerance=dict(relative=1e-12, absolute=1e-13,
            scope="Historical derived summary copies only; hashes, event clocks, prices and integer counts remain exact"),
        observed=dict(distinct_labels=g.distinct_labels, label_drop=g.label_drop,
            retained_original_timely=g.retained_hit_events, original_timely_denominator=1463,
            timely_large_hits=g.large_hits_1, large_denominator=947,
            mean_excess_bp=gt.mean_excess_bp, holm_p_seven=gt.holm_p_seven))


def case_features(job, refs):
    """Pin pickle bytes before parsing and preserve original source positions."""
    path = checked(job["features_path"], job["features_sha256"], refs)
    frame = pd.read_pickle(path)
    index = frame.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is None or str(index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT")
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing
            or not frame.columns.is_unique):
        raise ValueError("Invalid authenticated feature clock/schema")
    times = index.as_unit("ns").asi8
    if np.any(times % HOUR.value) or np.any(np.diff(times) != HOUR.value):
        raise ValueError("Case features must be the original continuous hourly segment")
    return frame.loc[index + HOUR <= END].copy()


def case_slice(asset, frame, state, events, original_events):
    selected = np.flatnonzero((frame.index >= CASE_BEGIN) & (frame.index < CASE_BEGIN + 96 * HOUR))
    if len(selected) != 96 or np.any(np.diff(selected) != 1):
        raise ValueError("Fixed case needs all 96 hourly bars: " + asset)
    if len(frame) != len(state) or not np.array_equal(state.decision_i.to_numpy(int), np.arange(len(frame))):
        raise ValueError("State positions differ from full feature positions")
    clocks = frame.index + HOUR
    stored_clocks = pd.DatetimeIndex(pd.to_datetime(state.decision_time, utc=True)).as_unit("ns")
    if not np.array_equal(stored_clocks.asi8, clocks.as_unit("ns").asi8):
        raise ValueError("State decision clocks differ from actual closes")
    local = events[events.decision_i.isin(selected)].copy()
    historical = original_events[original_events.decision_i.isin(selected)].copy()
    for arm in ARMS:
        for stage in ("early", "confirmed"):
            source = local[local.arm.eq(arm) & local.stage.eq(stage)]
            wanted = selected[state.iloc[selected][arm + "_" + stage].eq(True).to_numpy()]
            if not np.array_equal(np.sort(source.decision_i.to_numpy(int)), wanted):
                raise ValueError("Saved events and state markers disagree")
    for event in pd.concat([local, historical], ignore_index=True).to_dict("records"):
        i = int(event["decision_i"])
        if event["signal_close"] != float(frame.close.iloc[i]) or pd.Timestamp(event["decision_time"]) != clocks[i]:
            raise ValueError("Saved event price/clock differs from its real bar")
        if pd.Timestamp(event["bar_open"]) != frame.index[i]:
            raise ValueError("Event open clock must precede its decision by exactly one hour")
        if event["arm"] in ARMS:
            parent = int(event["parent_i"])
            if (not 0 <= i - parent <= 3 or event["parent_close"] != float(frame.close.iloc[parent])
                    or pd.Timestamp(event["parent_decision_time"]) != clocks[parent]
                    or not bool(state[event["arm"] + "_early"].iloc[parent])):
                raise ValueError("Child/parent identity is not causally anchored")
    # Shading reflects saved provenance only. Do not infer episodes from future prices.
    rearms = []
    for i in selected[state.iloc[selected].density_rearm_rearmed.eq(True).to_numpy()]:
        row = state.iloc[i]
        start, end = int(row.density_rearm_rearm_window_start_i), int(row.density_rearm_rearm_window_end_i)
        if start != i - 12 or end != i - 1 or end >= i:
            raise ValueError("Rearm shading is not its original prior12 support")
        rearms.append(dict(rearm_i=int(i), decision_time=clocks[i], source_start_i=start,
            source_end_i=end, source_start_time=clocks[start], source_end_time=clocks[end],
            research_qualification_not_signal=True))
    return selected, local, historical, rearms


def render_case(asset, frame, state, events, original_events, destination):
    """Draw all saved events, never suppress a missed/counterexample case."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle
    from matplotlib.ticker import FuncFormatter
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Refusing figure overwrite")
    selected, local, historical, rearms = case_slice(asset, frame, state, events, original_events)
    f = frame.iloc[selected]
    clocks = (f.index + HOUR).tz_convert(BJT)
    x = np.arange(96)
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(4, 1, figsize=(16, 12), sharex=True, constrained_layout=True,
        gridspec_kw={"height_ratios": [3.3, 3.3, 1.5, 1.1]})
    fig.patch.set_facecolor("white")
    colors = np.where(f.close.ge(f.open), "#138F7D", "#CB6470")
    ma_colors = ("#348D85", "#8ABBB0", "#5478B3", "#9EAFCF", "#646B74", "#B0B5BB")
    low = float(f[["low"] + list(MA_COLUMNS)].min().min())
    high = float(f[["high"] + list(MA_COLUMNS)].max().max())
    span = max(high - low, high * .001)
    pad = span * .15
    for ax, arm in zip(axes[:2], ARMS):
        if arm == "density_rearm":
            for rearm in rearms:
                left = max(-.5, rearm["source_start_i"] - selected[0] - .5)
                right = min(95.5, rearm["source_end_i"] - selected[0] + .5)
                ax.axvspan(left, right, color="#91A9D2", alpha=.075, zorder=0)
                ax.axvline(rearm["rearm_i"] - selected[0], color="#91A9D2", lw=.7, alpha=.6, ls=":", zorder=1)
        for j, bar in enumerate(f.itertuples()):
            ax.vlines(j, bar.low, bar.high, color=colors[j], lw=.8, zorder=2)
            ax.add_patch(Rectangle((j - .3, min(bar.open, bar.close)), .6,
                max(abs(bar.open - bar.close), span * .0004), facecolor=colors[j], edgecolor=colors[j], lw=.4, zorder=3))
        for key, color in zip(MA_COLUMNS, ma_colors):
            ax.plot(x, f[key], color=color, lw=.9, alpha=.85, zorder=1)
        arm_events = local[local.arm.eq(arm)]
        parents = arm_events[arm_events.stage.eq("early")]
        children = arm_events[arm_events.stage.eq("confirmed")]
        ax.scatter(parents.decision_i - selected[0], parents.signal_close,
            marker="o", s=24, color="#AF9468", alpha=.65, edgecolors="white", lw=.4, zorder=5)
        ax.scatter(children.decision_i - selected[0], children.signal_close,
            marker="^", s=75, color="#007D75", edgecolors="white", lw=.7, zorder=7)
        for rank, row in enumerate(children.sort_values("decision_i").itertuples()):
            j = int(row.decision_i - selected[0])
            ax.annotate(f"确认 {clocks[j]:%m-%d %H:%M}\n{row.signal_close:.7g} · 等待 {int(row.confirm_age)} 根",
                (j, row.signal_close), xytext=(0, 20 + rank % 2 * 26), textcoords="offset points",
                ha="center", fontsize=6.7, color="#066A64", zorder=9,
                bbox=dict(boxstyle="round,pad=.25", fc="white", ec="none", alpha=.9),
                arrowprops=dict(arrowstyle="-", color="#40958E", lw=.5))
        ax.scatter(historical.decision_i - selected[0], historical.signal_close,
            marker="*", s=96, color="#727985", edgecolors="white", lw=.6, zorder=8)
        ax.set_title(f"{NAMES[arm]}  |  研究父 {len(parents)} · 确认升级 {len(children)} · 原 V1 {len(historical)}", loc="left", fontsize=11)
        ax.set_ylim(max(low - pad, low * .7), high + pad)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.6g}"))
    axes[2].plot(x, f.md, color="#517EC2", lw=1.5, label="IMACD 主线")
    axes[2].plot(x, f.sb, color="#BD893C", lw=1.4, label="信号线")
    axes[2].axhline(0, color="#646B74", lw=.8)
    axes[2].set_title("IMACD · 原双线与零轴", loc="left", fontsize=10)
    axes[2].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.3g}"))
    axes[2].legend(loc="upper left", fontsize=8, frameon=False, ncol=2)
    axes[3].bar(x, f.volume, width=.65, color=colors, alpha=.75)
    axes[3].set_title("成交量 · 原始小时 bar", loc="left", fontsize=10)
    for ax in axes:
        ax.set_facecolor("#FCFDFE")
        ax.grid(axis="y", color="#E4E9EE", lw=.5, ls=":")
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color("#DAE1E8")
        ax.tick_params(labelsize=8, colors="#586572")
        ax.set_xlim(-1, 96)
        for hour in ("2026-08-19 23:00", "2026-08-20 00:00"):
            focus = pd.Timestamp(hour, tz=BJT)
            j = int(np.flatnonzero(clocks == focus)[0])
            ax.axvline(j, color="#8390A0", lw=.7, ls="--", alpha=.7)
    ticks = np.arange(0, 96, 8)
    axes[3].set_xticks(ticks, [clocks[j].strftime("%m-%d\n%H:%M") for j in ticks])
    axes[3].set_xlabel("北京时间 · 横轴为真实收盘时刻；图选 8/19 22:00、23:00 开盘，对应两条虚线 23:00、8/20 00:00 收盘", fontsize=9)
    handles = [Line2D([], [], marker="o", color="none", markerfacecolor="#AF9468", alpha=.65, label="研究父预警（非 V4 确认入场）", markersize=5),
        Line2D([], [], marker="^", color="none", markerfacecolor="#007D75", label="实际确认：三角与当根价", markersize=7),
        Line2D([], [], marker="*", color="none", markerfacecolor="#727985", label="原版 V1 保存信号", markersize=9),
        Patch(facecolor="#91A9D2", alpha=.14, label="G 资格恢复的前12条来源窗（非交易提示）")]
    axes[0].legend(handles=handles, loc="upper left", fontsize=7, frameon=True, framealpha=.92)
    axes[1].text(.005, .97, "浅带右侧细虚线：资格真正恢复的收盘时刻；恢复不等于触发信号", transform=axes[1].transAxes,
                 ha="left", va="top", fontsize=7, color="#778698")
    fig.suptitle(f"{asset} · OKX 1H · 固定 96 根全局回看（8/18—8/21 开盘）\n保存事件与后续走势；不回填确认、不用旧持有覆盖漏报", fontsize=13)
    fig.savefig(destination, dpi=150, facecolor="white")
    plt.close(fig)
    historical = historical.assign(stage="original")
    returned = pd.concat([local, historical], ignore_index=True).sort_values(["decision_i", "arm", "stage"])
    snapshots = []
    for text in ("2026-08-19 23:00", "2026-08-20 00:00"):
        close_time = pd.Timestamp(text, tz=BJT).tz_convert("UTC")
        i = int(np.flatnonzero(frame.index + HOUR == close_time)[0])
        snapshots.append(dict(decision_i=i, bar_open=frame.index[i], decision_time=close_time,
            bar_open_bjt=frame.index[i].tz_convert(BJT), decision_time_bjt=close_time.tz_convert(BJT),
            price_and_features=frame.iloc[i][["open", "high", "low", "close", "volume", "md", "sb"] + list(MA_COLUMNS)].to_dict(),
            saved_state=state.iloc[i].to_dict()))
    return returned, dict(asset=asset, bars=96, first_open=f.index[0], last_open=f.index[-1],
        first_close=clocks[0], last_close=clocks[-1], rearm_provenance=rearms,
        requested_open_clock_snapshots=snapshots,
        counts=[dict(arm=arm, stage=stage, events=int((returned.arm.eq(arm) & returned.stage.eq(stage)).sum()))
                for arm, stage in (("v1", "original"), ("v3", "early"), ("v3", "confirmed"),
                                   ("density_rearm", "early"), ("density_rearm", "confirmed"))],
        figure=str(destination))


def ownership_description(spans, signals):
    """Describe already saved ownership lifetimes, never economic coverage."""
    if not spans.descriptive_only.eq(True).all():
        raise ValueError("Ownership spans must explicitly remain descriptive")
    local = spans[spans.in_study.eq(True)]
    parents = signals[signals.arm.eq("density_rearm") & signals.stage.eq("early")]
    if len(local) != len(parents):
        raise ValueError("Owner spans do not conserve accepted study parents")
    rows = []
    for origin in ("bootstrap", "density_rearm"):
        group = local[local.accept_origin.eq(origin)]
        same(len(group), int(parents.parent_accept_origin.eq(origin).sum()), "owner origin count")
        rows.append(dict(origin=origin, parents=len(group), censored=int(group.censored.eq(True).sum()),
            median_observed_hours=group.observed_bars.median(), maximum_observed_hours=group.observed_bars.max(),
            end_reasons=group.span_end_reason.value_counts().to_dict(),
            descriptive_not_holding=True, no_fresh_hit_credit=True))
    if sum(row["parents"] for row in rows) != len(parents):
        raise ValueError("Unrecognized accepted-parent origin")
    return rows


def build(validation_sha):
    builder = committed_builder()
    output_paths = [OUT / "g_report_fragment.json", OUT / "g_report_manifest.json", OUT / "case_events.csv"]
    figure_dir = OUT / "g_report_figures"
    started = OUT / "g_report_started.json"
    if any(path.exists() for path in output_paths + [started, figure_dir]):
        raise ValueError("Refusing report/figure overwrite or unreviewed partial rerun")
    prepared, validation, refs = authenticate(validation_sha)
    tables = {name: read_csv(OUT / (name + ".csv"), refs) for name in TABLE_NAMES}
    signals, labels = (read_csv(OUT / (name + ".csv.gz"), refs) for name in ("signals", "labels"))
    historical = read_csv(F_SOURCE / "structural_holm_family.csv", refs)
    original_recall = read_csv(ORIGINAL / "recall_summary.csv", refs)
    original_trades = read_csv(ORIGINAL / "trade_summary.csv", refs)
    original_events = read_csv(ORIGINAL / "signals.csv.gz", refs)
    original_events = original_events[original_events.arm.eq("v1")].copy()
    baseline = read_json(OUT / "baseline_contract.json", refs)
    assessment = read_json(OUT / "assessment.json", refs)
    checks = validate_tables(tables, signals, labels, baseline, assessment, historical, original_recall, original_trades)
    # Preserve weekly original V1 evidence as well as the runner's four-period
    # cross-version table. These retain their historical control schedule.
    tables["original_v1_recall"] = original_recall[original_recall.arm.eq("v1")].copy()
    tables["original_v1_trades"] = original_trades[original_trades.arm.eq("v1")].copy()
    for name in ("retention_summary", "original_v1_recall"):
        tables[name] = tables[name].assign(missed_positive_events=tables[name].positive_events - tables[name].hits_1)
    matching = read_json(OUT / "matching.json", refs)
    jobs, state_artifacts = matching["jobs"], matching["state_artifacts"]
    if (matching["config"] != prepared["config"] or len(jobs) != 278 or len(state_artifacts) != len(jobs)
            or len({job["instrument"] for job in jobs}) != len(jobs)):
        raise ValueError("Wrong fixed market/state schedule")
    owners = ownership_description(read_csv(OUT / "structure_spans.csv.gz", refs), signals)
    write_json(started, dict(status="rendering", validation_sha256=validation_sha,
        prepared_sha256=validation["prepared_sha"], builder=builder, config=prepared["config"]))
    figure_dir.mkdir()
    cases, event_tables, figures = [], [], []
    for asset in CASE_ASSETS:
        positions = [i for i, job in enumerate(jobs) if job["asset"] == asset]
        if len(positions) != 1:
            raise ValueError("Exactly one frozen case instrument required: " + asset)
        n = positions[0]
        job = jobs[n]
        if job["minutes"] != 60 or job["venue"] != "okx":
            raise ValueError("Fixed cases are hourly OKX perpetuals")
        state_item = state_artifacts[n]
        state = read_csv(checked(state_item["path"], state_item["sha256"], refs), refs)
        frame = case_features(job, refs)
        destination = figure_dir / (asset.lower() + "_96h.png")
        rows, detail = render_case(asset, frame, state,
            signals[signals.instrument.eq(job["instrument"])],
            original_events[original_events.instrument.eq(job["instrument"])], destination)
        event_tables.append(rows)
        cases.append(detail)
        figures.append(destination)
    case_events = pd.concat(event_tables, ignore_index=True)
    for key in ("decision_time", "bar_open", "parent_decision_time", "publication_time"):
        if key in case_events:
            times = pd.to_datetime(case_events[key], utc=True)
            case_events[key + "_bjt"] = times.dt.tz_convert(BJT)
    with (OUT / "case_events.csv").open("x") as stream:
        case_events.to_csv(stream, index=False)
    notes = [
        "该 G 固定配置第 1 次使用 Owner 已授权的 holdout；同一历史池已经反复观察，不是盲样本外或最优参数结论。",
        "原始研究内 8046 锚点、1660 正例、947 大幅正例和原 V3 的 1463 个及时命中保持不变。及时只认原锚点 t..t+1 的新事件。",
        "减少提示数量不等于提高盈利胜率；独立父数、子升级数、同根合并标签数分别保留。确认不是第二笔研究交易。",
        "G 的 accepted-only 冷却独立重建，因此既可能删除原 V3 时点，也可能新增时点；不能把 G 解释为静态筛选原事件。",
        "表中净胜率/自然结束净胜率属于保存的独立父交易路径；命中率和未来障碍成功率不是该胜率。",
        "收益按原父接受后下一根开盘、原风险规则和 20bp 往返计算；不能称为已保存 V4 确认入场收益。未生成账户净值或账户最大回撤。",
        "V1 使用原历史结果与其原匹配随机；V3/G 使用本轮共同随机。V1 不冒充本轮共同控制组。",
        "七臂 Holm 校正包含全部 A-F 冻结原始 p 加 G；此外还有十三项更早探索，校正不能消除反复观察历史后的选择偏差。",
        "结构归属与恢复来源仅为因果研究定义，不是真实趋势金标。持有/归属覆盖不能替代及时新箭头，也不能替弱首发消耗资格造成的漏报开脱。",
        "原 prior12 高点与启动标签共享价格突破定义，较高召回有同源定义成分；需同时看误报、匹配超额与后续独立验证。",
        "固定三例只用于解释时序，不代表全市场。图中后续价格用于回看，信号全部来自冻结事件而非图形重算。",
        "未训练模型：val AUC 不适用；量比与 TR 表的 descriptive_auc 只是保存样本的描述性排序 AUC，不能称模型验证。",
        "未修改任何 Pine、TradingView、监控、通知、仓位、实盘或模型部署。",
    ]
    fragment = dict(schema="spike-g-density-rearm-report-fragment-v1", generated_at=pd.Timestamp.now(tz="UTC"),
        status="complete", validation_sha256=validation_sha, prepared_sha256=validation["prepared_sha"],
        builder=builder, config=prepared["config"], names=NAMES, integrity=checks,
        assessment=assessment, tables={key: value.to_dict("records") for key, value in tables.items()},
        ownership_descriptive=owners, cases=cases, case_events_path=str(OUT / "case_events.csv"),
        risks_and_honesty=notes, validation_limitations=validation.get("limitations", []),
        interpretation="Stored evidence for the report author; deployment remains false irrespective of gate results.",
        reproduction=[".venv/bin/python -m yoyo.evaluation.spike_burst_v3_density_rearm_study prepare",
            ".venv/bin/python -m yoyo.evaluation.spike_burst_v3_density_rearm_study evaluate --workers 3",
            ".venv/bin/python -m yoyo.evaluation.spike_burst_v3_density_rearm_report --validation-sha " + validation_sha],
        rerun_policy="Commands describe fresh reproduction after exact source commit; existing result directories cannot be overwritten.")
    write_json(OUT / "g_report_fragment.json", fragment)
    for path, digest in list(refs.items()):
        checked(path, digest, refs)
    if committed_builder()["sha256"] != builder["sha256"]:
        raise ValueError("Renderer source changed while building")
    products = [started, OUT / "g_report_fragment.json", OUT / "case_events.csv"] + figures
    manifest = dict(schema="spike-g-density-rearm-report-manifest-v1", status="complete",
        generated_at=pd.Timestamp.now(tz="UTC"), builder=builder, config=prepared["config"],
        source_pins=prepared["source_pins"], validation_sha256=validation_sha,
        prepared_sha256=validation["prepared_sha"], inputs=refs,
        artifacts=[dict(path=str(path.resolve()), sha256=sha(path)) for path in products],
        no_new_detection=True, no_new_scoring=True, no_parameter_changes=True,
        native_tradingview_compilation=False, online_changes=False)
    write_json(OUT / "g_report_manifest.json", manifest)
    print(json.dumps(dict(status="complete", fragment=str(OUT / "g_report_fragment.json"),
        manifest=str(OUT / "g_report_manifest.json"), manifest_sha256=sha(OUT / "g_report_manifest.json")), ensure_ascii=False))
    return manifest


def self_test():
    """Pure synthetic formatter/clock checks; never opens experiment inputs."""
    raw = np.array([.001, .02, .5, .1, .7, np.nan, .003])
    p = np.where(np.isnan(raw), 1., raw)
    order = np.argsort(p)
    adjusted = np.empty(7)
    adjusted[order] = np.minimum(1., np.maximum.accumulate(p[order] * np.arange(7, 0, -1)))
    family = pd.DataFrame(dict(arm=FAMILY, raw_p=raw, correction_input_p=p, holm_p_seven=adjusted))
    trades = pd.DataFrame([dict(arm="density_rearm", period="full", permutation_p=raw[-1], holm_p_seven=adjusted[-1])])
    validate_holm(family, family.iloc[:-1], trades)
    try:
        validate_holm(family.iloc[:-1], family.iloc[:-1], trades)
    except ValueError:
        pass
    else:
        raise AssertionError("Missing seventh hypothesis accepted")
    assert clean(dict(a=pd.NaT, b=np.nan, c=np.int64(2), d=pd.NA)) == dict(a=None, b=None, c=2, d=None)
    index = pd.date_range(CASE_BEGIN - 24 * HOUR, periods=144, freq="h")
    close = 10 + np.arange(144) * .01 + np.sin(np.arange(144) / 7) * .2
    frame = pd.DataFrame(dict(open=close - .02, high=close + .04, low=close - .05,
        close=close, volume=np.arange(144) + 100, md=np.sin(np.arange(144) / 15), sb=np.sin((np.arange(144) - 2) / 15)), index=index)
    for number, key in enumerate(MA_COLUMNS):
        frame[key] = close - .1 - .02 * number
    state = pd.DataFrame(dict(decision_i=np.arange(144), decision_time=index + HOUR))
    for arm in ARMS:
        state[arm + "_early"] = False
        state[arm + "_confirmed"] = False
        state.loc[[50, 74], arm + "_early"] = True
        state.loc[[50, 76], arm + "_confirmed"] = True
    state["density_rearm_rearmed"] = False
    state.loc[72, "density_rearm_rearmed"] = True
    state["density_rearm_rearm_window_start_i"] = np.nan
    state["density_rearm_rearm_window_end_i"] = np.nan
    state.loc[72, "density_rearm_rearm_window_start_i"] = 60
    state.loc[72, "density_rearm_rearm_window_end_i"] = 71
    events = []
    for arm in ARMS:
        for stage, indices in (("early", [50, 74]), ("confirmed", [50, 76])):
            for i in indices:
                parent = i if stage == "early" else 50 if i == 50 else 74
                events.append(dict(asset="SYNTHETIC", instrument="synthetic", arm=arm, stage=stage,
                    decision_i=i, signal_close=close[i], decision_time=index[i] + HOUR, bar_open=index[i],
                    parent_i=parent, parent_close=close[parent], parent_decision_time=index[parent] + HOUR,
                    confirm_age=i-parent, event_id=f"{arm}_{stage}_{i}"))
    events = pd.DataFrame(events)
    original = events.iloc[:1].assign(arm="v1")
    selected, local, _, rearms = case_slice("SYNTHETIC", frame, state, events, original)
    assert len(selected) == 96 and len(local) == 8 and rearms[0]["source_end_i"] == 71
    altered = events.copy()
    altered.loc[0, "decision_time"] += HOUR
    try:
        case_slice("SYNTHETIC", frame, state, altered, original)
    except ValueError:
        pass
    else:
        raise AssertionError("Incorrect event clock accepted")
    with tempfile.TemporaryDirectory(prefix="spike-g-render-synthetic-") as temporary:
        path = Path(temporary) / "synthetic.png"
        result, detail = render_case("SYNTHETIC", frame, state, events, original, path)
        assert path.stat().st_size > 10000 and len(result) == 9 and detail["bars"] == 96
        try:
            render_case("SYNTHETIC", frame, state, events, original, path)
        except ValueError:
            pass
        else:
            raise AssertionError("Existing chart overwritten")
        probe = Path(temporary) / "probe.txt"
        probe.write_text("synthetic")
        digest = sha(probe)
        refs = {}
        checked(probe, digest, refs)
        probe.write_text("changed")
        try:
            checked(probe, digest, refs)
        except ValueError:
            pass
        else:
            raise AssertionError("Mutated authenticated bytes accepted")
    print("Synthetic renderer checks passed: Holm7, missing-arm rejection, unknowns, 96 bars, same-bar/late child clocks, rearm provenance, SHA tamper and overwrite guards. No market inputs read.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validation-sha")
    group.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test() if args.self_test else build(args.validation_sha)
