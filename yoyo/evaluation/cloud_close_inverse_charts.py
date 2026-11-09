"""Single-panel 12H candlestick charts using the frozen market data and sample fill ledger."""
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.ticker import FuncFormatter, MaxNLocator, NullLocator
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_inverse_examples as book
from yoyo.evaluation.cloud_close_reward_paths import _ema_points_12h
from yoyo.monitor.cloud_signal_plans import stop_pct_for
from yoyo.monitor.monday_fib import week_window

HOUR = 3_600_000
STEP = HOUR * 12
BEFORE, AFTER = 64, 16
INK, GRAY = "#223343", "#5e6c79"
UP, DOWN = "#00877c", "#cf555a"
FAST, SLOW, RAIL = "#397cb2", "#ba801f", "#70578c"


def bjt(ms, fmt):
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime(fmt)


def candles(ax, frame, x):
    """One wick and one real open/close body per 12H bar; horizontal strokes for dojis."""
    colors = np.where(frame.close >= frame.open, UP, DOWN)
    ax.vlines(x, frame.low, frame.high, colors=colors, lw=.78, zorder=3)
    left, right = x - .32, x + .32
    body = np.stack([np.column_stack((left, frame.open)), np.column_stack((right, frame.open)),
                     np.column_stack((right, frame.close)), np.column_stack((left, frame.close))], axis=1)
    ax.add_collection(PolyCollection(body, facecolors=colors, edgecolors=colors, linewidths=.45, zorder=4))


def mark(ax, x, price, label, offset, color):
    dx, dy = offset
    ax.annotate(f"{label}  {price:.5g}", (x, price), xytext=(dx, dy), textcoords="offset points",
                size=10.5, color=color, ha="left" if dx > 0 else "right", va="center", zorder=7,
                arrowprops=dict(arrowstyle="-", color=color, lw=1, shrinkA=2, shrinkB=3),
                bbox=dict(boxstyle="round,pad=.28", facecolor="white", edgecolor="none", alpha=.96))
    ax.scatter(x, price, s=27, facecolor=color, edgecolor="white", lw=.8, zorder=8)


