"""Render the fixed 54-symbol confirmation expansion from saved ledgers only.

This presentation module never reads source OHLCV, loads a model, calls an
inference engine, evaluates trade returns, or chooses thresholds. It verifies
saved artifact hashes and reconciles the 216 shard summaries with decisions.
Charts describe counts, conditional retention, waiting and next-open price
displacement. All 54 symbols remain in the distribution, including zero-event
symbols. The prior BTC/ETH ledger is joined by event_id, not aggregate count.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1"
DATA = ROOT / "data/imacd_yolo_expanded_20260908_v1"
FOLDS = {"pre_holdout": ("2026-01-01", "2026-05-04"),
         "holdout_review": ("2026-05-04", "2026-07-01")}
FOLD_LABEL = {"pre_holdout": "1/1–5/4 · 模型验证已暴露", "holdout_review": "5/4–7/1 · 第2次复核"}
PERIODS = (60, 240)
COLORS = {"confirmed": "#168f85", "invalidated": "#c85e70",
          "expired": "#bcc4cf", "censored_end": "#d7aa54"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            result.update(chunk)
    return result.hexdigest()


def number(value, decimals=2):
    return f"{float(value):,.{decimals}f}" if value is not None and pd.notna(value) else "N/A"


def tf(minutes):
    return "1H" if int(minutes) == 60 else "4H"


def md_table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "|" + "---|"*len(headers),
                      *("| " + " | ".join(map(str, row)) + " |" for row in rows)])


def describe(frame):
    """Recompute descriptive counts independently of the inference engine."""
    full = frame.loc[frame.complete_followup.eq(True)]
    kept = full.loc[full.status.eq("confirmed")]
    delayed = kept.loc[kept.delay_bars.gt(0)]
    return dict(arrows=len(frame), complete=len(full), confirmed=len(kept),
        invalidated=int(full.status.eq("invalidated").sum()), expired=int(full.status.eq("expired").sum()),
        censored=len(frame)-len(full), immediate=int(kept.delay_bars.eq(0).sum()), delayed=len(delayed),
        confirmed_symbols=int(kept.symbol.nunique()),
        pass_rate_pct=100*len(kept)/len(full) if len(full) else None,
        delay_median_hours=float((kept.delay_bars*kept.timeframe_min/60).median()) if len(kept) else None,
        displacement_median_bp=float(kept.displacement_bp.median()) if len(kept) else None,
        delayed_displacement_median_bp=float(delayed.displacement_bp.median()) if len(delayed) else None,
        adverse_delays=int(delayed.displacement_bp.gt(0).sum()),
        favorable_delays=int(delayed.displacement_bp.lt(0).sum()))


def validate_saved(summary, decisions):
    """Check identities, finite category contract and summary count partitions."""
    symbols = summary["symbols"]
    if len(symbols) != 54 or len(set(symbols)) != 54:
        raise ValueError("expected the fixed 54-symbol universe")
    expected = {(s, m, fold) for s in symbols for m in PERIODS for fold in FOLDS}
    observed = {(r["symbol"], int(r["timeframe_min"]), r["fold"]) for r in summary["table"]}
    if observed != expected or len(summary["table"]) != len(expected):
        raise ValueError("shard table must include all 216 groups, including zeros")
    if set(summary["inputs"]) != {f"{s}_{m}_{fold}" for s, m, fold in expected}:
        raise ValueError("input receipts do not cover the 216 frozen groups")
    if decisions.event_id.duplicated().any():
        raise ValueError("duplicate event identities")
    if not decisions.status.isin(COLORS).all() or not decisions.side.isin([-1, 1]).all():
        raise ValueError("unknown status or direction")
    if not decisions.complete_followup.isin([True, False]).all():
        raise ValueError("invalid follow-up flag")
    if not decisions.complete_followup.eq(~decisions.status.eq("censored_end")).all():
        raise ValueError("censored/follow-up states disagree")
    confirmed = decisions.loc[decisions.status.eq("confirmed")]
    core_length = confirmed.core_end_i - confirmed.core_start_i + 1
    overlap = (np.minimum(confirmed.core_end_i, confirmed.signal_i)
               - np.maximum(confirmed.core_start_i, confirmed.setup_start_i) + 1)
    if (not core_length.isin([4, 5]).all() or not overlap.ge(1).all()
            or not np.isclose(overlap, confirmed.overlap_bars).all()
            or not np.isclose(overlap/core_length, confirmed.core_overlap_fraction).all()
            or not np.isclose(confirmed.core_end_i-confirmed.signal_i,
                              confirmed.core_end_from_arrow_bars).all()):
        raise ValueError("confirmed overlap descriptions do not match saved geometry")
    times = pd.to_datetime(decisions.signal_available_at, utc=True)
    known = decisions.symbol.isin(symbols) & decisions.timeframe_min.isin(PERIODS) & decisions.fold.isin(FOLDS)
    if not known.all():
        raise ValueError("decision outside frozen group universe")
    for fold, (start, end) in FOLDS.items():
        group = decisions.fold.eq(fold)
        if not times.loc[group].between(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), inclusive="left").all():
            raise ValueError("decision crosses its chronological cohort")
    for row in summary["table"]:
        group = decisions.loc[decisions.symbol.eq(row["symbol"]) &
                              decisions.timeframe_min.eq(row["timeframe_min"]) & decisions.fold.eq(row["fold"])]
        stats = describe(group)
        for name in ("arrows", "complete", "confirmed", "invalidated", "expired", "censored", "immediate", "delayed"):
            if stats[name] != row[name]:
                raise ValueError(f"summary/ledger mismatch: {row['symbol']} {name}")
        a, b = stats["pass_rate_pct"], row["pass_rate_pct"]
        if not ((a is None and b is None) or (a is not None and b is not None and np.isclose(a, b))):
            raise ValueError("retention denominator differs from complete-follow-up contract")
    return dict(shards_reconciled=len(expected), decision_identities_unique=True,
                chronological_groups_verified=True, counts_recomputed=True,
                core_overlap_geometry_recomputed=True)


def compare_prior(decisions):
    """Align the saved small-sample experiment on exact event identities."""
    prior_dir = ROOT / "data/imacd_yolo_timeframes_20260908_v1"
    summary_path = ROOT / "experiments/active/exp-imacd-yolo-timeframes-20260908-v1/results/summary.json"
    path = prior_dir / "decisions.csv"
    if not path.exists() or not summary_path.exists():
        return dict(available=False), []
    prior_summary = json.loads(summary_path.read_text())
    expected_sha = prior_summary["files"].get(str(path.relative_to(ROOT)))
    if expected_sha is None or digest(path) != expected_sha:
        raise ValueError("prior decisions differ from their frozen receipt")
    prior = pd.read_csv(path)
    current = decisions.loc[decisions.fold.eq("holdout_review") & decisions.symbol.isin(["BTC", "ETH"])]
    if prior.event_id.duplicated().any() or current.event_id.duplicated().any():
        raise ValueError("duplicate identity in prior comparison")
    old, new = prior.set_index("event_id"), current.set_index("event_id")
    common = old.index.intersection(new.index).sort_values()
    mismatches = []
    numeric = ("side", "signal_i", "setup_start_i", "confirmation_i", "delay_bars",
               "baseline_next_open", "confirmed_next_open", "displacement_bp")
    text = ("status", "model_detection_id")
    clocks = ("signal_available_at", "confirmation_available_at")
    for event_id in common:
        differences = []
        for name in (*numeric, *text, *clocks):
            a, b = old.loc[event_id, name], new.loc[event_id, name]
            if pd.isna(a) and pd.isna(b):
                continue
            if pd.isna(a) or pd.isna(b):
                same = False
            elif name in numeric:
                same = np.isclose(float(a), float(b), rtol=1e-12, atol=1e-12)
            elif name in clocks:
                same = pd.Timestamp(a) == pd.Timestamp(b)
            else:
                same = str(a) == str(b)
            if not same:
                differences.append(name)
        if differences:
            mismatches.append(dict(event_id=event_id, fields=differences))
    comparison = dict(available=True, prior_events=len(prior), current_events=len(current),
        common_events=len(common), unchanged_events=len(common)-len(mismatches), mismatches=mismatches,
        missing_prior_ids=old.index.difference(new.index).tolist(), extra_current_ids=new.index.difference(old.index).tolist(),
        prior_decisions_sha256=digest(path), prior_summary_sha256=digest(summary_path))
    rows = []
    for minutes in PERIODS:
        for label, frame in (("旧BTC/ETH", prior), ("扩展后BTC/ETH同日期", current)):
            stats = describe(frame.loc[frame.timeframe_min.eq(minutes)])
            rows.append([label, tf(minutes), stats["arrows"], stats["complete"], stats["confirmed"],
                         stats["censored"], number(stats["pass_rate_pct"])+"%"])
    return comparison, rows


def compact_review(review, windows_scored):
    """Keep audit results legible; the complete checks stay in their JSON file."""
    if "n_checks" not in review:
        return review
    names = ("n_checks", "all_passed", "n_shards", "n_events", "n_proposals",
             "n_trace_rows", "n_confirmed", "statuses", "events_outside_prior_btc_eth_cohort",
             "reused_core_confirmations", "trace_replay_coverage")
    result = {name: review[name] for name in names if name in review}
    failed = review.get("failed_checks", [])
    result.update(failed_check_count=len(failed), failed_checks=failed[:20],
                  omitted_failed_checks=max(0, len(failed)-20),
                  windows_scored_from_summary=windows_scored)
    prior = review.get("prior_btc_eth_comparison", {})
    prior_names = ("previous_events", "repeated_event_ids", "frozen_candidates_equal",
                   "traces_equal", "all_decision_fields_equal", "previous_boxes",
                   "repeated_cohort_boxes", "shared_detection_ids", "old_only_detection_ids",
                   "new_only_detection_ids", "shared_detection_confidence_changes",
                   "common_windows_with_saved_pixel_hashes", "differing_common_pixel_hashes")
    result["prior_btc_eth_comparison"] = {name: prior[name] for name in prior_names if name in prior}
    return result


def examples_markdown(summary_path, summary, decisions):
    """Verify an optional saved example manifest without rereading market data."""
    path = EXP / "results/example_manifest.json"
    heading = "## 看实际模型识别的形态\n\n"
    if not path.exists():
        return heading + "案例尚未生成，本报告不补画替代成功案例。\n", {"status": "not generated"}
    manifest = json.loads(path.read_text())
    if manifest["summary_sha256"] != digest(summary_path) or manifest["model_inference_runs"] != 0:
        raise ValueError("example manifest does not match this saved evaluation")
    kept = decisions.loc[decisions.status.eq("confirmed")].copy()
    kept["signal_available_at"] = pd.to_datetime(kept.signal_available_at, utc=True)
    kept = kept.sort_values(["signal_available_at", "event_id"])
    first = kept.groupby(["timeframe_min", "side"], sort=True).head(1)
    expected = pd.concat([first, kept.loc[kept.overlap_bars.eq(1)].head(1)]).drop_duplicates("event_id")
    expected = expected.sort_values(["signal_available_at", "event_id"])
    records = manifest["examples"]
    if [item["event_id"] for item in records] != expected.event_id.tolist():
        raise ValueError("saved example selection differs from frozen chronological rule")
    indexed = decisions.set_index("event_id", verify_integrity=True)
    section = heading + (
        "固定选例规则是1H/4H×多/空各取按原箭头时间最早的确认，另加最早一条仅重合1根的确认，去重后最多5例。"
        "不是按未来涨跌或收益挑赢家；缺少某组确认时不补选。\n\n"
        "上下文保留原参数六均线、IMACD双线和零轴；浅橙底为冻结蓄势段，橙线为原箭头，青色虚线为模型确认端点，"
        "青色区域为模型核心。每幅图只显示截至模型实际确认端点的K线，没有确认之后的走势。"
        "模型输入原图不加注释，框和箭头只画在独立audit副本。案例生成器复现原输入像素SHA，不重跑YOLO；"
        "本报告核对summary SHA、案例身份/时钟和三张PNG的文件SHA后展示。\n"
    )
    verified_files = {}
    for item in records:
        row = indexed.loc[item["event_id"]]
        minutes = int(row.timeframe_min)
        key = f"{row.symbol}_{minutes}_{row.fold}"
        if (item["symbol"] != row.symbol or int(item["timeframe_min"]) != minutes
                or int(item["side"]) != int(row.side) or item["fold"] != row.fold
                or int(item["overlap_bars"]) != int(row.overlap_bars)
                or pd.Timestamp(item["signal_available_at"]) != pd.Timestamp(row.signal_available_at)
                or pd.Timestamp(item["model_available_at"]) != pd.Timestamp(row.confirmation_available_at)
                or pd.Timestamp(item["context_last_open_at"]) + pd.Timedelta(minutes=minutes)
                    != pd.Timestamp(item["model_available_at"])
                or item["aggregate_sha256_verified"] != summary["inputs"][key]["bounded_ohlcv_sha256"]):
            raise ValueError(f"example identity or temporal receipt differs: {item['event_id']}")
        if set(item["paths"]) != {"input", "audit", "context"} or set(item["file_sha256"]) != set(item["paths"]):
            raise ValueError("example must contain exactly three hashed PNG files")
        for kind, relative in item["paths"].items():
            png = (ROOT / relative).resolve()
            if not png.is_relative_to(ROOT.resolve()) or png.suffix.lower() != ".png":
                raise ValueError("example path must be a repository-local PNG")
            if digest(png) != item["file_sha256"][kind]:
                raise ValueError(f"example PNG changed: {relative}")
            verified_files[relative] = item["file_sha256"][kind]
        section += (f"\n### {row.symbol} · {tf(minutes)} · {'多' if row.side == 1 else '空'}"
            f" · 核心重合{int(row.overlap_bars)}根\n\n"
            f"原箭头收盘UTC：{item['signal_available_at']}；模型确认UTC：{item['model_available_at']}。"
            f"此图最后一根开盘UTC：{item['context_last_open_at']}。\n\n"
            f"![截至模型确认端点的六均线与IMACD上下文](../{item['paths']['context']})\n\n"
            f"![独立模型核心审核副本](../{item['paths']['audit']})\n\n"
            f"[查看无注释模型原始输入]({ROOT / item['paths']['input']})；"
            f"生成器核对的输入像素SHA：`{item['pixel_sha256_verified']}`。\n")
    if not records:
        section += "\n本轮没有确认事件，未生成案例图片。\n"
    receipt = dict(manifest_sha256=digest(path), summary_sha256=manifest["summary_sha256"],
                   event_ids=[item["event_id"] for item in records], verified_png_files=verified_files,
                   model_inference_runs=0)
    return section, receipt


def make_charts(decisions, symbols, output_dir):
    """Three descriptive figures, selected with no success labels or returns."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "#fafbfc", "axes.facecolor": "#fafbfc"})
    ordered = sorted(symbols)
    fig, axes = plt.subplots(1, 2, figsize=(12, 13.8), sharey=True, layout="constrained")
    for ax, minutes in zip(axes, PERIODS):
        part = decisions.loc[decisions.timeframe_min.eq(minutes)]
        total = part.groupby("symbol").size().reindex(ordered, fill_value=0)
        confirmed = part.loc[part.status.eq("confirmed")].groupby("symbol").size().reindex(ordered, fill_value=0)
        y = np.arange(len(ordered))
        ax.barh(y, total, color="#dce2e9", height=.68, label="All arrows (incl. censored)")
        ax.barh(y, confirmed, color=COLORS["confirmed"], height=.68, label="Confirmed")
        for pos, (a, k) in enumerate(zip(total, confirmed)):
            ax.text(a+.4, pos, f"{k}/{a}", va="center", fontsize=7, color="#526170")
        ax.set_xlim(0, max(1, float(total.max()))*1.18+2)
        ax.set_yticks(y, ordered, fontsize=8)
        ax.set_title(f"{tf(minutes)} | all 54 symbols | confirmed / arrows", loc="left")
        ax.set_xlabel("Events, Jan 1 - Jul 1 UTC")
        ax.grid(axis="x", alpha=.16)
        ax.set_axisbelow(True)
        ax.legend(loc="lower right", fontsize=8)
    axes[0].invert_yaxis()
    paths["symbols"] = output_dir / "symbol_distribution.png"
    fig.savefig(paths["symbols"], dpi=140)
    plt.close(fig)

    months = [f"2026-{month:02d}" for month in range(1, 7)]
    month_values = pd.to_datetime(decisions.signal_available_at, utc=True).dt.strftime("%Y-%m")
    fig, axes = plt.subplots(2, 2, figsize=(12, 6.6), layout="constrained")
    for col, minutes in enumerate(PERIODS):
        for row, side in enumerate((1, -1)):
            ax = axes[row, col]
            part = decisions.loc[decisions.timeframe_min.eq(minutes) & decisions.side.eq(side)].copy()
            part["month"] = month_values.loc[part.index]
            bottom = np.zeros(6)
            for status, color in COLORS.items():
                counts = part.loc[part.status.eq(status)].groupby("month").size().reindex(months, fill_value=0)
                ax.bar(np.arange(6), counts, bottom=bottom, color=color, width=.64, label=status)
                bottom += counts.to_numpy()
            ax.set_xticks(np.arange(6), ["Jan", "Feb", "Mar", "Apr", "May", "Jun"])
            ax.set_title(f"{tf(minutes)} / {'LONG' if side == 1 else 'SHORT'}", loc="left")
            ax.set_ylabel("Arrow count")
            ax.grid(axis="y", alpha=.15)
            ax.set_axisbelow(True)
    axes[0, 0].legend(fontsize=7, ncol=2)
    paths["months"] = output_dir / "month_direction_distribution.png"
    fig.savefig(paths["months"], dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(12, 6.8), layout="constrained")
    for row, minutes in enumerate(PERIODS):
        part = decisions.loc[decisions.timeframe_min.eq(minutes) & decisions.status.eq("confirmed")]
        for fold, color, offset, label in (("pre_holdout", "#557fa9", -.18, "Jan 1 - May 4 (exposed)"),
                                           ("holdout_review", "#169b8d", .18, "May 4 - Jul 1 (review #2)")):
            kept = part.loc[part.fold.eq(fold)]
            counts = kept.groupby("delay_bars").size().reindex(range(10), fill_value=0)
            axes[row, 0].bar(np.arange(10)+offset, counts, width=.34, color=color, label=label)
            values = np.sort(kept.displacement_bp.dropna().to_numpy(float)/100)
            if len(values):
                axes[row, 1].step(values, np.arange(1, len(values)+1)/len(values), where="post",
                                  color=color, linewidth=1.6, label=f"{label}; n={len(values)}")
                axes[row, 1].scatter(values, np.arange(1, len(values)+1)/len(values), s=9, color=color)
        axes[row, 0].set_xticks(range(10), [f"{i*minutes/60:g}" for i in range(10)])
        axes[row, 0].set_title(f"{tf(minutes)} / confirmed waiting time", loc="left")
        axes[row, 0].set_xlabel("Hours after original arrow close (0 = immediate)")
        axes[row, 0].set_ylabel("Confirmed events")
        axes[row, 1].set_title(f"{tf(minutes)} / directional next-open displacement", loc="left")
        axes[row, 1].set_xlabel("Percent; positive = less favorable after waiting (NOT P&L)")
        axes[row, 1].set_ylabel("Cumulative share of confirmed events")
        axes[row, 1].axvline(0, color="#8b949e", linewidth=.8, linestyle="--")
        axes[row, 1].set_ylim(0, 1.03)
        for ax in axes[row]:
            ax.grid(axis="y", alpha=.15)
            ax.set_axisbelow(True)
        axes[row, 0].legend(fontsize=7)
        if part.empty:
            axes[row, 1].text(.5, .5, "No confirmed events", transform=axes[row, 1].transAxes, ha="center")
        else:
            axes[row, 1].legend(fontsize=7)
    paths["waiting"] = output_dir / "waiting_displacement.png"
    fig.savefig(paths["waiting"], dpi=150)
    plt.close(fig)
    return paths


