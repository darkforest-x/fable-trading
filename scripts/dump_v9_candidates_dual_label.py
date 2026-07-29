"""Rebuild the judgment pool on v9's own candidates, labelled under BOTH exits.

Two things force this rebuild rather than reusing judgment_yolo_short_v6_wide.csv.

SAME-SOURCE  the judgment layer must be trained on the candidate distribution it
will score. The frozen v11 layer, measured on the v6 pool today, does not merely
fail -- it inverts: its top decile returns -0.2979% against the pool's +0.0312%,
its bottom decile +0.0975%. That is the same-source failure the v16 rebuild
already diagnosed, repeating. Training on v6 candidates and then serving v9 would
reproduce it a third time.

DUAL LABEL   the exit is undecided. Holding 72 bars with no TP/SL beat
TP5xATR/SL2xATR by 8.5x pooled and won 3 of 4 quarters, but decays (+0.78% ->
+0.46% -> +0.055%) and loses outright in Q1. Rather than block the rebuild on
that decision, every row carries both outcomes, so whichever exit the owner picks
the pool is already labelled for it -- and the two can be compared on identical
candidates.

No dense prefilter. The v16 dump cut the bar universe ~15x with fast<=0.0028 &
full<=0.0055, run>=5, which is safe for a detector trained on that rule and wrong
for this one: only 31.0% of the owner's own accepted boxes qualify as mechanically
dense, so the gate would silently discard most of what v9 fires on and quietly
change the pool being built. Correctness over the 15x -- this scans every bar.

Causal: each window ends at bar t and the tip-edge gate is applied, so no bar to
the right of the box is ever rendered (the lookahead that once produced PF 6.61).
Labels look forward; features never do.

Train side only, < 2026-05-04. Holdout untouched (iron rule 1). Writes a CSV; it
promotes nothing and changes no config.

Usage (RTX 3060):
  $env:PYTHONPATH="C:\\fable"
  .venv\\Scripts\\python.exe scripts\\dump_v9_candidates_dual_label.py `
      --weights models\\owner_short_star_v9.pt --n-symbols 40 --device 0 `
      --out data\\judgment_v9_dual.csv
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS, add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 5.0, 2.0
TRAIL_ATR = 3.0          # chandelier distance above the running low
BE_ARM_ATR = 1.5         # profit needed before the stop moves to entry
SL_WIDE_ATR = 4.0        # the "is 2xATR simply too tight" arm
PARTIAL_ATR = 2.0        # half off here, the rest rides the trend
BATCH = 48
RENDER_WORKERS = 6       # cv2 drops the GIL, so rendering overlaps


def both_labels(enriched, i: int) -> dict | None:
    """TP5/SL2 outcome and the plain 72-bar hold, on the same entry."""
    ei = i + 1
    if ei >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[i])
    atr_pct = float(enriched["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(enriched["open"].iloc[ei])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON_BARS - 1, len(enriched) - 1)
    if last - ei + 1 < HORIZON_BARS:
        return None                       # refuse partial horizons: they bias the hold
    hi = enriched["high"].to_numpy()[ei:last + 1]
    lo = enriched["low"].to_numpy()[ei:last + 1]
    cl = enriched["close"].to_numpy()[ei:last + 1]

    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = int(np.argmax(lo <= tp)) if (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        g_bar, why = 1 - tp / entry, "TP"
    elif dn < up:
        g_bar, why = 1 - sl / entry, "SL"
    else:
        g_bar, why = 1 - cl[-1] / entry, "TIMEOUT"
    g_hold = 1 - cl[-1] / entry

    # This is a trend strategy, and the repo has tried these exits piecemeal
    # across separate scripts and separate pools. Labelling all of them on the
    # SAME candidates is the only way the comparison means anything -- earlier
    # rounds compared an exit measured on one pool against another measured on a
    # different one. Every variant below shares this entry and this horizon.
    ma_hi = enriched["ma_max"].to_numpy()[ei:last + 1] if "ma_max" in enriched else None
    ma_lo = enriched["ma_min"].to_numpy()[ei:last + 1] if "ma_min" in enriched else None

    def first_true(mask) -> int | None:
        idx = np.flatnonzero(mask)
        return int(idx[0]) if len(idx) else None

    # 1. trend: alive while price stays under the bundle, dead when it closes above
    j = first_true(np.isfinite(ma_hi) & (cl > ma_hi)) if ma_hi is not None else None
    g_trend, trend_bars = ((1 - cl[j] / entry, j) if j is not None
                           else (g_hold, len(cl) - 1))

    # 2. structural stop: the bundle's upper edge as a hard stop, no target
    j = first_true(np.isfinite(ma_hi) & (hi >= ma_hi)) if ma_hi is not None else None
    g_struct, struct_bars = ((1 - float(ma_hi[j]) / entry, j) if j is not None
                             else (g_hold, len(cl) - 1))

    # 3. chandelier trail: stop rides TRAIL_ATR above the running low
    g_trail, trail_bars = g_hold, len(cl) - 1
    run_lo = np.inf
    for jj in range(len(cl)):
        run_lo = min(run_lo, float(lo[jj]))
        stop = run_lo + TRAIL_ATR * atr
        if jj > 0 and hi[jj] >= stop:
            g_trail, trail_bars = 1 - stop / entry, jj
            break

    # 4. breakeven-then-run: once BE_ATR of profit exists, stop moves to entry
    g_be, be_bars = g_hold, len(cl) - 1
    armed = False
    for jj in range(len(cl)):
        if not armed and lo[jj] <= entry - BE_ARM_ATR * atr:
            armed = True
        elif armed and hi[jj] >= entry:
            g_be, be_bars = 0.0, jj
            break

    # 5. take-profit only, no stop -- isolates what the stop costs
    j = first_true(lo <= entry - TP_MULT * atr)
    g_tponly = (TP_MULT * atr / entry) if j is not None else g_hold
    tponly_bars = j if j is not None else len(cl) - 1

    # 6. wide stop, same target: is the 2xATR stop simply too tight
    tp_w, sl_w = entry - TP_MULT * atr, entry + SL_WIDE_ATR * atr
    up_w = first_true(lo <= tp_w)
    dn_w = first_true(hi >= sl_w)
    if up_w is not None and (dn_w is None or up_w < dn_w):
        g_wide, wide_bars = 1 - tp_w / entry, up_w
    elif dn_w is not None:
        g_wide, wide_bars = 1 - sl_w / entry, dn_w
    else:
        g_wide, wide_bars = g_hold, len(cl) - 1

    # 7. half off at 2xATR, remainder rides the trend exit
    j = first_true(lo <= entry - PARTIAL_ATR * atr)
    if j is None:
        g_partial, partial_bars = g_trend, trend_bars
    else:
        g_partial = 0.5 * (PARTIAL_ATR * atr / entry) + 0.5 * g_trend
        partial_bars = max(j, trend_bars)

    k = min(up, dn, len(cl) - 1)
    return {"entry_price": entry, "atr14": atr, "atr_pct": atr_pct,
            "outcome_barrier": why,
            "gross_barrier": g_bar, "gross_hold": g_hold,
            "net_barrier_maker": g_bar - SWAP_MAKER,
            "net_barrier_taker": g_bar - SWAP_TAKER,
            "net_hold_maker": g_hold - SWAP_MAKER,
            "net_hold_taker": g_hold - SWAP_TAKER,
            **{f"gross_{k}": v for k, v in (
                ("trend", g_trend), ("struct", g_struct), ("trail", g_trail),
                ("be", g_be), ("tponly", g_tponly), ("wide", g_wide),
                ("partial", g_partial))},
            **{f"bars_{k}": v for k, v in (
                ("trend", trend_bars), ("struct", struct_bars), ("trail", trail_bars),
                ("be", be_bars), ("tponly", tponly_bars), ("wide", wide_bars),
                ("partial", partial_bars))},
            **{f"net_{k}_maker": v - SWAP_MAKER for k, v in (
                ("trend", g_trend), ("struct", g_struct), ("trail", g_trail),
                ("be", g_be), ("tponly", g_tponly), ("wide", g_wide),
                ("partial", g_partial))},
            **{f"net_{k}_taker": v - SWAP_TAKER for k, v in (
                ("trend", g_trend), ("struct", g_struct), ("trail", g_trail),
                ("be", g_be), ("tponly", g_tponly), ("wide", g_wide),
                ("partial", g_partial))},
            **{f"label_{k}": int(v - SWAP_MAKER > 0) for k, v in (
                ("trend", g_trend), ("struct", g_struct), ("trail", g_trail),
                ("be", g_be), ("tponly", g_tponly), ("wide", g_wide),
                ("partial", g_partial))},
            "label_barrier": int(g_bar - SWAP_MAKER > 0),
            "label_hold": int(g_hold - SWAP_MAKER > 0),

            # what a real position would have had to sit through, either way
            "mae": float(np.max(hi[: k + 1]) / entry - 1)}


def fire_bars(ema, model, device, lo: int, hi: int, conf: float,
              tmp_dir: Path) -> list[int]:
    """Every tip-aligned fire in [lo, hi), MIN_GAP-deduped. No rule prefilter."""
    fires: list[int] = []
    last_sig = -10 ** 9
    idx = list(range(lo, hi))
    # Rendering is the bottleneck, not inference: on the 3060 the GPU sampled 0%
    # across five consecutive reads at 40-50W while one CPU core drew charts.
    # cv2 releases the GIL, so threads actually overlap here.
    from concurrent.futures import ThreadPoolExecutor

    def render_one(k_t):
        k, t = k_t
        p = tmp_dir / f"w{k}.png"
        try:
            _, tf = render_chart(ema.iloc[t - WINDOW + 1:t + 1], out_path=p)
        except Exception:  # noqa: BLE001
            return None
        return str(p), t, tf

    pool_x = ThreadPoolExecutor(max_workers=RENDER_WORKERS)
    for s in range(0, len(idx), BATCH):
        chunk = idx[s:s + BATCH]
        paths, tfs = [], []
        for r in pool_x.map(render_one, list(enumerate(chunk))):
            if r is None:
                continue
            paths.append(r[0]); tfs.append((r[1], r[2]))
        if not paths:
            continue
        try:
            res = model.predict(paths, conf=conf, verbose=False, device=device,
                                half=(device not in ("cpu", None)))
        except Exception:  # noqa: BLE001
            continue
        for (t, tf), r in zip(tfs, res):
            b = r.boxes
            if b is None or len(b) == 0:
                continue
            hit = False
            for row in b.xywhn.cpu().numpy():
                cx, w = float(row[0]), float(row[2])
                if right_edge_to_bar(cx, w, tf, n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                    hit = True
                    break
            if hit and t - last_sig >= MIN_GAP_BARS:
                fires.append(t)
                last_sig = t
    return fires


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--n-symbols", type=int, default=40)
    ap.add_argument("--end", default="2026-05-03")
    ap.add_argument("--start", default=None,
                    help="scan from this date. A full 11-month series is ~39k bars per\n                         symbol and every bar needs its own 200-bar render, so depth\n                         costs more wall time than symbol count does. Trading depth for\n                         breadth keeps the pool wider across market conditions.")
    ap.add_argument("--device", default=None)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--out", default="data/judgment_v9_dual.csv")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore rows already in --out and rebuild from scratch")
    args = ap.parse_args()
    end = min(pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1), HOLDOUT_START)

    model = load_yolo_model(args.weights)
    device = args.device
    if device is None:
        import torch
        device = "0" if torch.cuda.is_available() else "cpu"
    tmp_dir = PROJECT / "data" / "_v9dump"
    tmp_dir.mkdir(exist_ok=True)
    print(f"weights={args.weights} device={device} conf={args.conf} "
          f"end<{str(end)[:10]} (holdout untouched)", flush=True)

    pool = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=WINDOW + 400):
        if (source != "okx" or not symbol.endswith("_USDT_SWAP")
                or is_stockish(symbol) or is_eval_symbol(symbol)):
            continue
        pool.append((symbol, frame))
    random.seed(20260728)
    chosen = random.sample(pool, min(args.n_symbols, len(pool)))

    # Resume: the CSV is appended per symbol, so a stopped run has usable rows and
    # the symbols already in it must not be redone. Without this, restarting to
    # apply a speedup would redo everything the run had already paid for.
    out_path = PROJECT / args.out
    done: set[str] = set()
    if out_path.exists() and not args.no_resume:
        try:
            done = set(pd.read_csv(out_path, usecols=["symbol"])["symbol"].unique())
        except Exception:  # noqa: BLE001
            done = set()
    if done:
        before = len(chosen)
        chosen = [(s, f) for s, f in chosen if s not in done]
        print(f"resume: {len(done)} symbols already in {out_path.name}, "
              f"{before - len(chosen)} skipped", flush=True)
    print(f"universe={len(pool)} chosen={len(chosen)}", flush=True)

    rows = []
    t0 = time.perf_counter()
    for k, (symbol, frame) in enumerate(chosen, 1):
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < end].reset_index(drop=True)
        if len(frame) < WINDOW + 200:
            continue
        scan_lo = WINDOW
        if args.start:
            t2 = pd.to_datetime(frame["open_time"], utc=True)
            scan_lo = max(WINDOW, int((t2 < pd.Timestamp(args.start, tz="UTC")).sum()))
        ema = add_mas(frame)
        enriched = add_indicators(ema)
        from src.detection.data import ALL_MA_COLS
        _ma = np.vstack([ema[c].to_numpy(dtype=float)
                         for c in ALL_MA_COLS if c in ema.columns])
        enriched["ma_max"] = np.nanmax(_ma, axis=0)
        enriched["ma_min"] = np.nanmin(_ma, axis=0)
        featured = add_features(enriched)
        fires = fire_bars(ema, model, device, scan_lo,
                          len(frame) - HORIZON_BARS - 2, args.conf, tmp_dir)
        if not fires:
            print(f"[{k}/{len(chosen)}] {symbol}: fires=0 "
                  f"({(time.perf_counter()-t0)/60:.1f}min)", flush=True)
            continue
        feats = extract_feature_rows(featured, fires)
        n_ok = 0
        sym_rows = []
        for pos, i in enumerate(fires):
            lab = both_labels(enriched, i)
            if lab is None:
                continue
            row = {c: float(feats.iloc[pos][c]) for c in FEATURE_COLUMNS}
            row.update({"source": "okx", "symbol": symbol, "side": "short",
                        "signal_i": int(i),
                        "signal_time": str(pd.to_datetime(
                            enriched["open_time"], utc=True).iloc[i])})
            row.update(lab)
            rows.append(row); sym_rows.append(row)
            n_ok += 1
        if sym_rows:
            out = PROJECT / args.out
            pd.DataFrame(sym_rows).to_csv(
                out, mode="a", header=not out.exists(), index=False)
        print(f"[{k}/{len(chosen)}] {symbol}: fires={len(fires)} kept={n_ok} "
              f"total={len(rows)} ({(time.perf_counter()-t0)/60:.1f}min)", flush=True)

    try:
        pool_x.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    for p in tmp_dir.glob("*.png"):
        p.unlink(missing_ok=True)
    if not rows:
        print("no rows produced")
        return 1
    out = PROJECT / args.out
    df = pd.read_csv(out) if out.exists() else pd.DataFrame(rows)
    print(f"\nwrote {len(df)} rows -> {out}   ({(time.perf_counter()-t0)/60:.1f} min)")
    print(f"  positive rate  barrier={df['label_barrier'].mean():.3f}  "
          f"hold={df['label_hold'].mean():.3f}")
    print(f"  mean net@maker barrier={df['net_barrier_maker'].mean()*100:+.4f}%  "
          f"hold={df['net_hold_maker'].mean()*100:+.4f}%")
    print(f"  time range {df['signal_time'].min()[:10]} ~ {df['signal_time'].max()[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
