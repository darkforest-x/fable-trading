"""Render three causal V1 ETH stop cases from the experiment's immutable inputs."""
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
SOURCE = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized/okx/ETH-USDT-SWAP_30m.csv.gz"
LEDGER = EXP / "results/eth_stop_sensitivity_ledger.csv.gz"
CASES = {
    "case_initial_stop_loss": "2025-01-13T00:00:00Z",
    "case_modest_trail_win": "2025-07-06T13:00:00Z",
    "case_concentrated_winner": "2026-08-18T14:00:00Z",
}


def candle(ax, frame):
    width = 0.016
    for ts, row in frame.iterrows():
        x = mdates.date2num(ts.to_pydatetime())
        color = "#18a870" if row.close >= row.open else "#d85757"
        ax.vlines(x, row.low, row.high, color=color, linewidth=.8)
        ax.add_patch(plt.Rectangle((x - width / 2, min(row.open, row.close)), width,
                                   max(abs(row.close - row.open), .05), color=color, alpha=.8))


def render(name, signal):
    raw = pd.read_csv(SOURCE, index_col=0, parse_dates=True)
    raw.index = pd.to_datetime(raw.index, utc=True)
    ledger = pd.read_csv(LEDGER, parse_dates=["signal_bar_open", "entry_time", "exit_time"])
    ledger = ledger[(ledger.strategy == "v1") & (ledger.timeframe_min == 30)]
    signal = pd.Timestamp(signal)
    rows = ledger[ledger.signal_bar_open.eq(signal) & ledger.variant.isin(["baseline", "trail_atr_3.0", "trail_atr_5.0"])]
    assert len(rows) == 3, f"missing case rows for {signal}"
    end = rows.exit_time.max() + pd.Timedelta(hours=12)
    view = raw.loc[signal - pd.Timedelta(hours=12):end]
    fig, ax = plt.subplots(figsize=(12, 5.4), constrained_layout=True)
    candle(ax, view)
    base = rows[rows.variant.eq("baseline")].iloc[0]
    ax.axhline(base.initial_stop, color="#bc3c3c", linestyle="--", label=f"initial SL {base.initial_stop:.2f}")
    colors = {"baseline": "#315bd6", "trail_atr_3.0": "#f28e2b", "trail_atr_5.0": "#8c53b5"}
    labels = {"baseline": "baseline 4 ATR trail", "trail_atr_3.0": "3 ATR trail", "trail_atr_5.0": "5 ATR trail"}
    for row in rows.itertuples():
        ax.scatter(row.entry_time, row.entry_price, marker="^", color="#222222", zorder=5)
        ax.scatter(row.exit_time, row.exit_price, marker="x", s=50, color=colors[row.variant], zorder=5,
                   label=f"{labels[row.variant]} exit {row.net_r:+.2f}R")
    ax.axvline(signal, color="#777777", linewidth=.8, linestyle=":", label="signal close")
    ax.set_title(f"ETH-USDT-SWAP 30m — {name.replace('_', ' ')}")
    ax.set_ylabel("USDT")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=mdates.UTC))
    ax.grid(alpha=.2)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(EXP / "results" / f"{name}.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    for name, signal in CASES.items():
        render(name, signal)
