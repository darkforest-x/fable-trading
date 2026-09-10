"""Render authenticated frozen next-close descriptions and a report fragment.

The joined receipt SHA is mandatory. Classification was frozen before the old
labels were joined; both completed receipts, all referenced bytes and current
reviewed source hashes are authenticated before CSV parsing. Only stored
candidate/next-close prices, frozen prior12 bounds and already assigned label
membership are interpreted. No market OHLC, feature pickle, detector, simulator,
new scoring or future price series is loaded. Byte authentication of referenced
files is not interpretation of their contents.

Outputs are one three-population count figure, three fixed next-close position
figures and a JSON report fragment. Inside is a location, not a successful
retest; below is not a losing-trade label. This module must itself be committed
before rendering, and never overwrites a completed or partly started report.
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
EXP = ROOT / "experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1"
OUT = EXP / "results"
PREPARED_SHA = "32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42"
VALIDATION_SHA = "a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570"
CATEGORIES = ("above", "inside", "below", "unknown")
NAMES = dict(above="仍在原区间上方", inside="回到原区间内", below="跌到原区间下方",
             unknown="未知", not_caught="原 V3 未及时捕获")
COLORS = dict(above="#278C9B", inside="#D59B38", below="#CB6470", unknown="#8C919B", not_caught="#C5C8D0")
CASES = (("HYPE", "2026-08-19T21:00+08:00"), ("NEAR", "2026-08-19T15:00+08:00"),
         ("PEPE", "2026-08-19T21:00+08:00"))
FIXED = dict(candidates=10386, source_labels=14904, anchors=8046, positive=1660,
             large_positive=947, timely_positive=1463)
TIME_COLUMNS = ("candidate_time", "interval_source_time", "expected_decision_time", "classification_time")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""): digest.update(part)
    return digest.hexdigest()


def checked(path, digest, refs):
    path = Path(path).resolve()
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(path) != digest:
        raise ValueError("Changed authenticated report input: " + str(path))
    if str(path) in refs and refs[str(path)] != digest: raise ValueError("Conflicting input SHA")
    refs[str(path)] = digest
    return path


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)): return value.item()
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA: return None
    if isinstance(value, (Path, pd.Timestamp)): return str(value)
    return value


def write_json(path, payload):
    with Path(path).open("x") as stream:
        json.dump(clean(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def source_pin():
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit exact report builder before rendering")
    return {relative: sha(__file__)}


def authenticate(joined_sha, output=OUT):
    """Authenticate the closed two-phase receipt graph without market parsing."""
    output = Path(output).resolve()
    refs = {}
    jp = checked(output / "joined_manifest.json", joined_sha, refs)
    joined = json.loads(jp.read_text())
    cp = checked(output / "classification_manifest.json", joined["classification_sha"], refs)
    classification = json.loads(cp.read_text())
    if (joined.get("status") != "complete" or classification.get("status") != "complete"
            or joined["config"] != classification["config"]
            or joined["source_pins"] != classification["source_pins"]
            or classification.get("labels_parsed") is not False
            or classification.get("phase") != "classification_before_label_join"
            or joined["config"].get("schema") != "spike-v3-frozen-retest-description-v1"
            or joined["config"].get("denominators") != FIXED):
        raise ValueError("Classification must be frozen before the independent label join")
    for receipt in (classification, joined):
        if receipt["prepared_sha"] != PREPARED_SHA or receipt["validation_sha"] != VALIDATION_SHA:
            raise ValueError("Wrong frozen F receipt lineage")
        for item in receipt["artifacts"]: checked(item["path"], item["sha256"], refs)
        for path, digest in receipt["sources"].items(): checked(path, digest, refs)
        for relative, digest in receipt["source_pins"].items(): checked(ROOT / relative, digest, refs)
    if classification["candidate_count"] != FIXED["candidates"]:
        raise ValueError("Original candidate denominator changed")
    if joined["counts"] != {k: v for k, v in FIXED.items() if k != "candidates"}:
        raise ValueError("Original anchor denominators changed")
    for name in ("classified_candidates.csv.gz", "position_summary.csv", "f_status_crosstab.csv",
                 "fixed_examples.csv", "joined_anchors.csv.gz", "timely_positive_positions.csv"):
        if str(output / name) not in refs: raise ValueError("Unpinned required report input: " + name)
    return classification, joined, refs


def clocks(frame):
    frame = frame.copy()
    for key in TIME_COLUMNS:
        if key in frame: frame[key] = pd.to_datetime(frame[key], utc=True)
    return frame


def read_tables(output=OUT):
    """Read only position columns and existing label membership, not future OHLC."""
    output = Path(output)
    candidates = clocks(pd.read_csv(output / "classified_candidates.csv.gz", float_precision="round_trip"))
    anchors = pd.read_csv(output / "joined_anchors.csv.gz", float_precision="round_trip",
        usecols=["instrument", "event_i", "label", "large_peak", "hit_1", "timely_location", "original_signal_i"])
    stored = pd.read_csv(output / "position_summary.csv", float_precision="round_trip")
    label_stored = pd.read_csv(output / "timely_positive_positions.csv", float_precision="round_trip")
    cross = pd.read_csv(output / "f_status_crosstab.csv", float_precision="round_trip")
    cases = clocks(pd.read_csv(output / "fixed_examples.csv", float_precision="round_trip"))
    if (len(candidates) != FIXED["candidates"] or len(anchors) != FIXED["anchors"]
            or candidates.duplicated(["instrument", "candidate_i"]).any()
            or anchors.duplicated(["instrument", "event_i"]).any()
            or not candidates.location.isin(CATEGORIES).all()):
        raise ValueError("Frozen position/anchor population differs")
    position = pd.DataFrame([dict(location=key, candidates=int(candidates.location.eq(key).sum()),
        candidate_denominator=len(candidates), candidate_fraction=float(candidates.location.eq(key).sum()) / len(candidates))
        for key in CATEGORIES])
    pd.testing.assert_frame_equal(position, stored, check_dtype=False, rtol=0, atol=1e-12)
    recomputed_cross = candidates.groupby(["location", "f_status", "f_reason"], dropna=False).size().rename("candidates").reset_index()
    pd.testing.assert_frame_equal(recomputed_cross, cross, check_dtype=False)
    positive = anchors[anchors.label.eq("positive")]
    timely = positive[positive.hit_1.eq(True)]
    large = positive[positive.large_peak.eq(True)]
    if (len(positive) != FIXED["positive"] or len(timely) != FIXED["timely_positive"]
            or len(large) != FIXED["large_positive"]
            or not timely.timely_location.isin(CATEGORIES).all()
            or not anchors.loc[~anchors.hit_1.eq(True), "timely_location"].eq("not_caught").all()):
        raise ValueError("Original timely/large label membership differs")
    label_position = pd.DataFrame([dict(location=key,
        timely_positive=int(timely.timely_location.eq(key).sum()), original_timely_denominator=len(timely),
        fraction_of_original_timely=float(timely.timely_location.eq(key).sum()) / len(timely),
        large_positive_anchors=int(large.timely_location.eq(key).sum()), original_large_denominator=len(large),
        fraction_of_original_large=float(large.timely_location.eq(key).sum()) / len(large))
        for key in (*CATEGORIES, "not_caught")])
    pd.testing.assert_frame_equal(label_position, label_stored, check_dtype=False, rtol=0, atol=1e-12)
    if position.candidates.sum() != 10386 or label_position.timely_positive.sum() != 1463 or label_position.large_positive_anchors.sum() != 947:
        raise ValueError("Position partitions no longer conserve original denominators")
    if len(cases) != 3: raise ValueError("Exactly three prespecified cases required")
    selected = []
    for asset, stamp in CASES:
        match = candidates[candidates.asset.eq(asset) & candidates.candidate_time.eq(pd.Timestamp(stamp).tz_convert("UTC"))]
        if len(match) != 1: raise ValueError("Missing or ambiguous fixed case: " + asset)
        selected.append(match.iloc[0])
    selected = pd.DataFrame(selected).reset_index(drop=True)
    pd.testing.assert_frame_equal(selected, cases, check_dtype=False, rtol=0, atol=0)
    return dict(candidates=candidates, anchors=anchors, positions=position,
                label_positions=label_position, f_status=cross, cases=cases)


def setup_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def count_figure(tables, destination):
    """Three distinct populations, with unknown/missed counts explicitly kept."""
    plt = setup_plot()
    fig, axes = plt.subplots(1, 3, figsize=(16, 6), constrained_layout=True)
    specs = [(tables["positions"], "candidates", 10386, "全部原 V3 候选", CATEGORIES),
             (tables["label_positions"], "timely_positive", 1463, "原 V3 及时捕获的正例", CATEGORIES),
             (tables["label_positions"], "large_positive_anchors", 947, "全部原大幅正例", (*CATEGORIES, "not_caught"))]
    for ax, (frame, column, denominator, title, keys) in zip(axes, specs):
        values = frame.set_index("location").loc[list(keys), column].to_numpy(int)
        y = np.arange(len(keys))
        ax.barh(y, values, color=[COLORS[key] for key in keys], height=.58)
        ax.set_yticks(y, [NAMES[key] for key in keys], fontsize=10)
        ax.invert_yaxis()
        ax.set_title(title + f"\n固定分母 {denominator:,}", fontsize=13, loc="left", pad=15)
        ax.set_xlim(0, max(1, values.max()) * 1.50)
        ax.set_xlabel("数量", fontsize=10)
        ax.grid(axis="x", alpha=.12)
        ax.set_axisbelow(True)
        for j, value in enumerate(values):
            ax.text(value + max(1, values.max()) * .025, j,
                f"{value:,} · {value / denominator:.1%}", va="center", fontsize=10, color="#303946")
        for edge in ("top", "right", "left"): ax.spines[edge].set_visible(False)
        ax.spines["bottom"].set_color("#DCE0E5")
        ax.tick_params(axis="y", length=0)
    fig.suptitle("下一根收盘相对原区间的位置 · 三个分母分别展示", fontsize=17)
    fig.supxlabel("位置不等于交易盈亏；旧正例不等于盈利交易。原未捕获的大幅正例单独保留。", fontsize=10)
    fig.savefig(destination, dpi=170, facecolor="white")
    plt.close(fig)


def stamp(value):
    return "未观察到" if pd.isna(value) else pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")


def case_figure(row, destination):
    """Only two known closes and frozen levels, deliberately not a candle chart."""
    plt = setup_plot()
    from matplotlib.ticker import FuncFormatter
    fig, ax = plt.subplots(figsize=(10, 5.6), constrained_layout=True)
    low, high = float(row["frozen_prior_low"]), float(row["frozen_prior_high"])
    current, following = float(row["candidate_close"]), float(row["decision_close"])
    known = pd.notna(row["classification_time"]) and np.isfinite(following)
    valid_box = np.isfinite([low, high]).all() and 0 < low <= high
    levels = [current] + ([following] if known else []) + ([low, high] if valid_box else [])
    minimum, maximum = min(levels), max(levels)
    span = max(maximum - minimum, abs(current) * .005)
    if valid_box:
        ax.axhspan(low, high, color="#E7EBF0", alpha=.85, zorder=0)
        ax.axhline(high, color="#667382", lw=1.2, ls="--")
        ax.axhline(low, color="#667382", lw=1.2, ls="--")
        ax.text(1.08, high, f"原高 {high:.8g}", fontsize=10, color="#53606E", va="bottom")
        ax.text(1.08, low, f"原低 {low:.8g}", fontsize=10, color="#53606E", va="top")
    ax.scatter([0], [current], s=75, color="#33485F", zorder=3)
    ax.annotate(f"原候选收盘 {current:.8g}", (0, current), xytext=(0, 18),
        textcoords="offset points", ha="center", fontsize=11, color="#33485F")
    if known:
        ax.plot([0, 1], [current, following], color="#A0A8B2", lw=1.2, ls=":", zorder=1)
        ax.scatter([1], [following], s=100, color=COLORS[row["location"]], zorder=3)
        ax.annotate(f"裁决收盘 {following:.8g}", (1, following), xytext=(0, -25),
            textcoords="offset points", ha="center", fontsize=11, color=COLORS[row["location"]])
    else:
        ax.text(1, current, "下一根价格未知", ha="center", color=COLORS["unknown"], fontsize=11)
    ax.set_xticks([0, 1], ["原候选 t\n" + stamp(row["candidate_time"]),
        "实际裁决\n" + stamp(row["classification_time"])])
    ax.set_xlim(-.40, 1.65)
    ax.set_ylim(minimum - span * .30, maximum + span * .34)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.7g}"))
    ax.set_ylabel("价格")
    ax.set_xlabel("北京时间；连线只连接两次收盘，不表示盘中路径。", fontsize=10, labelpad=12)
    ax.grid(axis="y", alpha=.15)
    ax.set_title(f"{row['asset']} · {NAMES[row['location']]}\n"
        "区间固定于原候选前 12 根，未随下一根滚动", loc="left", fontsize=15, pad=20)
    for edge in ("top", "right"): ax.spines[edge].set_visible(False)
    fig.supxlabel("此图只说明位置，不判断回踩成功、后续上涨或交易盈亏。", fontsize=10)
    fig.savefig(destination, dpi=170, facecolor="white")
    plt.close(fig)


def fragment(tables, joined_sha, classification_sha, figures, inputs, output):
    """A factual JSON fragment for the owner report; no new strategy recommendation."""
    return dict(schema="spike-v3-retest-report-fragment-v1", title="原 V3 突破后下一根的位置诊断",
        scope="已有冻结 F 的描述性重读；不是新增过滤、策略评分或盲样本外验证。",
        joined_sha=joined_sha, classification_sha=classification_sha, denominators=FIXED,
        candidates=tables["positions"].to_dict("records"),
        original_label_positions=tables["label_positions"].to_dict("records"),
        f_status_crosstab=tables["f_status"].to_dict("records"), fixed_cases=tables["cases"].to_dict("records"),
        figures=figures,
        interpretation=["above/inside/below 只比较裁决收盘与原 t 冻结区间；等于任一边界属于 inside。",
            "inside 不自动等于成功回踩，below 不自动等于失败交易，unknown 不算拒绝或噪音。",
            "1463 是原 V3 及时捕获的旧正例，不能用作盈利交易分母；947 个大幅正例含原未及时捕获者。",
            "观察时刻是实际下一根收盘，不能回填成原候选当时已知的条件，也不能修补 F 的原捕获率。",
            "不同币种同一市场事件及同币滚动锚点有相关性，数量不是独立成功次数。",
            "本描述没有测量新的净收益、随机对照差、AUC 或置换 p，不能据此声称某个新规则有效。"],
        integrity_control="已提交预审中的合成阴性对照拒绝错位身份、改变冻结边界、伪造时钟、缺行和哈希篡改；只验证完整性。",
        links=dict(plan=str(EXP / "PROJECT_PLAN.md"), preflight=str(EXP / "qa/preflight_review.json"),
            classification=str(output / "classification_manifest.json"), joined=str(output / "joined_manifest.json"),
            classified_candidates=str(output / "classified_candidates.csv.gz"), joined_anchors=str(output / "joined_anchors.csv.gz"),
            original_f_report=str(ROOT / "analysis/html/p1_spike_v3_price_acceptance_20260910.html")),
        reproduction=f".venv/bin/python -m yoyo.evaluation.spike_burst_v3_retest_report --joined-sha {joined_sha}",
        input_hashes=inputs, live_deployed=False, new_filter=False, native_pine_validated=False)


def build(joined_sha, output=OUT):
    pins = source_pin()
    output = Path(output).resolve()
    if (output / "retest_report_started.json").exists() or (output / "retest_report_manifest.json").exists():
        raise ValueError("Refusing report overwrite or partial rerun")
    classification, _, refs = authenticate(joined_sha, output)
    tables = read_tables(output)
    figure_dir = output / "figures"
    paths = [figure_dir / "retest_position_counts.png"] + [figure_dir / (asset + "_next_close_position.png") for asset, _ in CASES]
    fragment_path = output / "retest_report_fragment.json"
    if fragment_path.exists() or any(path.exists() for path in paths): raise ValueError("Refusing figure/fragment overwrite")
    write_json(output / "retest_report_started.json", dict(joined_sha=joined_sha, source_pins=pins))
    figure_dir.mkdir(parents=True, exist_ok=True)
    count_figure(tables, paths[0])
    for row, path in zip(tables["cases"].to_dict("records"), paths[1:]): case_figure(row, path)
    figures = [dict(path=str(path), sha256=sha(path)) for path in paths]
    classification_sha = sha(output / "classification_manifest.json")
    write_json(fragment_path, fragment(tables, joined_sha, classification_sha, figures, refs, output))
    if source_pin() != pins: raise ValueError("Report builder changed during rendering")
    for path, digest in refs.items(): checked(path, digest, {})
    manifest = dict(status="complete", source_pins=pins, joined_sha=joined_sha, classification_sha=classification_sha,
        prepared_sha=classification["prepared_sha"], validation_sha=classification["validation_sha"], sources=refs,
        artifacts=figures + [dict(path=str(fragment_path), sha256=sha(fragment_path))],
        no_new_market_data=True, no_new_scoring=True, future_ohlc_loaded=False, new_filter=False)
    write_json(output / "retest_report_manifest.json", manifest)
    return dict(path=str(output / "retest_report_manifest.json"), sha256=sha(output / "retest_report_manifest.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joined-sha", required=True)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(build(args.joined_sha, args.output), indent=2))


if __name__ == "__main__": main()
