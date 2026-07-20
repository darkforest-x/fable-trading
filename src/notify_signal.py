"""Trade-signal Telegram alerts: TradingView-style text + annotated candle chart.

Mainline signals are long-only dense-MA / YOLO startups with TP5/SL2 barriers.
Text mimics:

    🚀 信号
    品种: ETHUSDT.P   (LONG) 🟢
    价格: 2088.75
    止盈: ...
    止损: ...

Chart overlays entry (dashed), take-profit, and stop-loss like TV.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.data.loader import list_series, load_series
from src.judgment.forward_types import SL_MULT, TP_MULT
from src.notify import send, send_photo

PROJECT_DIR = Path(__file__).resolve().parents[1]
CHART_DIR = PROJECT_DIR / "data" / "signal_charts"
LOOKBACK_BARS = 96
# No future candles in the frame — empty right margin is set via xlim (see render).
LOOKAHEAD_BARS = 0
# Right blank fraction of the full plot width (signal sits near left 2/3 edge).
RIGHT_BLANK_FRAC = 1.0 / 3.0
# Same dual-MA stack as YOLO detection charts (src/detection/render.py).
MA_PERIODS = (20, 60, 120)
# Dark-theme hex; SMA cool / EMA warm — all solid lines (owner 2026-07-17).
MA_STYLE = {
    "sma20": {"color": "#3d8fd1", "ls": "-", "lw": 1.15, "alpha": 0.95},
    "sma60": {"color": "#5cb8b0", "ls": "-", "lw": 1.05, "alpha": 0.9},
    "sma120": {"color": "#8a8aaa", "ls": "-", "lw": 1.0, "alpha": 0.85},
    "ema20": {"color": "#f06024", "ls": "-", "lw": 1.2, "alpha": 0.95},
    "ema60": {"color": "#faa03c", "ls": "-", "lw": 1.1, "alpha": 0.9},
    "ema120": {"color": "#c84696", "ls": "-", "lw": 1.05, "alpha": 0.85},
}


def add_dual_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal SMA/EMA 20/60/120 on full history (compute before window cut)."""
    out = frame.copy()
    close = out["close"].astype(float)
    for n in MA_PERIODS:
        out[f"sma{n}"] = close.rolling(n, min_periods=n).mean()
        out[f"ema{n}"] = close.ewm(span=n, adjust=False).mean()
    return out


def display_symbol(symbol: str) -> str:
    """ETH_USDT_SWAP -> ETHUSDT.P ; BTC_USDT -> BTCUSDT."""
    s = str(symbol).upper()
    if s.endswith("_USDT_SWAP"):
        return s[: -len("_USDT_SWAP")] + "USDT.P"
    if s.endswith("_USDT"):
        return s[: -len("_USDT")] + "USDT"
    return s.replace("_", "")


def signal_side(record: Mapping[str, Any]) -> str:
    """Mainline is long-only; allow explicit side override for future shorts."""
    side = str(record.get("side") or record.get("direction") or "LONG").upper()
    if side in {"S", "SHORT", "SELL"}:
        return "SHORT"
    return "LONG"


def barrier_prices(
    entry_price: float,
    atr: float,
    *,
    side: str = "LONG",
    tp_mult: float = TP_MULT,
    sl_mult: float = SL_MULT,
) -> tuple[float, float]:
    """Return (take_profit, stop_loss) absolute prices."""
    if atr <= 0 or entry_price <= 0:
        return float("nan"), float("nan")
    if side == "SHORT":
        return entry_price - tp_mult * atr, entry_price + sl_mult * atr
    return entry_price + tp_mult * atr, entry_price - sl_mult * atr


def atr_from_record(record: Mapping[str, Any]) -> float:
    """Prefer absolute ATR if present; else entry * atr_pct."""
    if record.get("atr14") is not None and np.isfinite(float(record["atr14"])):
        return float(record["atr14"])
    entry = float(record.get("entry_price") or 0)
    atr_pct = float(record.get("atr_pct") or 0)
    if entry > 0 and atr_pct > 0:
        return entry * atr_pct
    return float("nan")


