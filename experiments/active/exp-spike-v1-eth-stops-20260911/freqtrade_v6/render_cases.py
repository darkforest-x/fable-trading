"""Render two V6 Freqtrade ledger trades without backfilling future protection.

Each candle is frozen OKX OHLCV.  The active-stop plan is plotted only from its
entry candle through its actual Freqtrade exit; every plotted value was known at
the preceding candle close.  The shaded post-exit bars are review context only.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DATA = ROOT / "data" / "futures"
CASES = (
    ("full_both_240m_baseline", True, "4h", "v6_4h_winner.png"),
    ("full_both_60m_baseline", False, "1h", "v6_1h_loss.png"),
)


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def candles(ax, bars: pd.DataFrame, interval: pd.Timedelta) -> None:
    """Draw OHLC bodies and wicks; date remains the candle-open timestamp."""
    width = interval.total_seconds() / 86400 * 0.64
    for bar in bars.itertuples(index=False):
        x = mdates.date2num(bar.date.to_pydatetime())
        rising = float(bar.close) >= float(bar.open)
        color = "#15803d" if rising else "#dc2626"
        ax.vlines(x, float(bar.low), float(bar.high), color=color, linewidth=0.75, zorder=2)
        lower = min(float(bar.open), float(bar.close))
        height = abs(float(bar.close) - float(bar.open))
        ax.add_patch(Rectangle((x - width / 2, lower), width, max(height, 0.01),
                               facecolor=color, edgecolor=color, linewidth=0.45, alpha=0.86, zorder=3))


def locate_signal(signals: pd.DataFrame, entry: pd.Timestamp, side: int) -> pd.Series:
    matches = signals.loc[signals.entry_time.eq(entry) & signals.side.eq(side)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one signal for {entry!s}/{side}, got {len(matches)}")
    return matches.iloc[0]


def render_case(run: str, winner: bool, timeframe: str, output: str) -> None:
    trades = pd.read_csv(RESULTS / f"{run}_trades.csv.gz", parse_dates=["open_date", "close_date"])
    trade_index = trades.profit_ratio.idxmax() if winner else trades.profit_ratio.idxmin()
    trade = trades.loc[trade_index]
    entry, exit_ = utc(trade.open_date), utc(trade.close_date)
    side = -1 if bool(trade.is_short) else 1
    minutes = 240 if timeframe == "4h" else 60
    interval = pd.Timedelta(minutes=minutes)

    signals = pd.read_csv(ROOT / f"plans/signals_both_{minutes}m_baseline.csv",
                          parse_dates=["signal_bar_open", "signal_close_time", "entry_time"])
    signal = locate_signal(signals, entry, side)
    stops = pd.read_csv(ROOT / f"plans/stops_both_{minutes}m_baseline.csv",
                        parse_dates=["entry_time", "current_time"])
    plan = stops.loc[stops.entry_time.eq(entry) & stops.side.eq(side)].sort_values("current_time").copy()
    plan["current_time"] = plan.current_time.map(utc)
    plan = plan.loc[plan.current_time.between(entry, exit_, inclusive="both")]
    if plan.empty or plan.current_time.iloc[0] != entry:
        raise ValueError(f"missing causal stop plan for {run} row {trade_index}")

    bars = pd.read_feather(DATA / f"ETH_USDT_USDT-{timeframe}-futures.feather")
    bars["date"] = pd.to_datetime(bars.date, utc=True)
    review_end = exit_ + 72 * interval
    start = entry - 100 * interval
    shown = bars.loc[bars.date.between(start, review_end, inclusive="both")].copy()
    if shown.empty:
        raise ValueError("selected OHLC window is empty")
    signal_bar = bars.loc[bars.date.eq(utc(signal.signal_bar_open))]
    if len(signal_bar) != 1:
        raise ValueError("signal bar missing from frozen OHLC")
    signal_close_price = float(signal_bar.iloc[0].close)

    fig, ax = plt.subplots(figsize=(14, 6.4), layout="constrained")
    candles(ax, shown, interval)
    # This shading is deliberately separate from any strategy line.
    ax.axvspan(mdates.date2num(exit_.to_pydatetime()), mdates.date2num(review_end.to_pydatetime()),
               facecolor="#dbeafe", alpha=0.48, label="post-exit 72 bars · review only", zorder=0)
    # These start exactly at entry and end at the actual ledger exit.
    ax.hlines(float(plan.initial_stop.iloc[0]), mdates.date2num(entry.to_pydatetime()),
              mdates.date2num(exit_.to_pydatetime()), colors="#d97706", linestyles="--", linewidth=1.25,
              label=f"initial SL {float(plan.initial_stop.iloc[0]):,.2f} · entry→exit", zorder=4)
    ax.step(plan.current_time, plan.active_stop, where="post", color="#7e22ce", linewidth=1.65,
            label="active protection · known before each bar", zorder=5)
    # Signal close and next-open entry coincide on the candle boundary by design; label both without moving time.
    ax.scatter([utc(signal.signal_close_time)], [signal_close_price], marker="D", s=42, color="#0f766e",
               edgecolors="white", linewidths=.7, label=f"signal close {signal_close_price:,.2f}", zorder=7)
    ax.scatter([entry], [float(trade.open_rate)], marker="^" if side == 1 else "v", s=70, color="#16a34a",
               edgecolors="white", linewidths=.8, label=f"actual next-open entry {float(trade.open_rate):,.2f}", zorder=8)
    ax.scatter([exit_], [float(trade.close_rate)], marker="X", s=68, color="#b91c1c", edgecolors="white",
               linewidths=.7, label=f"actual exit {float(trade.close_rate):,.2f}", zorder=8)

    direction = "LONG" if side == 1 else "SHORT"
    ledger_id = f"{run}:row-{trade_index}:{int(trade.open_timestamp)}:{direction.lower()}"
    outcome = "winner" if winner else "loss"
    ax.set_title(f"V6 Freqtrade historical {outcome} · {timeframe} · {direction}\n"
                 f"signal close {utc(signal.signal_close_time):%Y-%m-%d %H:%M UTC} → "
                 f"next-open entry {entry:%Y-%m-%d %H:%M UTC} → exit {exit_:%Y-%m-%d %H:%M UTC}")
    ax.text(0.01, 0.99,
            f"ledger id: {ledger_id}\nexit: {trade.exit_reason} · net return: {float(trade.profit_ratio):+.2%}\n"
            "Purple step is causal active protection; it does not extend before entry.",
            transform=ax.transAxes, va="top", ha="left", fontsize=8.4,
            bbox={"boxstyle": "round,pad=.35", "facecolor": "white", "edgecolor": "#94a3b8", "alpha": .9})
    ax.set_ylabel("ETH-USDT-SWAP price (USDT)")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M UTC"))
    ax.grid(axis="y", color="#cbd5e1", linewidth=.6, alpha=.7)
    ax.legend(loc="lower right", fontsize=8, ncols=2, framealpha=.93)
    ax.margins(x=.015)
    fig.savefig(RESULTS / output, dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    for item in CASES:
        render_case(*item)
