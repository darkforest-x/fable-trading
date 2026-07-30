#!/usr/bin/env python3
"""Render ~200 sample trade charts with **Claude K-line style** for L2 v10 freeze.

必须用 ``src.notify_signal.render_signal_chart`` 同款画法（不是裸 YOLO 方图）：
  - 深色底 + 红绿烛 + 实线 SMA/EMA 20·60·120
  - SHORT：ENTRY 紫虚线 + ▼；TP 绿虚线（下方 5×ATR）；SL 红虚线（上方 2×ATR）
  - 入场竖线 + 出场路径/圆点（TP绿 / SL红 / 超时琥珀）

WINDOW=200 训推一致（信号前 200 根）；信号后最多 HORIZON=72 根画出出场。

死命令（仓库根；含出入场叠层重建 + 注入 report）::

  cd /Users/zhangzc/fable-trading && \\
  PYTHONPATH=. python3 scripts/build_l2_v10_freeze_sample_gallery.py && \\
  PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && \\
  open analysis/output/l2_v10_reg_freeze_20260731/report.html
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.data.loader import list_series, load_series
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits
from src.judgment.yolo_candidates import WINDOW
from src.notify_signal import (
    MA_STYLE,
    add_dual_mas,
    barrier_prices,
    display_symbol,
)

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/l2_v10_reg_freeze_20260731"
SAMP = OUT / "samples"
META = PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json"
N_SAMPLE = 200
TP_MULT, SL_MULT = 5.0, 2.0
HORIZON = 72  # bars after signal for exit path
LOOKBACK = WINDOW  # 200 — same as detection/judgment window

# Claude / notify_signal palette (hex)
COL_ENTRY_SHORT = "#ab47bc"
COL_TP = "#26a69a"
COL_SL = "#ef5350"
COL_TO = "#f59e0b"
BG = "#0e1116"


def _short_exit_px(entry: float, ret: float) -> float | None:
    """v10 short labels use realized_ret = entry/exit - 1 (see label_short_candidate)."""
    if not np.isfinite(ret) or not np.isfinite(entry) or entry <= 0:
        return None
    denom = 1.0 + ret
    if abs(denom) < 1e-12:
        return None
    return entry / denom


def _resolve_exit_short(
    enriched: pd.DataFrame,
    signal_i: int,
    entry: float,
    atr: float,
    ret: float,
    outcome_hint: str = "",
) -> tuple[int | None, float | None, str]:
    """Walk short TP5/SL2 barriers (same geometry as label_short_candidate).

    Do **not** call resolve_forward_exit — that is long-only (TP up / SL down).
    """
    entry_i = signal_i + 1
    if entry_i >= len(enriched) or not np.isfinite(atr) or atr <= 0:
        return None, _short_exit_px(entry, ret), outcome_hint or "timeout"

    lower = entry - TP_MULT * atr  # TP for short
    upper = entry + SL_MULT * atr  # SL for short
    last_i = min(entry_i + HORIZON - 1, len(enriched) - 1)
    highs = enriched["high"].to_numpy()[entry_i : last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : last_i + 1]
    n = len(highs)
    if n == 0:
        return None, _short_exit_px(entry, ret), outcome_hint or "timeout"

    hit_dn = lows <= lower
    hit_up = highs >= upper
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else n
    up_first = int(np.argmax(hit_up)) if hit_up.any() else n

    if dn_first < up_first:
        exit_i = entry_i + dn_first
        return exit_i, float(lower), "tp"
    if up_first < dn_first:
        exit_i = entry_i + up_first
        return exit_i, float(upper), "sl"
    if dn_first == up_first < n:
        exit_i = entry_i + up_first
        return exit_i, float(upper), "sl"
    # timeout at horizon close (if full horizon available)
    if last_i >= entry_i + HORIZON - 1:
        exit_i = last_i
        exit_px = float(enriched["close"].iloc[exit_i])
        return exit_i, exit_px, "timeout"

    # partial path: fall back to label ret
    return last_i, _short_exit_px(entry, ret), outcome_hint or "timeout"


def render_claude_short_trade(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    entry: float,
    atr: float,
    exit_i: int | None,
    exit_px: float | None,
    outcome: str,
    symbol: str,
    out_path: Path,
) -> None:
    """Claude-style K-line (notify_signal) + short entry/TP/SL + exit path."""
    side = "SHORT"
    tp, sl = barrier_prices(entry, atr, side=side, tp_mult=TP_MULT, sl_mult=SL_MULT)
    entry_i = min(signal_i + 1, len(frame) - 1)  # next open

    frame_ma = add_dual_mas(frame)
    start = max(0, signal_i - LOOKBACK + 1)
    end = min(len(frame_ma), max(signal_i + HORIZON, exit_i or signal_i) + 1)
    if exit_i is not None:
        end = max(end, min(len(frame_ma), exit_i + 1))
    sub = frame_ma.iloc[start:end].reset_index(drop=True)
    entry_pos = entry_i - start
    exit_pos = None if exit_i is None else int(exit_i - start)
    if exit_pos is not None and (exit_pos < 0 or exit_pos >= len(sub)):
        exit_pos = len(sub) - 1

    times = pd.to_datetime(sub["open_time"], utc=True)
    x = mdates.date2num(times.to_numpy(dtype="datetime64[ns]"))
    opens = sub["open"].to_numpy(dtype=float)
    highs = sub["high"].to_numpy(dtype=float)
    lows = sub["low"].to_numpy(dtype=float)
    closes = sub["close"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(11.2, 5.8), dpi=130)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

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
                max(body_high - body_low, 1e-12 * max(entry, 1)),
                facecolor=color,
                edgecolor=color,
                linewidth=0.6,
                zorder=3,
            )
        )

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
            linestyle="-",
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
            bbox=dict(boxstyle="round,pad=0.15", fc=BG, ec=color, alpha=0.85),
            zorder=5,
        )

    # Claude SHORT: entry purple dashed + ▼; TP/SL dashed
    level(entry, COL_ENTRY_SHORT, "ENTRY SHORT", ls="--", lw=1.6)
    level(tp, COL_TP, f"TP {TP_MULT:g}xATR", ls="--", lw=1.3)
    level(sl, COL_SL, f"SL {SL_MULT:g}xATR", ls="--", lw=1.3)

    if 0 <= entry_pos < len(x):
        ax.axvline(x[entry_pos], color=COL_ENTRY_SHORT, linestyle=":", linewidth=1.0, alpha=0.7, zorder=1)
        ax.scatter(
            [x[entry_pos]],
            [entry],
            marker="v",
            s=90,
            color=COL_ENTRY_SHORT,
            edgecolors="white",
            linewidths=0.7,
            zorder=6,
        )

    # exit path + circle
    if exit_px is not None and np.isfinite(exit_px) and exit_pos is not None and 0 <= exit_pos < len(x):
        path_col = COL_TP if outcome == "tp" else (COL_SL if outcome == "sl" else COL_TO)
        if 0 <= entry_pos < len(x):
            ax.plot(
                [x[entry_pos], x[exit_pos]],
                [entry, exit_px],
                color=path_col,
                linewidth=1.2,
                alpha=0.85,
                zorder=5,
            )
        ax.scatter(
            [x[exit_pos]],
            [exit_px],
            marker="o",
            s=70,
            color=path_col,
            edgecolors="white",
            linewidths=0.8,
            zorder=7,
        )
        lab = {"tp": "TP exit", "sl": "SL exit", "timeout": "timeout"}.get(outcome, "exit")
        ax.annotate(
            f"{lab} {exit_px:.4g}",
            xy=(x[exit_pos], exit_px),
            xytext=(8, 10),
            textcoords="offset points",
            color=path_col,
            fontsize=8.5,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.15", fc=BG, ec=path_col, alpha=0.85),
            zorder=8,
        )

    disp = display_symbol(symbol)
    ax.set_title(
        f"{disp}  ·  SHORT  ·  15m  ·  SMA/EMA 20·60·120  ·  Claude overlay",
        color="#e6edf3",
        fontsize=12.5,
        pad=10,
        loc="left",
    )
    ax.tick_params(colors="#8b949e", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    fig.autofmt_xdate(rotation=20, ha="right")
    ax.grid(True, color="#21262d", linewidth=0.6, alpha=0.8)

    x_left = float(x[0] - width)
    x_right = float(x[-1] + width)
    ax.set_xlim(x_left, x_right)

    y_vals = [float(highs.max()), float(lows.min()), entry]
    for v in (tp, sl, exit_px if exit_px is not None else np.nan):
        if np.isfinite(v):
            y_vals.append(float(v))
    for name in MA_STYLE:
        if name in sub.columns:
            vals = sub[name].to_numpy(dtype=float)
            vals = vals[np.isfinite(vals)]
            if len(vals):
                y_vals.extend([float(vals.min()), float(vals.max())])
    y_lo, y_hi = min(y_vals), max(y_vals)
    pad = (y_hi - y_lo) * 0.08 or entry * 0.01
    ax.set_ylim(y_lo - pad, y_hi + pad)

    fig.tight_layout(pad=0.6)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    if not META.exists():
        raise SystemExit(f"missing {META}")
    meta = json.loads(META.read_text())
    thr = float(meta["threshold_val_q90"])
    best = int(meta.get("best_iteration") or 1)
    booster = lgb.Booster(model_file=str(PROJECT / meta["model_path"]))
    train, val, _ = load_splits(PROJECT / meta["dataset_path"], horizon_bars=72)
    val = val.copy()
    val["score"] = booster.predict(
        val[list(FEATURE_COLUMNS)], num_iteration=best if best > 0 else None
    )
    train = train.copy()
    train["score"] = booster.predict(
        train[list(FEATURE_COLUMNS)], num_iteration=best if best > 0 else None
    )

    # Oversample: some rows miss kline / lookback; aim for N_SAMPLE rendered cards.
    k = max(1, len(val) // 10)
    val_s = val.sort_values("score", ascending=False)
    top, bot, mid = val_s.head(k), val_s.tail(k), val_s.iloc[k:-k] if len(val_s) > 2 * k else val_s
    parts = [
        top.sample(n=min(len(top), 160), random_state=42).assign(
            band="top-decile 顶十分位", split="val 验证集"
        ),
        bot.sample(n=min(len(bot), 80), random_state=43).assign(
            band="bottom-decile 底十分位", split="val 验证集"
        ),
        mid.sample(n=min(len(mid), 80), random_state=44).assign(
            band="mid 中间分位", split="val 验证集"
        ),
    ]
    samples = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["symbol", "signal_time"])
    keys = set(zip(samples["symbol"], samples["signal_time"].astype(str)))
    # train pool as fill buffer (top + random mid) for render misses
    tr_s = train.sort_values("score", ascending=False)
    tr_fill = tr_s[~tr_s.apply(lambda r: (r["symbol"], str(r["signal_time"])) in keys, axis=1)]
    samples = pd.concat(
        [
            samples,
            tr_fill.head(250).assign(
                band="top-decile 顶十分位(train补)", split="train 训练集"
            ),
        ],
        ignore_index=True,
    )
    samples = samples.reset_index(drop=True)

    groups = list_series(bar="15m")
    sym_paths: dict[str, list[Path]] = {}
    for (src, sym), paths in groups.items():
        if not str(sym).endswith("_USDT_SWAP"):
            continue
        if src == "okx":
            sym_paths[sym] = list(paths)
        elif sym not in sym_paths:
            sym_paths[sym] = list(paths)

    SAMP.mkdir(parents=True, exist_ok=True)
    for p in SAMP.glob("*.png"):
        p.unlink()

    cards = []
    miss = 0
    frame_cache: dict[str, pd.DataFrame] = {}
    for _, row in samples.iterrows():
        sym = str(row["symbol"])
        st = pd.Timestamp(row["signal_time"])
        st = st.tz_localize("UTC") if st.tzinfo is None else st.tz_convert("UTC")
        if sym not in frame_cache:
            paths = sym_paths.get(sym)
            if not paths:
                miss += 1
                continue
            fr = load_series(paths)
            if fr.empty:
                miss += 1
                continue
            fr = add_indicators(fr)
            frame_cache[sym] = fr
        fr = frame_cache[sym]
        times = pd.to_datetime(fr["open_time"], utc=True)
        hits = np.flatnonzero(times == st)
        if len(hits) == 0:
            diffs = (times - st).abs()
            si = int(diffs.argmin())
            if diffs.iloc[si] > pd.Timedelta(minutes=20):
                miss += 1
                continue
        else:
            si = int(hits[0])
        if si < LOOKBACK - 1:
            miss += 1
            continue

        if si + 1 < len(fr):
            entry = float(fr["open"].iloc[si + 1])
        else:
            entry = (
                float(row["entry_price"])
                if pd.notna(row.get("entry_price"))
                else float(fr["close"].iloc[si])
            )
        atr = (
            float(row["atr14"])
            if pd.notna(row.get("atr14"))
            else (
                float(fr["atr14"].iloc[si])
                if "atr14" in fr.columns and pd.notna(fr["atr14"].iloc[si])
                else entry * 0.01
            )
        )
        if not np.isfinite(atr) or atr <= 0:
            atr = abs(entry) * 0.01
        tp, sl = barrier_prices(entry, atr, side="SHORT", tp_mult=TP_MULT, sl_mult=SL_MULT)
        ret = float(row["realized_ret"])
        outcome_hint = str(row.get("outcome_barrier") or row.get("outcome") or "").lower()
        if outcome_hint in {"timeout"}:
            pass
        elif outcome_hint in {"tp", "sl", "sl_ambiguous"}:
            if outcome_hint == "sl_ambiguous":
                outcome_hint = "sl"
        else:
            outcome_hint = ""
        exit_i, exit_px, outcome = _resolve_exit_short(
            fr, si, entry, atr, ret, outcome_hint=outcome_hint
        )
        if exit_px is None:
            exit_px = _short_exit_px(entry, ret)

        tag = "pos" if ret > 0 else ("neg" if ret < 0 else "flat")
        fname = (
            f"{len(cards)+1:03d}_{re.sub(r'[^A-Za-z0-9_]', '', sym)[:24]}_"
            f"{st.strftime('%Y%m%d_%H%M')}_{tag}.png"
        )
        try:
            render_claude_short_trade(
                fr,
                signal_i=si,
                entry=entry,
                atr=atr,
                exit_i=exit_i,
                exit_px=exit_px,
                outcome=outcome,
                symbol=sym,
                out_path=SAMP / fname,
            )
        except Exception as exc:  # noqa: BLE001
            print("render fail", sym, exc)
            miss += 1
            continue

        score = float(row["score"])
        cards.append(
            {
                "i": len(cards) + 1,
                "file": f"samples/{fname}",
                "symbol": sym,
                "signal_time": str(st),
                "score": round(score, 6),
                "realized_ret": round(ret, 6),
                "ret_pct": round(100 * ret, 2),
                "passed": bool(score >= thr),
                "band": str(row.get("band", "")),
                "split": str(row.get("split", "")),
                "label": int(row["label"]) if pd.notna(row.get("label")) else None,
                "entry": round(entry, 8),
                "tp": round(float(tp), 8) if np.isfinite(tp) else None,
                "sl": round(float(sl), 8) if np.isfinite(sl) else None,
                "exit": None if exit_px is None else round(float(exit_px), 8),
                "outcome": outcome or "",
                "side": "short",
                "overlay": "claude_notify_signal_SHORT_TP5_SL2_exit",
            }
        )
        if len(cards) % 50 == 0:
            print(f"rendered {len(cards)}", flush=True)
        if len(cards) >= N_SAMPLE:
            break

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "samples_manifest.json").write_text(
        json.dumps(
            {
                "n": len(cards),
                "miss": miss,
                "threshold": thr,
                "overlay": (
                    "claude notify_signal K-line: ENTRY SHORT (purple dashed+▼), "
                    "TP 5xATR green dashed, SL 2xATR red dashed, exit path+circle"
                ),
                "window_bars": LOOKBACK,
                "horizon_bars_after": HORIZON,
                "renderer": "notify_signal-style (matplotlib dual MA + barriers)",
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"cards={len(cards)} miss={miss} -> {SAMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