def _score_cmp(score, thr) -> str:
    try:
        if score is None or thr is None:
            return "/"
        return "≥" if float(score) >= float(thr) else "<"
    except (TypeError, ValueError):
        return "/"


def _score_star_count(score: Any, thr: Any, *, max_stars: int = 5) -> int:
    """Map model score → star count (0..max_stars). Higher score = more stars.

    When a threshold is present (mainline / micro), stars scale by score/thr:
      < thr          → 0  (scout / miss — no strength claim)
      [1.00, 1.10)   → 1  barely above gate
      [1.10, 1.25)   → 2
      [1.25, 1.50)   → 3
      [1.50, 2.00)   → 4
      ≥ 2.00         → 5  standout
    Without thr, fall back to absolute regression-score bins (realized_ret scale).
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0
    if not np.isfinite(s):
        return 0
    try:
        t = float(thr) if thr is not None else float("nan")
    except (TypeError, ValueError):
        t = float("nan")
    if np.isfinite(t) and t > 0:
        r = s / t
        if r < 1.0:
            n = 0
        elif r < 1.10:
            n = 1
        elif r < 1.25:
            n = 2
        elif r < 1.50:
            n = 3
        elif r < 2.00:
            n = 4
        else:
            n = 5
    else:
        # absolute fallback for captions that omit threshold
        if s < 0.015:
            n = 0
        elif s < 0.022:
            n = 1
        elif s < 0.028:
            n = 2
        elif s < 0.035:
            n = 3
        elif s < 0.050:
            n = 4
        else:
            n = 5
    return max(0, min(int(max_stars), n))


def _score_stars(score: Any, thr: Any, *, max_stars: int = 5) -> str:
    """e.g. '★★★☆☆' — empty string if score unusable."""
    try:
        s = float(score)
        if not np.isfinite(s):
            return ""
    except (TypeError, ValueError):
        return ""
    n = _score_star_count(score, thr, max_stars=max_stars)
    return "★" * n + "☆" * (max_stars - n)


def format_signal_caption(record: Mapping[str, Any]) -> str:
    """HTML caption for Telegram (photo or message)."""
    side = signal_side(record)
    side_emoji = "🔴" if side == "SHORT" else "🟢"
    symbol = display_symbol(str(record.get("symbol", "?")))
    entry = float(record.get("entry_price") or 0)
    atr = atr_from_record(record)
    tp, sl = barrier_prices(entry, atr, side=side)
    score = record.get("score")
    score_s = f"{float(score):.4f}" if score is not None and np.isfinite(float(score)) else "—"
    thr = record.get("threshold")
    thr_s = f"{float(thr):.4f}" if thr is not None and np.isfinite(float(thr)) else "—"
    stars = _score_stars(score, thr)
    from src.timefmt import format_beijing

    entry_time = format_beijing(
        record.get("entry_time") or record.get("signal_time"),
        with_seconds=False,
        with_label=True,
        fallback=str(record.get("entry_time") or record.get("signal_time") or "—")[:19],
    )
    status = str(record.get("status") or "open")

    def px(x: float) -> str:
        if not np.isfinite(x):
            return "—"
        if x >= 100:
            return f"{x:.2f}"
        if x >= 1:
            return f"{x:.4f}"
        return f"{x:.6f}"

    bar = record.get("bar") or record.get("channel") or ""
    bar_line = f"周期: <b>{bar}</b>" if bar else ""
    ch = record.get("channel")
    title = "🚀 <b>信号</b>"
    if ch == "eth_micro" or (isinstance(bar, str) and bar in {"1m", "2m", "3m", "5m"}):
        title = "⚡ <b>ETH Micro 信号</b>"
    elif ch == "scout":
        # Right-edge detection, NOT a threshold-passed trade signal: same layout
        # so the owner reads one format, but the title/status must not let a
        # forming cluster be mistaken for an executable entry.
        title = "👁 <b>视觉侦察</b>（右缘密集）"
        bar_line = ""
    # Put stars on the title when score clears the gate (n≥1) for glanceability.
    n_stars = _score_star_count(score, thr)
    if n_stars > 0 and stars:
        title = f"{title}  {stars}"
    lines = [
        title,
        f"品种: <b>{symbol}</b>   ({side}) {side_emoji}",
    ]
    if bar_line:
        lines.append(bar_line)
    score_line = f"分数: {score_s}  {_score_cmp(score, thr)} 阈值 {thr_s}"
    if stars:
        score_line = f"{score_line}  {stars}"
    lines += [
        f"价格: <b>{px(entry)}</b>",
        f"止盈: <b>{px(tp)}</b>  <i>(TP {TP_MULT:g}×ATR)</i>",
        f"止损: <b>{px(sl)}</b>  <i>(SL {SL_MULT:g}×ATR)</i>",
        f"时间: {entry_time}",
        # honest comparator: a scout hit below threshold must not read as "≥"
        score_line,
        f"状态: {status}",
    ]
    lag = record.get("lag_min")
    if lag is not None:
        try:
            lag_f = float(lag)
            tag = "tip✓" if lag_f <= 20 else "事后"
            lines.append(f"检出延迟: <b>{lag_f:.0f}m</b> ({tag})")
        except (TypeError, ValueError):
            pass
    return "\n".join(lines)


def _load_ohlc(source: str, symbol: str) -> pd.DataFrame | None:
    groups = list_series(bar="15m")
    paths = groups.get((source, symbol))
    if not paths:
        # try any matching symbol
        for (src, sym), ps in groups.items():
            if sym == symbol:
                paths = ps
                source = src
                break
    if not paths:
        return None
    frame = load_series(paths)
    if frame.empty:
        return None
    return frame


def render_signal_chart(
    record: Mapping[str, Any],
    *,
    out_path: Path | None = None,
    lookback: int = LOOKBACK_BARS,
    lookahead: int = LOOKAHEAD_BARS,
    frame: pd.DataFrame | None = None,
) -> Path | None:
    """Draw candles + SMA/EMA (solid) + entry/TP/SL. Returns PNG path or None.

    Right ~1/3 of the plot is empty margin so the signal is not jammed against
    the right edge (owner 2026-07-17). `frame` lets a caller with fresh data
    bypass the disk cache.
    """
    source = str(record.get("source") or "okx")
    symbol = str(record.get("symbol") or "")
    if frame is None:
        frame = _load_ohlc(source, symbol)
    if frame is None or frame.empty:
        print(f"tg_signal: no kline for {source}/{symbol}")
        return None

    side = signal_side(record)
    entry_price = float(record.get("entry_price") or 0)
    signal_i = int(record.get("signal_i") or -1)
    entry_i = signal_i + 1 if signal_i >= 0 else None

    # locate entry by time if signal_i missing/out of range
    if entry_i is None or entry_i < 0 or entry_i >= len(frame):
        et = pd.Timestamp(record.get("entry_time") or record.get("signal_time"))
        if et.tzinfo is None:
            et = et.tz_localize("UTC")
        else:
            et = et.tz_convert("UTC")
        times = pd.to_datetime(frame["open_time"], utc=True)
        hits = np.where(times == et)[0]
        if len(hits) == 0:
            # nearest
            hits = np.where(times <= et)[0]
        entry_i = int(hits[-1]) if len(hits) else len(frame) - 1

    atr = atr_from_record(record)
    if not np.isfinite(atr) or atr <= 0:
        # fall back to series atr if present
        if "atr14" in frame.columns:
            atr = float(frame["atr14"].iloc[max(0, entry_i - 1)])
        else:
            # rough ATR from recent ranges
            window = frame.iloc[max(0, entry_i - 14) : entry_i + 1]
            atr = float((window["high"] - window["low"]).mean())
    tp, sl = barrier_prices(entry_price, atr, side=side)
    if not np.isfinite(entry_price) or entry_price <= 0:
        entry_price = float(frame["open"].iloc[entry_i])

    # Dual MA on full series so SMA120 has real warmup, then cut window.
    # Window ends at the entry bar — no future candles; blank margin is pure xlim pad.
    frame_ma = add_dual_mas(frame)
    start = max(0, entry_i - lookback)
    end = min(len(frame_ma), entry_i + max(0, int(lookahead)) + 1)
    sub = frame_ma.iloc[start:end].reset_index(drop=True)
    entry_pos = entry_i - start

    times = pd.to_datetime(sub["open_time"], utc=True)
    x = mdates.date2num(times.to_numpy(dtype="datetime64[ns]"))
    opens = sub["open"].to_numpy(dtype=float)
    highs = sub["high"].to_numpy(dtype=float)
    lows = sub["low"].to_numpy(dtype=float)
    closes = sub["close"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(11.2, 5.8), dpi=140)
    fig.patch.set_facecolor("#0e1116")
    ax.set_facecolor("#0e1116")

    width = 0.7 * (x[1] - x[0]) if len(x) > 1 else 0.01
    for i in range(len(sub)):
        up = closes[i] >= opens[i]
        color = "#26a69a" if up else "#ef5350"
        ax.vlines(x[i], lows[i], highs[i], color=color, linewidth=1.0, zorder=2)
        body_low, body_high = sorted((opens[i], closes[i]))
        ax.add_patch(
            plt.Rectangle(
                (x[i] - width / 2, body_low),
                width,
                max(body_high - body_low, 1e-12 * max(entry_price, 1)),
                facecolor=color,
                edgecolor=color,
                linewidth=0.6,
                zorder=3,
            )
        )

    # SMA + EMA all solid (20/60/120).
    ma_handles = []
    for name, style in MA_STYLE.items():
        if name not in sub.columns:
            continue
        y = sub[name].to_numpy(dtype=float)
        mask = np.isfinite(y)
        if not mask.any():
            continue
        (line,) = ax.plot(
            x[mask],
            y[mask],
            color=style["color"],
            linestyle="-",  # solid — never dashed
            linewidth=style["lw"],
            alpha=style["alpha"],
            zorder=3.5,
            label=name.upper(),
        )
        ma_handles.append(line)

    if ma_handles:
        leg = ax.legend(
            handles=ma_handles,
            loc="upper left",
            fontsize=7.5,
            framealpha=0.72,
            facecolor="#161b22",
            edgecolor="#30363d",
            labelcolor="#c9d1d9",
            ncol=2,
            borderpad=0.4,
            handlelength=2.2,
        )
        leg.set_zorder(7)

    # TradingView-like levels
    def level(y: float, color: str, label: str, ls: str = "--", lw: float = 1.4) -> None:
        if not np.isfinite(y):
            return
        ax.axhline(y, color=color, linestyle=ls, linewidth=lw, alpha=0.95, zorder=4)
        ax.text(
            0.995,
            y,
            f" {label} {y:.4g}",
            transform=ax.get_yaxis_transform(),
            color=color,
            fontsize=9,
            va="center",
            ha="right",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.15", fc="#0e1116", ec=color, alpha=0.85),
            zorder=5,
        )

    entry_color = "#42a5f5" if side == "LONG" else "#ab47bc"
    level(entry_price, entry_color, "ENTRY", ls="--", lw=1.6)
    level(tp, "#26a69a", "TP", ls="--", lw=1.3)
    level(sl, "#ef5350", "SL", ls="--", lw=1.3)

    # entry vertical marker
    if 0 <= entry_pos < len(x):
        ax.axvline(x[entry_pos], color=entry_color, linestyle=":", linewidth=1.0, alpha=0.7, zorder=1)
        ax.scatter(
            [x[entry_pos]],
            [entry_price],
            marker="^" if side == "LONG" else "v",
            s=70,
            color=entry_color,
            edgecolors="white",
            linewidths=0.6,
            zorder=6,
        )

    disp = display_symbol(symbol)
    # Avoid emoji in matplotlib titles (DejaVu has no color emoji glyphs).
    side_tag = "LONG" if side == "LONG" else "SHORT"
    ax.set_title(
        f"{disp}  ·  {side_tag}  ·  15m  ·  SMA/EMA 20·60·120",
        color="#e6edf3",
        fontsize=12.5,
        pad=10,
        loc="left",
    )
    ax.tick_params(colors="#8b949e", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.yaxis.label.set_color("#8b949e")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    fig.autofmt_xdate(rotation=20, ha="right")
    ax.grid(True, color="#21262d", linewidth=0.6, alpha=0.8)
    # Data occupies left ~2/3; right ~1/3 is empty margin so signal is not edge-cramped.
    x_left = float(x[0] - width)
    x_data_right = float(x[-1] + width)
    data_span = max(x_data_right - x_left, 1e-9)
    blank_frac = float(RIGHT_BLANK_FRAC)
    blank_frac = min(max(blank_frac, 0.05), 0.6)
    # data_span / total = (1 - blank_frac)  →  total = data_span / (1 - blank_frac)
    total_span = data_span / (1.0 - blank_frac)
    x_right = x_left + total_span
    ax.set_xlim(x_left, x_right)

    # keep TP/SL + MA bundle in view
    y_vals = [float(highs.max()), float(lows.min()), entry_price]
    for v in (tp, sl):
        if np.isfinite(v):
            y_vals.append(float(v))
    for name in MA_STYLE:
        if name in sub.columns:
            vals = sub[name].to_numpy(dtype=float)
            vals = vals[np.isfinite(vals)]
            if len(vals):
                y_vals.extend([float(vals.min()), float(vals.max())])
    y_lo, y_hi = min(y_vals), max(y_vals)
    pad = (y_hi - y_lo) * 0.08 or entry_price * 0.01
    ax.set_ylim(y_lo - pad, y_hi + pad)

    fig.tight_layout(pad=0.6)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        safe = symbol.replace("/", "_")
        ts = str(record.get("signal_time") or "na").replace(":", "").replace(" ", "_")[:20]
        out_path = CHART_DIR / f"{safe}_{ts}.png"
    out_path = Path(out_path)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_path


def alerts_enabled() -> bool:
    """Env FABLE_TG_SIGNAL_ALERTS=0 disables; default on when TG config exists."""
    flag = os.environ.get("FABLE_TG_SIGNAL_ALERTS", "1").strip().lower()
    return flag not in {"0", "false", "off", "no"}


def notify_signal(record: Mapping[str, Any], *, dry_run: bool = False) -> bool:
    """Format + chart + send one signal. Never raises."""
    try:
        caption = format_signal_caption(record)
        chart = render_signal_chart(record)
        if dry_run:
            print(caption)
            print(f"chart -> {chart}")
            return chart is not None
        if chart is not None:
            ok = send_photo(chart, caption=caption)
            if ok:
                return True
            # fall back to text if photo fails
        return send(caption)
    except Exception as exc:  # noqa: BLE001
        print(f"tg_signal: notify failed: {exc}")
        try:
            return send(format_signal_caption(record))
        except Exception:
            return False


def notify_new_forward_signals(
    new_records: list[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> int:
    """Send alerts for newly opened threshold signals. Returns success count."""
    if not new_records:
        return 0
    if not alerts_enabled() and not dry_run:
        print("tg_signal: alerts disabled (FABLE_TG_SIGNAL_ALERTS=0)")
        return 0
    ok_n = 0
    for record in new_records:
        # only alert entries that pass score threshold semantics already
        if notify_signal(record, dry_run=dry_run):
            ok_n += 1
    return ok_n


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Demo / resend one signal alert")
    parser.add_argument("--symbol", default="ETH_USDT_SWAP")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send", action="store_true", help="actually push to Telegram")
    args = parser.parse_args()

    frame = _load_ohlc("okx", args.symbol)
    if frame is None or len(frame) < 120:
        print(f"not enough bars for {args.symbol}")
        return 1
    signal_i = len(frame) - 20
    entry_i = signal_i + 1
    entry = float(frame["open"].iloc[entry_i])
    # rough atr
    atr = float((frame["high"] - frame["low"]).iloc[signal_i - 14 : signal_i + 1].mean())
    record = {
        "source": "okx",
        "symbol": args.symbol,
        "side": "LONG",
        "signal_time": str(frame["open_time"].iloc[signal_i]),
        "entry_time": str(frame["open_time"].iloc[entry_i]),
        "entry_price": entry,
        "signal_i": signal_i,
        "atr14": atr,
        "atr_pct": atr / entry,
        "score": 0.85,
        "threshold": 0.0165,
        "status": "open",
    }
    dry = args.dry_run or not args.send
    ok = notify_signal(record, dry_run=dry)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