def draw(position, path, fills, source):
    entry, end = int(position.entry_ms), int(position.exit_bar_ms)
    last_bar = end // STEP * STEP
    start, last = entry - BEFORE * STEP, last_bar + AFTER * STEP
    hours, bars = book.original.parent.repair.market.frames(source)
    frame = bars.loc[start:last].copy()
    np.testing.assert_array_equal(frame.index, np.arange(start, last + STEP, STEP))
    ema = _ema_points_12h(bars.loc[:last, ["close"]].reset_index().to_dict("records")).set_index("time")
    frame = frame.join(ema[["ema20", "ema50"]])

    key = entry - STEP
    week = week_window(key)
    monday = hours.loc[week["start"]:week["end"]-1]
    assert len(monday) == 24 and week["end"] <= key < week["expires"]
    low, high = monday.low.min(), monday.high.max()
    lower, upper = low - .618*(high-low), high + .618*(high-low)
    is_long = position.side == 1
    rail = lower if is_long else upper
    assert np.isclose(rail*(1-position.side*stop_pct_for(position.symbol)), path.stop_price)
    signal = frame.loc[key]
    assert (signal.low < lower if is_long else signal.high > upper) and lower < signal.close < upper
    assert (signal.ema20 - signal.ema50) * position.side > 0

    fig, ax = plt.subplots(figsize=(12.2, 5.4), facecolor="white")
    fig.subplots_adjust(left=.029, right=.934, top=.815, bottom=.100)
    x = (frame.index.to_numpy()-start)/STEP
    candles(ax, frame, x)
    ax.plot(x, frame.ema20, color=FAST, lw=1.05, zorder=2, label="EMA20")
    ax.plot(x, frame.ema50, color=SLOW, lw=1.05, zorder=2, label="EMA50")
    ax.fill_between(x, frame.ema20, frame.ema50, color=FAST, alpha=.062, zorder=1)
    # The extension is visible only during the signal week's actual validity period.
    ax.hlines(rail, (week["end"]-start)/STEP, (week["expires"]-start)/STEP,
              lw=1.3, color=RAIL, ls="--", label=("当周 1.618 下轨" if is_long else "当周 1.618 上轨"))

    # Place fills in their native 12H candle. The order ledger resolves intrabar exits to H1.
    visible = fills[fills.action.ne("trim")].drop_duplicates(["hour_ms", "action", "fill_price"]).sort_values("hour_ms")
    assert np.isclose(fills.underlying_cash.sum(), position.cash_pnl)
    assert np.isclose(visible[visible.action.eq("entry")].fill_price.item(), path.entry_price)
    for r in visible.itertuples():
        stamp = int(r.hour_ms) // STEP * STEP
        price = float(r.fill_price)
        assert frame.at[stamp, "low"]*(1-1e-9) <= price <= frame.at[stamp, "high"]*(1+1e-9)
        if r.action == "entry":
            label, color, offset = ("开多" if is_long else "开空"), "#185a8b", (-18, -28 if is_long else 30)
        elif r.action.startswith("take_profit"):
            label = "翻倍止盈" if np.isclose(price, 2*path.entry_price) and is_long else "2R 止盈"
            color, offset = "#986311", (12, 28 if is_long else -30)
        elif r.action.startswith("stop"):
            label, color, offset = "止损", "#a23742", (14, -26 if is_long else 28)
        elif r.action.startswith("ema_flip"):
            label, color, offset = "余仓平仓", INK, (10, 28)
        else:
            raise ValueError(f"Unexpected fill action: {r.action}")
        mark(ax, (stamp-start)/STEP, price, label, offset, color)

    y_min = frame[["low","ema20","ema50"]].min().min()
    y_max = frame[["high","ema20","ema50"]].max().max()
    use_log = y_max / y_min > 8
    if use_log:
        ax.set_yscale("log")
        ticks = [v*10.**k for k in range(-6, 6) for v in (1, 2, 5) if y_min*.88 < v*10.**k < y_max*1.14]
        ax.set_yticks(ticks)
        ax.yaxis.set_minor_locator(NullLocator())
        ax.set_ylim(y_min/(y_max/y_min)**.10, y_max*(y_max/y_min)**.10)
    else:
        pad = (y_max-y_min)*.115
        ax.set_ylim(y_min-pad,y_max+pad)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y,_: f"{y:.4g}"))
    ax.yaxis.tick_right()
    ax.tick_params(length=0, colors=GRAY, labelsize=9.5, pad=6)
    ax.set_xlim(-1, len(frame))
    ticks = np.linspace(0, len(frame)-1, 7, dtype=int)
    ax.set_xticks(ticks, [bjt(frame.index[i], "%m/%d") for i in ticks])
    ax.spines[:].set_visible(False)
    ax.grid(axis="y", color="#e9edef", lw=.65)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", bbox_to_anchor=(0,1.005), borderaxespad=0, frameon=False, ncol=3,
              labelcolor=GRAY, fontsize=9.8, handlelength=1.6, columnspacing=1.4)

    direction = "多单" if is_long else "空单"
    fig.text(.030,.93,f"{position.symbol} / USDT", fontsize=20, color=INK, fontfamily="DejaVu Sans", weight="semibold")
    fig.text(.030,.881,f"OKX 永续   ·   12H   ·   {direction}   ·   {bjt(entry,'%Y')} 年",fontsize=11,color=GRAY)
    fig.text(.976,.929,f"{position.cash_pnl:+,.2f} U",ha="right",fontsize=18,color=UP if position.cash_pnl>0 else DOWN,
             fontfamily="DejaVu Sans",weight="medium")
    fig.text(.976,.881,"本笔净盈亏",ha="right",fontsize=10.5,color=GRAY)
    if use_log:
        ax.text(1.005,1.004,"对数轴",transform=ax.transAxes,fontsize=8.5,color=GRAY,va="bottom")
    fig.savefig(book.OUT/f"{position.symbol}.png",dpi=165)
    plt.close(fig)
    print(position.symbol, len(frame), int(visible.hour_ms.nunique()))


def main():
    font = "/System/Library/Fonts/Hiragino Sans GB.ttc"
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({"font.family": font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus": False})
    funded = pd.read_csv(book.BASE/"funded_inverse_1.5x.csv.gz",index_col="event_id")
    tape = pd.read_csv(book.BASE/"selected_paths.csv.gz")
    fills = pd.read_csv(book.OUT/"sample_fills.csv")
    _,_,src = book.original.source.inputs()
    for event in book.samples(funded,tape):
        position = funded.loc[event]
        path = tape[tape.event_id.eq(event) & tape.tp_r.eq(0)].iloc[0]
        draw(position, path, fills[fills.event_id.eq(event)], src[position.symbol])


if __name__ == "__main__":
    main()
