#!/usr/bin/env python3
"""Tip-replay backtest for dense_owner_w20_midbox detector.

Protocol (iron rule 12 + project economics):
  - Tip window: fixed W bars ending at tip t (default W=24, mid of train 20–30).
  - Full-series MA → slice → render_chart (same as training / live).
  - YOLO conf (default 0.15 from val gate).
  - A' edge gate: box right edge maps to tip or tip-1 (TIP_EDGE_BARS=2).
  - MIN_GAP_BARS dedup per symbol.
  - Entry = next bar open; TP5/SL2 on signal-bar ATR14; horizon 72; same-bar → SL.
  - Net = gross − FORWARD_COST (maker RT).
  - Matched control: same symbol × UTC-month × atr_pct quintile random entries,
    same barriers/cost; excess = det − cell mean control.
  - UTC-week block sign-flip permutation p on mean excess.

Holdout (≥2026-05-04): refused unless --allow-holdout. Owner approved
2026-08-07 in chat; this config's holdout consumption is recorded in the report.

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \\
      --weights analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt \\
      --start 2026-03-01 --end 2026-05-03 --tag w20_preholdout
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \\
      --weights ... --start 2026-05-04 --end 2026-07-01 --allow-holdout \\
      --holdout-n 1 --tag w20_holdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
os.environ.setdefault("YOYO_DATA_ROOT", str(PROJECT))

from yoyo.data.loader import list_series, load_series  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart  # noqa: E402

from src.costs import FORWARD_COST  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS  # noqa: E402
from src.judgment.labeling import HORIZON_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 5.0, 2.0
DEFAULT_WINDOW = 24
DEFAULT_CONF = 0.15
PREDICT_BATCH = 12
CTRL_PER_CELL = 8
N_PERM = 2000
BLOCKED = {
    "USDC", "USDG", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "BUSD",
    "XAU", "XAG", "XAUT", "PAXG",
}


@dataclass
class Trade:
    symbol: str
    signal_i: int
    signal_time: str
    entry_time: str
    conf: float
    atr: float
    atr_pct: float
    outcome: str
    gross_ret: float
    net_ret: float
    segment: str  # preholdout | holdout


def atr14(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def resolve_trade(df: pd.DataFrame, t: int) -> dict | None:
    entry_i = t + 1
    if entry_i >= len(df):
        return None
    atr = float(df["atr14"].iloc[t])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df["open"].iloc[entry_i])
    last_i = min(entry_i + HORIZON_BARS - 1, len(df) - 1)
    highs = df["high"].to_numpy()[entry_i : last_i + 1]
    lows = df["low"].to_numpy()[entry_i : last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up1 = int(np.argmax(hit_up)) if hit_up.any() else len(highs)
    dn1 = int(np.argmax(hit_dn)) if hit_dn.any() else len(highs)
    if up1 < dn1:
        outcome, ret = "tp", upper / entry - 1.0
    elif dn1 <= up1 and hit_dn.any():
        outcome, ret = "sl", lower / entry - 1.0
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        outcome, ret = "timeout", float(df["close"].iloc[last_i]) / entry - 1.0
    else:
        return None
    close_t = float(df["close"].iloc[t])
    atr_pct = atr / close_t if close_t > 0 else float("nan")
    return {
        "outcome": outcome,
        "gross_ret": float(ret),
        "entry_time": str(df["open_time"].iloc[entry_i]),
        "atr": atr,
        "atr_pct": float(atr_pct),
        "entry_i": entry_i,
    }


def series_pool(min_bars: int) -> list[tuple[str, pd.DataFrame]]:
    groups: dict[tuple[str, str], list[Path]] = {}
    for d in (PROJECT / "data" / "kline_cache", PROJECT / "data" / "kline_fetched"):
        if not d.is_dir():
            continue
        part = list_series(cache_dir=d, bar="15m")
        for k, paths in part.items():
            groups.setdefault(k, []).extend(paths)
    out: list[tuple[str, pd.DataFrame]] = []
    for (_src, sym), paths in sorted(groups.items()):
        if not sym.endswith("_USDT_SWAP"):
            continue
        base = sym.split("_", 1)[0]
        if base in BLOCKED or is_stockish(sym) or is_eval_symbol(sym):
            continue
        try:
            df = load_series(paths)
        except Exception:
            continue
        if len(df) < min_bars:
            continue
        out.append((sym, df))
    return out


def replay_symbol(
    symbol: str,
    df: pd.DataFrame,
    model,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    window: int,
    conf: float,
    device: str,
    tip_stride: int,
    segment: str,
) -> tuple[list[Trade], int, int]:
    enriched = add_mas(df)
    enriched = enriched.copy()
    enriched["atr14"] = atr14(enriched)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    lo = int(np.searchsorted(times, start))
    hi = int(np.searchsorted(times, end, side="right"))
    lo = max(lo, window)
    # leave room for entry+horizon resolution
    hi = min(hi, len(enriched) - HORIZON_BARS - 2)
    if hi <= lo:
        return [], 0, 0

    trades: list[Trade] = []
    n_fired = 0
    bars_scanned = 0
    last_signal = -(10**9)
    tmpdir = Path(tempfile.mkdtemp(prefix=f"w20tip_{symbol}_"))
    batch: list[tuple[int, object, Path]] = []

    def flush(batch_items: list) -> None:
        nonlocal n_fired, last_signal
        if not batch_items:
            return
        paths = [str(p) for _, _, p in batch_items]
        res = model.predict(paths, conf=conf, verbose=False, device=device)
        for (t, tf, _), r in zip(batch_items, res):
            boxes = r.boxes
            if boxes is None or len(boxes) == 0:
                continue
            fired = False
            best_c = 0.0
            xy = boxes.xywhn.cpu().numpy()
            cf = boxes.conf.cpu().numpy()
            for b, c in zip(xy, cf):
                cx, _, w, _ = map(float, b[:4])
                bar = right_edge_to_bar(cx, w, tf, n_bars=window)
                if bar >= window - TIP_EDGE_BARS:
                    fired = True
                    best_c = max(best_c, float(c))
            if not fired:
                continue
            n_fired += 1
            if t - last_signal < MIN_GAP_BARS:
                continue
            tr = resolve_trade(enriched, t)
            if tr is None:
                continue
            last_signal = t
            trades.append(
                Trade(
                    symbol=symbol,
                    signal_i=t,
                    signal_time=str(times.iloc[t]),
                    entry_time=tr["entry_time"],
                    conf=best_c,
                    atr=tr["atr"],
                    atr_pct=tr["atr_pct"],
                    outcome=tr["outcome"],
                    gross_ret=tr["gross_ret"],
                    net_ret=tr["gross_ret"] - FORWARD_COST,
                    segment=segment,
                )
            )

    tips = list(range(lo, hi, max(1, tip_stride)))
    for t in tips:
        bars_scanned += 1
        sub = enriched.iloc[t - window + 1 : t + 1].reset_index(drop=True)
        if len(sub) != window:
            continue
        p = tmpdir / f"{t % (PREDICT_BATCH * 4)}.png"
        try:
            _, tf = render_chart(sub, out_path=str(p))
        except Exception:
            continue
        batch.append((t, tf, p))
        if len(batch) >= PREDICT_BATCH:
            flush(batch)
            batch = []
    flush(batch)
    # cleanup pngs
    for p in tmpdir.glob("*.png"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        tmpdir.rmdir()
    except OSError:
        pass
    return trades, n_fired, bars_scanned


def build_controls(
    trades: list[Trade],
    series_map: dict[str, pd.DataFrame],
    *,
    rng: np.random.Generator,
    n_per: int = CTRL_PER_CELL,
) -> tuple[pd.DataFrame, dict]:
    """Match each trade to random entries in same symbol×month×atr quintile."""
    if not trades:
        return pd.DataFrame(), {"n_pairs": 0}

    # atr edges from detector pool
    atrs = np.array([t.atr_pct for t in trades if np.isfinite(t.atr_pct)])
    if len(atrs) < 20:
        edges = np.array([-np.inf, np.inf])
    else:
        edges = np.quantile(atrs, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
        edges[0], edges[-1] = -np.inf, np.inf
        # unique edges
        edges = np.unique(edges)

    def cell_of(sym: str, ts: pd.Timestamp, atr_pct: float) -> str:
        m = ts.strftime("%Y-%m")
        try:
            q = int(np.searchsorted(edges, atr_pct, side="right") - 1)
            q = max(0, min(q, len(edges) - 2))
        except Exception:
            q = 0
        return f"{sym}|{m}|q{q}"

    # precompute candidate indices per symbol
    cand: dict[str, pd.DataFrame] = {}
    for sym, df in series_map.items():
        en = df
        if "atr14" not in en.columns:
            en = en.copy()
            en["atr14"] = atr14(en)
        times = pd.to_datetime(en["open_time"], utc=True)
        atr = en["atr14"].to_numpy()
        close = en["close"].to_numpy()
        atr_pct = np.where(close > 0, atr / close, np.nan)
        ok = np.where(
            np.isfinite(atr_pct)
            & (np.arange(len(en)) >= 50)
            & (np.arange(len(en)) + 1 + HORIZON_BARS < len(en))
        )[0]
        if len(ok) == 0:
            continue
        t_ok = pd.to_datetime(times.iloc[ok], utc=True)
        cand[sym] = pd.DataFrame(
            {
                "i": ok,
                "t": t_ok.to_numpy(),
                "atr_pct": atr_pct[ok],
                "month": t_ok.dt.strftime("%Y-%m").to_numpy(),
            }
        )

    rows = []
    miss = 0
    for tr in trades:
        ts = pd.Timestamp(tr.signal_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        cell = cell_of(tr.symbol, ts, tr.atr_pct)
        sym = tr.symbol
        if sym not in cand:
            miss += 1
            continue
        cdf = cand[sym]
        month = ts.strftime("%Y-%m")
        # atr quintile match
        try:
            q = int(np.searchsorted(edges, tr.atr_pct, side="right") - 1)
            q = max(0, min(q, max(0, len(edges) - 2)))
        except Exception:
            q = 0
        # bucket on cdf
        ap = cdf["atr_pct"].to_numpy()
        qq = np.searchsorted(edges, ap, side="right") - 1
        qq = np.clip(qq, 0, max(0, len(edges) - 2))
        mask = (cdf["month"].to_numpy() == month) & (qq == q)
        pool_i = cdf.loc[mask, "i"].to_numpy()
        if len(pool_i) < 5:
            # fall back: same month only
            mask = cdf["month"].to_numpy() == month
            pool_i = cdf.loc[mask, "i"].to_numpy()
        if len(pool_i) < 3:
            miss += 1
            continue
        pick = rng.choice(pool_i, size=min(n_per, len(pool_i)), replace=False)
        df = series_map[sym]
        if "atr14" not in df.columns:
            df = df.copy()
            df["atr14"] = atr14(df)
        ctrl_rets = []
        for i in pick:
            r = resolve_trade(df, int(i))
            if r is None:
                continue
            ctrl_rets.append(r["gross_ret"] - FORWARD_COST)
        if not ctrl_rets:
            miss += 1
            continue
        ctrl_mean = float(np.mean(ctrl_rets))
        rows.append(
            {
                "symbol": tr.symbol,
                "signal_time": tr.signal_time,
                "segment": tr.segment,
                "outcome": tr.outcome,
                "net_ret": tr.net_ret,
                "ctrl_mean": ctrl_mean,
                "excess": tr.net_ret - ctrl_mean,
                "cell": cell,
                "week": ts.strftime("%Y-W%W"),
                "n_ctrl": len(ctrl_rets),
            }
        )
    matched = pd.DataFrame(rows)
    meta = {"n_pairs": int(len(matched)), "n_miss": miss, "edges": edges.tolist()}
    return matched, meta


def perm_p_week_signflip(matched: pd.DataFrame, n_perm: int, rng: np.random.Generator) -> float:
    """Two-sided p: UTC-week block sign-flip of excess under H0 mean=0."""
    if matched.empty:
        return float("nan")
    excess = matched["excess"].to_numpy(dtype=float)
    weeks = matched["week"].to_numpy()
    obs = float(np.mean(excess))
    # unique weeks
    uniq = list(pd.unique(weeks))
    week_idx = {w: np.where(weeks == w)[0] for w in uniq}
    ge = 0
    for _ in range(n_perm):
        # flip all excesses in a week together
        signs = {w: rng.choice([-1.0, 1.0]) for w in uniq}
        perm = excess.copy()
        for w, idx in week_idx.items():
            perm[idx] *= signs[w]
        if abs(float(np.mean(perm))) >= abs(obs) - 1e-15:
            ge += 1
    return (ge + 1) / (n_perm + 1)


def summarize(trades: list[Trade], matched: pd.DataFrame, perm_p: float, meta: dict) -> dict:
    net = np.array([t.net_ret for t in trades], dtype=float)
    gross = np.array([t.gross_ret for t in trades], dtype=float)
    wins = float(net[net > 0].sum()) if net.size else 0.0
    losses = float(net[net < 0].sum()) if net.size else 0.0
    oc = pd.Series([t.outcome for t in trades]).value_counts().to_dict() if trades else {}
    out = {
        "n_trades": int(net.size),
        "win_rate": round(float((net > 0).mean()), 4) if net.size else None,
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else None,
        "mean_gross_bp": round(float(gross.mean() * 1e4), 2) if net.size else None,
        "mean_net_bp": round(float(net.mean() * 1e4), 2) if net.size else None,
        "total_net_units": round(float(net.sum()), 5) if net.size else None,
        "outcomes": oc,
        "matched_n": int(len(matched)),
        "matched_lift_bp": round(float(matched["excess"].mean() * 1e4), 2) if len(matched) else None,
        "matched_lift_se_bp": round(
            float(matched["excess"].std(ddof=1) / np.sqrt(len(matched)) * 1e4), 2
        )
        if len(matched) > 1
        else None,
        "perm_p": round(float(perm_p), 4) if np.isfinite(perm_p) else None,
        "ctrl_meta": meta,
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--start", default="2026-03-01")
    ap.add_argument("--end", default="2026-05-03")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--tip-stride", type=int, default=1)
    ap.add_argument("--n-symbols", type=int, default=0, help="0 = all eligible")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--device", default=None)
    ap.add_argument("--tag", default="w20_midbox_tip")
    ap.add_argument("--allow-holdout", action="store_true")
    ap.add_argument(
        "--holdout-n",
        type=int,
        default=0,
        help="this config's holdout consumption ordinal (record in report)",
    )
    ap.add_argument("--seed", type=int, default=20260807)
    args = ap.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)
    touches_holdout = end > HOLDOUT_START or start >= HOLDOUT_START
    if touches_holdout and not args.allow_holdout:
        raise SystemExit(
            f"window touches holdout (>={HOLDOUT_START.date()}). "
            "Need owner approval + --allow-holdout + --holdout-n."
        )
    if touches_holdout and args.holdout_n < 1:
        raise SystemExit("holdout window requires --holdout-n >= 1 for ledger")

    segment = "holdout" if start >= HOLDOUT_START else "preholdout"
    if start < HOLDOUT_START < end:
        segment = "mixed"  # will tag per trade

    device = args.device
    if device is None:
        import torch

        if torch.cuda.is_available():
            device = "0"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    print(f"device={device} window={args.window} conf={args.conf}", flush=True)

    model = load_yolo_model(args.weights)
    pool = series_pool(min_bars=args.window + HORIZON_BARS + 200)
    rng = np.random.default_rng(args.seed)
    if args.symbols:
        chosen = [(s, f) for s, f in pool if s in set(args.symbols)]
    elif args.n_symbols and args.n_symbols > 0:
        idx = rng.choice(len(pool), size=min(args.n_symbols, len(pool)), replace=False)
        chosen = [pool[i] for i in sorted(idx)]
    else:
        chosen = pool
    print(f"symbols={len(chosen)} / pool={len(pool)}", flush=True)

    all_trades: list[Trade] = []
    total_fired = 0
    bars_scanned = 0
    series_map = {s: f for s, f in chosen}

    for i, (sym, frame) in enumerate(chosen, 1):
        # tag segment per tip time
        trades, fired, scanned = replay_symbol(
            sym,
            frame,
            model,
            start,
            end,
            window=args.window,
            conf=args.conf,
            device=device,
            tip_stride=args.tip_stride,
            segment=segment,
        )
        # fix mixed
        if segment == "mixed":
            for t in trades:
                ts = pd.Timestamp(t.signal_time)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                t.segment = "holdout" if ts >= HOLDOUT_START else "preholdout"
        all_trades.extend(trades)
        total_fired += fired
        bars_scanned += scanned
        print(
            f"[{i}/{len(chosen)}] {sym}: fired={fired} trades={len(trades)} "
            f"cum_trades={len(all_trades)}",
            flush=True,
        )

    # ensure atr14 on series for controls
    for sym, df in list(series_map.items()):
        if "atr14" not in df.columns:
            d2 = df.copy()
            d2["atr14"] = atr14(d2)
            series_map[sym] = d2

    matched, ctrl_meta = build_controls(all_trades, series_map, rng=rng)
    pval = perm_p_week_signflip(matched, N_PERM, rng)
    summary = summarize(all_trades, matched, pval, ctrl_meta)
    summary.update(
        {
            "tag": args.tag,
            "weights": str(Path(args.weights).resolve()),
            "window_bars": args.window,
            "conf": args.conf,
            "tip_stride": args.tip_stride,
            "tip_edge_bars": TIP_EDGE_BARS,
            "protocol": (
                f"tip_replay W={args.window} conf>={args.conf} edge>={args.window - TIP_EDGE_BARS}; "
                f"entry t+1 open; TP{TP_MULT}/SL{SL_MULT}/H{HORIZON_BARS}; "
                f"cost={FORWARD_COST}; MIN_GAP={MIN_GAP_BARS}; "
                f"matched control symbol×month×atr_q; week sign-flip perm n={N_PERM}"
            ),
            "range": f"{args.start}..{args.end}",
            "n_symbols": len(chosen),
            "bars_scanned": bars_scanned,
            "fired_raw": total_fired,
            "fire_per_1k_bars": round(1000 * total_fired / max(bars_scanned, 1), 3),
            "cost": FORWARD_COST,
            "holdout_touched": bool(touches_holdout),
            "holdout_consumption_n": args.holdout_n if touches_holdout else 0,
            "holdout_note": (
                f"w20_midbox tip-replay config holdout consumption #{args.holdout_n}; "
                "owner approved 2026-08-07 chat"
                if touches_holdout
                else "pre-holdout only"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    # by segment
    by_seg = {}
    for seg in ("preholdout", "holdout"):
        sub = [t for t in all_trades if t.segment == seg]
        if not sub:
            continue
        msub = matched[matched["segment"] == seg] if len(matched) and "segment" in matched else pd.DataFrame()
        psub = perm_p_week_signflip(msub, N_PERM, rng) if len(msub) else float("nan")
        by_seg[seg] = summarize(sub, msub, psub, {"n_pairs": int(len(msub))})
    summary["by_segment"] = by_seg

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path = out_dir / f"{args.tag}_trades.csv"
    pd.DataFrame([asdict(t) for t in all_trades]).to_csv(trades_path, index=False)
    if len(matched):
        matched.to_csv(out_dir / f"{args.tag}_matched.csv", index=False)
    js_path = out_dir / f"{args.tag}.json"
    js_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "trades_n": len(all_trades),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {js_path}")
    print(f"wrote {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