def run():
    """Regenerate presentation only; require committed source and saved hashes."""
    source = str(Path(__file__).relative_to(ROOT))
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    if subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=ROOT, text=True).strip():
        raise ValueError("commit report source before generating artifacts")
    summary_path = EXP / "results/summary.json"
    summary = json.loads(summary_path.read_text())
    for relative, expected in summary["files"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError(f"saved evaluation artifact changed: {relative}")
    d = pd.read_csv(DATA / "decisions.csv")
    validation = validate_saved(summary, d)
    comparison, comparison_rows = compare_prior(d)
    examples_section, examples_receipt = examples_markdown(summary_path, summary, d)
    paths = make_charts(d, summary["symbols"], EXP / "results/report_figures")
    tables, coverage = [], []
    for fold, (start, end) in FOLDS.items():
        for minutes in PERIODS:
            part = d.loc[d.fold.eq(fold) & d.timeframe_min.eq(minutes)]
            stats = describe(part)
            tables.append([FOLD_LABEL[fold], tf(minutes), stats["arrows"], stats["complete"], stats["confirmed"],
                stats["invalidated"], stats["expired"], stats["censored"], number(stats["pass_rate_pct"])+"%",
                stats["immediate"], number(stats["delay_median_hours"])])
            inputs = [summary["inputs"][f"{symbol}_{minutes}_{fold}"] for symbol in summary["symbols"]]
            expected_bars = int((pd.Timestamp(end)-pd.Timestamp(start))/pd.Timedelta(minutes=minutes))
            no_arrow = sum(not ((part.symbol == symbol).any()) for symbol in summary["symbols"])
            coverage.append([FOLD_LABEL[fold], tf(minutes), len(inputs), no_arrow,
                sum(info["evaluation_bars"] < expected_bars for info in inputs),
                sum(info["evaluation_ready_bars"] < info["evaluation_bars"] for info in inputs),
                sum(info["evaluation_bars"] for info in inputs), sum(info["evaluation_ready_bars"] for info in inputs)])
    main_table = md_table(["原箭头时段UTC", "周期", "全部箭头", "完整随访", "确认", "失效", "到期", "截断", "确认/完整", "即时确认", "等待中位h"], tables)
    cover_table = md_table(["时段", "周期", "输入币数", "零箭头币数", "覆盖不足币数", "有未ready行情币数", "评价K线", "ready K线"], coverage)
    monthly_rows, direction_rows, price_rows, identity_rows, overlap_rows = [], [], [], [], []
    for minutes in PERIODS:
        part = d.loc[d.timeframe_min.eq(minutes)]
        for month in range(1, 7):
            group = part.loc[pd.to_datetime(part.signal_available_at, utc=True).dt.month.eq(month)]
            stats = describe(group)
            monthly_rows.append([f"2026-{month:02d}", tf(minutes), stats["arrows"], stats["complete"],
                                 stats["confirmed"], stats["censored"], number(stats["pass_rate_pct"])+"%"])
        for side in (1, -1):
            stats = describe(part.loc[part.side.eq(side)])
            direction_rows.append([tf(minutes), "多" if side == 1 else "空", stats["arrows"], stats["complete"],
                                   stats["confirmed"], number(stats["pass_rate_pct"])+"%"])
        for fold in FOLDS:
            stats = describe(part.loc[part.fold.eq(fold)])
            price_rows.append([FOLD_LABEL[fold], tf(minutes), stats["confirmed"], stats["immediate"], stats["delayed"],
                number(stats["displacement_median_bp"]), number(stats["delayed_displacement_median_bp"]),
                stats["adverse_delays"], stats["favorable_delays"]])
            confirmed = part.loc[part.fold.eq(fold) & part.status.eq("confirmed")]
            count = len(confirmed)
            one = int(confirmed.overlap_bars.eq(1).sum())
            after = int(confirmed.core_end_from_arrow_bars.gt(0).sum())
            fraction = confirmed.core_overlap_fraction
            identity_rows.append([FOLD_LABEL[fold], tf(minutes), count,
                number(100*fraction.min() if count else None)+"%",
                number(100*fraction.median() if count else None)+"%",
                f"{one}/{count} ({number(100*one/count if count else None)}%)",
                f"{after}/{count} ({number(100*after/count if count else None)}%)"])
        confirmed = part.loc[part.status.eq("confirmed")]
        overlap_rows.append([tf(minutes), len(confirmed),
            *(int(confirmed.overlap_bars.eq(value).sum()) for value in range(1, 6)),
            int(confirmed.core_end_from_arrow_bars.lt(0).sum()),
            int(confirmed.core_end_from_arrow_bars.eq(0).sum()),
            int(confirmed.core_end_from_arrow_bars.gt(0).sum())])
    symbol_rows = []
    for symbol in sorted(summary["symbols"]):
        row = [symbol]
        for minutes in PERIODS:
            stats = describe(d.loc[d.symbol.eq(symbol) & d.timeframe_min.eq(minutes)])
            row += [f"{stats['confirmed']}/{stats['complete']}/{stats['arrows']}", number(stats["pass_rate_pct"])+"%"]
        symbol_rows.append(row)
    null_rows = []
    for fold in FOLDS:
        for minutes in PERIODS:
            null = summary["direction_null"][f"{minutes}_{fold}"]
            null_rows.append([FOLD_LABEL[fold], tf(minutes), null["observed"], null["permutations"],
                number(null["null_mean"], 3), number(null["p_one_sided"], 6), number(null["p_holm"], 6)])
    validations = summary.get("validation", {})
    passed = sum(info.get("passed") is True for info in validations.values())
    checked = sum(int(info.get("confirmed_events_checked", 0)) for info in validations.values())
    failed_groups = [key for key, value in validations.items() if value.get("passed") is not True]
    review_path = EXP / "results/independent_review.json"
    review = (json.loads(review_path.read_text()) if review_path.exists() else {"status": "not yet available"})
    totals = describe(d)
    windows = sum(info.get("windows_scored", 0) for info in summary["inputs"].values())
    confirmed_symbols = d.loc[d.status.eq("confirmed"), "symbol"].nunique()
    comparison_text = (f"旧台账{comparison['prior_events']}条，扩大评估中BTC/ETH同日期{comparison['current_events']}条；"
        f"按event_id对齐{comparison['common_events']}条，其中{comparison['unchanged_events']}条指定字段一致。"
        "核对方向、原箭头与确认时钟、等待、模型框身份及两次开盘价格位移；差异与缺失清单见下方回执。"
        if comparison.get("available") else "旧台账不可用，未编造共同样本比较。")
    relative = {key: "../" + str(path.relative_to(ROOT)) for key, path in paths.items()}
    md = f"""# IMACD → YOLO 扩大检查：54币、半年、1H与4H

这轮把固定模型与同一确认规则扩展到既有54币池，按1H/4H和两段日期完成{len(summary['inputs'])}个分组，实际推理{windows:,}张条件化窗口。共{totals['arrows']:,}个原箭头，其中{totals['complete']:,}个有完整随访、{totals['censored']:,}个被分段边界截断；模型确认{totals['confirmed']:,}个，分布于{confirmed_symbols}个品种。**确认/完整随访为{number(totals['pass_rate_pct'])}%，这是候选保留率，不是真实去噪率或成功概率。**

本轮只扩大数据覆盖；模型、图像、阈值和9根等待预算没有按新结果调整。前段2026-01-01至05-04属于模型验证已暴露时段，后段05-04至07-01属于既有holdout再次复核。同一配置的holdout评估次数：1H第{summary['holdout_consumptions']['60']}次，4H第{summary['holdout_consumptions']['240']}次；不能因另起实验名重新记作首次，更不能称新盲测。

## 先看周期与时段

{main_table}

分组依据原箭头收盘时间，所有日期右开。5月4日和7月1日分别硬截断随访；候选需要完整覆盖p至p+9的确认端点以及p+10的下一开盘，否则记censored。保留率统一以完整随访候选为分母，截断仍列在全部箭头中，不把缺观察当作未确认。

1H最多等9小时，4H最多等36小时，均检查p至p+9共10个收盘端点。取第一次合法确认；md回零、反向或未知后永久失效。这里的失效、到期、确认都是程序状态，没有新增人工真假标签。

## 全54币分布：不只列出有确认的币

![各币全部箭头与模型确认数]({relative['symbols']})

灰条为全部箭头（含截断），绿色为确认；右侧数字为确认/全部，按币名字母顺序排列，保留零事件品种。图上比例与主表“确认/完整随访”分母不同，避免把截断藏起来。长条只表示候选更多，不代表该币更适合交易。

{md_table(['币种', '1H 确认/完整/全部', '1H确认率', '4H 确认/完整/全部', '4H确认率'], symbol_rows)}

## 月份和多空是否集中

![月份、方向、周期的状态分布]({relative['months']})

月份按原箭头收盘UTC归属，五月包含硬分界两侧，推断时仍使用主表两段拆分。零柱表示无该方向候选，不能单凭它证明当月完整市场覆盖。多币同一行情不等于彼此独立的证据。

{md_table(['月份UTC', '周期', '全部', '完整', '确认', '截断', '确认/完整'], monthly_rows)}

{md_table(['周期', '方向', '全部', '完整', '确认', '确认/完整'], direction_rows)}

## 等多久、等完价格变了多少

![确认等待与沿方向价格位移分布]({relative['waiting']})

左侧0小时单列即时确认；右侧为全部确认的经验累计分布，包括即时确认的零位移。沿方向位移定义为 `side × (确认后次开盘 / 原箭头后次开盘 − 1)`，正数表示等待后沿信号方向更贵，负数表示更便宜。它不是收益、实际滑点或成交保证。为免大量即时确认掩盖等待代价，另列仅延迟确认的中位数。

{md_table(['时段', '周期', '确认', '即时', '延迟', '全部位移中位bp', '仅延迟位移中位bp', '延迟更贵', '延迟更便宜'], price_rows)}

## 模型确认的还是原蓄势结构吗

这里仅读保存几何台账：重合根数是模型核心与原蓄势段加箭头根p的真实交集；重合率的分母是模型核心自身4/5根，不是整个蓄势段。当前冻结规则只要求至少1根交集，所以下表必须和保留率一起看，不能沿用BTC/ETH小样本的重合程度作为全币结论。

{md_table(['时段', '周期', '确认数', '最小核心重合率', '中位核心重合率', '仅重合1根', '核心末端晚于箭头p'], identity_rows)}

{md_table(['周期', '确认数', '重合1根', '2根', '3根', '4根', '5根', '核心末端早于p', '等于p', '晚于p'], overlap_rows)}

仅重合1根时，几何条件通过对原蓄势身份的支持很弱；核心末端晚于p表示核心延伸到了原箭头之后，可能包含启动后的新结构。这些是审核优先级描述，并未新增过滤门，也不能单凭表格判定形态正确或错误。需要逐图人工审核才能确认是否仍是用户要求的“原蓄势释放”。

{examples_section}

## 与之前BTC/ETH小样本核对

{comparison_text}

{md_table(['范围', '周期', '全部', '完整', '确认', '截断', '确认/完整'], comparison_rows) if comparison_rows else '没有可用比较台账。'}

旧报告见[BTC/ETH的1H/4H试验]({ROOT / 'analysis/html/p1_imacd_yolo_timeframes_20260908.html'})。这里不重跑旧推理，也不改写历史报告；共同事件对齐只能验证扩大任务没有悄悄改变旧事件结果，不能验证新增事件真假。

## 覆盖与零信号的区别

{cover_table}

每段每周期计划54个输入，共216个。覆盖不足表示evaluation_bars小于该完整时段理论K线数；“有未ready行情”表示已有K线中存在未满足固定指标就绪条件的部分。两者都不能记成有充分观察的无信号。既有缓存池不是历史上全部OKX在交易币种，可能存在选择、上市时间及幸存者偏差；没有按本轮收益或确认率选择这54币。

## 工程零假设与证据边界

{md_table(['时段', '周期', '真实同向确认', '置换次数', '打乱均值', '单侧p', '四项Holm p'], null_rows)}

固定原箭头方向下的md有效窗口与模型几何候选，在同币同月内打乱箭头方向；两段×两周期共四项检验使用同一家族Holm校正。这只检验条件化方向关联，不检验未来趋势、真假噪音、盈利或新增过滤器的独立价值。模型与IMACD共享价格和均线，存在机械相关；同月同币缺少方向变化时，置换本身也可能退化。

本轮没有新增金标或监督训练，正类率和val样本数不适用；确认率不是正类率。AUC、accuracy、precision/recall均为N/A。未规定经济退出和仓位合同，因此成本、TP/SL、胜率、净收益、最大回撤、top-decile毛净收益和经济匹配随机入场均为N/A，不以确认数减少代替这些指标。工程对照是全IMACD箭头及上述条件化方向打乱。

## 验证、来源和复现

报告逐一核对{len(summary['files'])}份保存产物的SHA，并将216组计数与combined decisions重新对账。冻结程序保存{len(validations)}组内置验证收据，其中{passed}组passed=true，共记录{checked}次确认事件检查；未通过或缺少passed的分组：`{failed_groups}`。这是内置回执，不冒充外部审核通过数量。以下仅列独立审核的检查总数、结果、失效覆盖与旧样本对齐摘要，窗口数明确取自运行summary。失败列表最多展示20项，完整逐项检查见[独立审核JSON]({review_path})。

```json
{json.dumps(compact_review(review, windows), ensure_ascii=False, indent=2)}
```

共同小样本对齐回执：

```json
{json.dumps(comparison, ensure_ascii=False, indent=2)}
```

推理源冻结commit：`{summary['source_commit']}`；模型权重SHA：`{summary['model_sha256']}`。模型保持原生15m研究权重的W18/W19、conf0.25、NMS0.70、imgsz1280配置，无训练或参数搜索。相同根数在1H/4H覆盖更长自然时间，不能当作模型已经适配新周期的证明。运行版本：`{json.dumps(summary.get('versions', {}), ensure_ascii=False)}`。预测API背景见[Ultralytics官方文档](https://docs.ultralytics.com/modes/predict/)。本报告生成器不加载模型、不读取原始行情，只读取保存台账。

```bash
cd /Users/zhangzc/fable-trading
# 首次/中断续跑：仅接受来源与SHA一致的已完成分片
.venv/bin/python -m yoyo.evaluation.imacd_yolo_expanded
# 独立核对保存台账，不重复推理
.venv/bin/python -m yoyo.evaluation.imacd_yolo_expanded_verify
# 可选：复现按时间选定的历史模型输入/审核图，不重跑YOLO
.venv/bin/python -m yoyo.evaluation.imacd_yolo_expanded_examples
# 重新生成展示，不再推理
.venv/bin/python -m yoyo.evaluation.imacd_yolo_expanded_report
# HTML也可从保存MD独立转换（report命令已自动执行此步）
.venv/bin/python scripts/md_to_html.py analysis/p1_imacd_yolo_expanded_20260908.md --out-dir analysis/html
```

报告源码提交后生成MD，随后立即用scripts/md_to_html.py转换HTML。模型、原始缓存、大型台账不入git，完整路径、来源与摘要保存在实验source_manifest、universe及summary中。

## 风险与诚实声明

- 更多币和更长区间提高覆盖，但共享行情、训练/验证暴露和历史池选择仍限制外推；没有新增真正独立盲样本。
- 模型确认少不等于噪音少：尚未人工审查被删除的赢家、保留的假启动或图形语义一致性。
- 4H允许等待36小时。离线可用时钟不包含Mac扫描、推理和网络通知延迟，未完成全市场在线容量或交易验收。
- 截断候选不能用于评价完整确认过程；在线系统应按当前已知数据逐步决定，不能等待未来完整随访后再回填信号。
- 没有经济退出合同，不宣称更赚钱。TradingView、spike、Mac监控、TG/Bark、ACTIVE和实盘配置保持本任务只读，training_eligible=false、production_eligible=false。

## 下一步

依据全币、月份、方向分布选择人工审核队列，按事前规则同时审核确认与未确认候选；用逐端点轨迹核对首次合法确认和首次失效。经济增益需要另行固定入场、退出、成本与评价合同，不能直接把本轮保留率作为上线门。
"""
    report = ROOT / "analysis/p1_imacd_yolo_expanded_20260908.md"
    report.write_text(md)
    subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/md_to_html.py", str(report),
                    "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    receipt = dict(summary_sha256=digest(summary_path), report_source_sha256=digest(Path(__file__)),
        report_sha256=digest(report), validation=validation, comparison=comparison,
        examples=examples_receipt,
        figures={str(path.relative_to(ROOT)): digest(path) for path in paths.values()})
    (EXP / "results/report_receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False))
    print(ROOT / "analysis/html" / report.with_suffix(".html").name)


if __name__ == "__main__":
    run()
