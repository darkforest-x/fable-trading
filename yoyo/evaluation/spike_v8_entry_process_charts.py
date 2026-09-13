"""Render four median-neighbor definition charts for frozen V8 entry-process labels.

Charts are explanatory audit aids, never outcome evidence.  Each selection is
an OKX, closed, split-safe V8 event closest to its label/outcome subgroup's
median net R: H1 confirmation-stale loss/profit and H2 setup loss/profit.
The figure shows the confirmation-prefix used by the label and shades the 72
bars after confirmation to make clear that later bars were not features.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import load_verified_stream
from yoyo.evaluation.spike_v8_entry_process_study import CONFIG, EXP, _repo_relative, sha256


EVIDENCE = EXP / "results/full_v1/same_entry_evidence.csv.gz"
RAW = Path(json.loads(CONFIG.read_text())["raw"])
V8_REPLAY = Path(json.loads(CONFIG.read_text())["v8_replay"]) / "streams"
CHARTS = (
    ("h1_confirmation_stale", "h1_confirmation_no_progress"),
    ("h2_failed_break_reversal", "h2_failed_break_reversal"),
)


def _committed() -> bool:
    """Keep formal chart output bound to committed source and frozen evidence."""
    rel = _repo_relative(Path(__file__))
    return (subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel}"], check=False, capture_output=True).returncode == 0
            and subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], check=False).returncode == 0)


def select_cases(events: pd.DataFrame) -> pd.DataFrame:
    """Choose median-neighbor OKX examples deterministically, never extrema."""
    source = events.loc[events.venue.eq("okx") & events.scoring_closed].copy()
    rows = []
    for label, column in CHARTS:
        for outcome, mask in (("loss", source.net_r < 0), ("profit", source.net_r > 0)):
            candidates = source.loc[mask & source[column].astype(bool)].copy()
            if candidates.empty:
                raise ValueError(f"no OKX split-safe {label} {outcome} candidate")
            median = float(candidates.net_r.median())
            chosen = candidates.assign(_distance=(candidates.net_r - median).abs()).sort_values(
                ["_distance", "trade_id"], kind="stable").iloc[0]
            rows.append({"chart_id": f"{label}_{outcome}", "label": label, "outcome": outcome,
                         "candidate_count": len(candidates), "subgroup_median_net_r": median,
                         "selection_rule": "OKX + split-safe closed + label + outcome sign; minimum absolute distance to subgroup median net R; trade_id tie-break",
                         **chosen.drop(labels=["_distance"]).to_dict()})
    return pd.DataFrame(rows)


def _plot_case(case: pd.Series, output: Path) -> None:
    """Plot OHLC, six frozen MAs and causal label points around one case."""
    context = load_verified_stream(RAW / "streams" / str(case.stream_key))
    bars = context.cache["bars"]
    confirm = pd.Timestamp(case.signal_bar_open)
    i = int(bars.index.get_loc(confirm))
    start, end = max(0, i - 48), min(len(bars), i + 73)
    view = bars.iloc[start:end].copy()
    local_time = view.index.tz_convert("Asia/Shanghai")
    confirmation_close = confirm + pd.Timedelta(minutes=context.minutes)
    confirmation_local = confirmation_close.tz_convert("Asia/Shanghai")
    trades = pd.read_csv(V8_REPLAY / f"{case.stream_key}.trades.csv.gz")
    trade_signal_open = pd.to_datetime(trades.signal_bar_open, utc=True)
    trade_side = pd.to_numeric(trades.side, errors="raise")
    trade = trades.loc[
        trades.trade_id.eq(case.trade_id)
        & trades.arm.eq("v8")
        & trade_signal_open.eq(confirm)
        & trade_side.eq(int(case.side))
    ]
    if len(trade) != 1:
        raise ValueError(
            "missing exact frozen V8 trade for chart: "
            f"trade_id={case.trade_id}, signal_bar_open={confirm}, side={case.side}"
        )
    trade = trade.iloc[0]
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    date_values = mdates.date2num(local_time.to_pydatetime())
    width = (context.minutes / 1440.0) * .68
    for x, bar in zip(date_values, view.itertuples(index=False)):
        color = "#dc2626" if bar.close >= bar.open else "#16a34a"
        ax.vlines(x, bar.low, bar.high, color=color, lw=.75, zorder=1)
        bottom, height = min(bar.open, bar.close), abs(bar.close - bar.open)
        ax.add_patch(Rectangle((x - width / 2, bottom), width, max(height, 1e-12), facecolor=color, edgecolor=color, lw=.5, zorder=2))
    for name, color in (("s20", "#2563eb"), ("e20", "#60a5fa"), ("s60", "#7c3aed"),
                        ("e60", "#a78bfa"), ("s120", "#dc2626"), ("e120", "#fca5a5")):
        ax.plot(local_time, view[name], color=color, lw=.9, label=name)
    ax.axvline(confirmation_local, color="#dc2626", ls="--", lw=1.3, label="V8确认收盘时点")
    ax.axvspan(confirmation_local, local_time[-1] + pd.Timedelta(minutes=context.minutes), color="#f3f4f6", alpha=.8,
               label="确认后：仅后验展示，不参与标签特征")
    signal_x = confirm.tz_convert("Asia/Shanghai")
    signal_close = float(bars.close.iloc[i])
    ax.scatter([signal_x], [signal_close], color="#dc2626", marker="D", s=28, zorder=5, label="V8确认K")
    if case.label.startswith("h1"):
        anchor = pd.Timestamp(case.h1_anchor_time).tz_convert("Asia/Shanghai")
        ax.axvline(anchor, color="#0891b2", ls="--", lw=1.3, label="V6形态证据K")
    else:
        q, failback = pd.Timestamp(case.h2_breakout_time).tz_convert("Asia/Shanghai"), pd.Timestamp(case.h2_failback_time).tz_convert("Asia/Shanghai")
        ax.axvline(q, color="#7c3aed", ls="--", lw=1.3, label="反向放量突破K")
        ax.axvline(failback, color="#f59e0b", ls="--", lw=1.3, label="回到原区间K")
        ax.hlines([float(case.h2_range_low), float(case.h2_range_high)], q, confirmation_local, colors="#7c3aed", linestyles=":", lw=1.0,
                  label="突破前冻结区间")
    entry_time = pd.Timestamp(trade.entry_time).tz_convert("Asia/Shanghai")
    exit_time = pd.Timestamp(trade.exit_time).tz_convert("Asia/Shanghai")
    ax.scatter([entry_time], [float(trade.entry_price)], color="#111827", marker="^", s=36, zorder=5, label="下一根开盘入场")
    sl_end = min(exit_time, local_time[-1])
    ax.hlines(float(trade.initial_stop), entry_time, sl_end, colors="#b91c1c", linestyles="-.", lw=1.0, label="原始止损")
    if local_time[0] <= exit_time <= local_time[-1] and not bool(trade.censored):
        ax.scatter([exit_time], [float(trade.exit_price)], color="#111827", marker="x", s=38, zorder=5, label="冻结退出")
    else:
        ax.text(.01, .02, "冻结退出在展示窗外或为边界censor", transform=ax.transAxes, fontsize=9, color="#374151")
    ax.set_title(f"定义示例：{case.chart_id} · {case.symbol} · {context.minutes}分钟")
    ax.set_ylabel("价格")
    ax.set_xlabel("北京时间K线开盘时间（虚线：确认收盘时点）")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=local_time.tz))
    ax.xaxis_date()
    ax.legend(loc="best", ncol=2, fontsize=8)
    fig.savefig(output, dpi=150)
    plt.close(fig)


def run(output: Path, *, official: bool = False) -> None:
    """Write a four-row selection manifest and its four definition-only PNGs."""
    if official and not _committed():
        raise ValueError("official chart output requires this exact source committed cleanly to HEAD")
    if official and EXP not in output.parents:
        raise ValueError("official chart output must remain under the experiment directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("chart output exists; use a new immutable directory")
    events = pd.read_csv(EVIDENCE)
    cases = select_cases(events)
    output.mkdir(parents=True, exist_ok=False)
    for case in cases.itertuples(index=False):
        _plot_case(pd.Series(case._asdict()), output / f"{case.chart_id}.png")
    cases.to_csv(output / "selection_manifest.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({"source": sha256(Path(__file__)), "evidence": sha256(EVIDENCE),
                                                        "selection_manifest": sha256(output / "selection_manifest.csv"),
                                                        "charts": [f"{x.chart_id}.png" for x in cases.itertuples(index=False)]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    args = parser.parse_args()
    run(args.output, official=args.official)
