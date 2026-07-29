"""Score a candidate pool with Kronos and write its forecast as judgment features.

The judgment layer runs on 28 hand-built features plus 19 causal alphas, and that
set is out of road: switching the target to regression is worth +17.82bp and the
permutation test on the result sits at p=0.32. Kronos is a foundation model over
K-lines -- 45 exchanges, 12B bars, MIT -- so the question is whether a learned
representation carries anything the hand features do not.

Cost was the blocker and is now measured (scripts/kronos_batch_bench.py, M4 MPS,
Kronos-small): one forecast at a time is 7.8s and 55.7 hours for the pool.
Batching is worth only 1.7x, because the bottleneck is serial autoregressive
generation rather than parallelism. The tenfold win is the horizon -- 72 bars to
12 -- which costs nothing that matters here, since the feature is "how far does
it move", not the path. Batch 8 at 12 bars is 0.77s per candidate, 5.5 hours for
25,602, and runs on the Mac without touching the 3060.

Causality: the context ends at the signal bar and Kronos never sees a bar past it,
so these features are usable at decision time. The forecast itself is of course
about the future -- that is the point -- but it is produced from past bars only.

Writes a CSV of kr_* columns keyed to (symbol, signal_time). It trains nothing and
adopts nothing; whether these features earn their place is decided by the same bar
every other candidate feature faces:

    beat +17.82bp top-decile lift, permutation p<0.01, matched random control.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_kronos_features.py \
      --pool data/judgment_v10_wide.csv --out data/kronos_feats_v10.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "external" / "Kronos"))

from src.data.loader import list_series, load_series  # noqa: E402

LOOKBACK = 400
PRED_LEN = 12          # 3 hours at 15m; 72 costs 10x for a path we do not use
BATCH = 8              # measured sweet spot; 32 is already slower per item
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")


# Autoregressive sampling occasionally diverges: on a 24-row smoke test the
# medians tracked reality (+2.39% predicted against +1.67% actual) while the MEAN
# was -271%, because a few paths ran the price toward zero. One such row poisons
# a feature column, so degenerate forecasts are dropped rather than clipped --
# clipping would keep a fabricated value at the boundary and the model would learn
# the boundary.
MAX_ABS_MOVE = 0.60          # a 12-bar 15m move beyond +-60% is the sampler failing


def features_from(pred_df: pd.DataFrame, entry: float) -> dict[str, float]:
    """Reduce a forecast path to numbers a judgment layer can use.

    A short cares about how far price travels down, how much of the path is
    spent below entry, and how far it runs up on the way -- the last one is the
    forecast's version of the adverse excursion that decides whether a stop is
    hit, which is exactly what the hand features cannot see.
    """
    c = pred_df["close"].to_numpy(dtype=float)
    h = pred_df["high"].to_numpy(dtype=float)
    lo = pred_df["low"].to_numpy(dtype=float)
    if len(c) == 0 or not np.isfinite(entry) or entry <= 0:
        return {}
    if not (np.all(np.isfinite(c)) and np.all(np.isfinite(h)) and np.all(np.isfinite(lo))):
        return {}
    if np.min(lo) <= 0 or np.min(c) <= 0:
        return {}
    moves = np.abs(np.r_[c, h, lo] / entry - 1)
    if np.max(moves) > MAX_ABS_MOVE:
        return {}
    return {
        "kr_ret_end": c[-1] / entry - 1,
        "kr_ret_min": float(np.min(lo)) / entry - 1,      # best case for a short
        "kr_ret_max": float(np.max(h)) / entry - 1,       # adverse excursion
        "kr_frac_below": float(np.mean(c < entry)),
        "kr_slope": float(np.polyfit(np.arange(len(c)), c / entry - 1, 1)[0]),
        "kr_path_vol": float(np.std(np.diff(c) / entry)) if len(c) > 1 else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="NeoQuasar/Kronos-small")
    ap.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    ap.add_argument("--pred-len", type=int, default=PRED_LEN)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--limit", type=int, default=0, help="0 = whole pool")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer

    pool = Path(args.pool) if Path(args.pool).is_absolute() else PROJECT / args.pool
    out_path = Path(args.out) if Path(args.out).is_absolute() else PROJECT / args.out
    d = pd.read_csv(pool)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].reset_index(drop=True)          # iron rule 1
    if args.limit:
        d = d.head(args.limit)

    # resume: a 5-hour run must survive being interrupted
    done: set[tuple[str, str]] = set()
    if out_path.exists():
        try:
            prev = pd.read_csv(out_path, usecols=["symbol", "signal_time"])
            done = set(zip(prev["symbol"], prev["signal_time"]))
            print(f"resume: {len(done)} 行已存在,跳过")
        except Exception:  # noqa: BLE001
            done = set()

    dev = args.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"池 {pool.name}  {len(d)} 行   设备 {dev}   "
          f"预测 {args.pred_len} 根   批 {args.batch}", flush=True)
    tok = KronosTokenizer.from_pretrained(args.tokenizer)
    mdl = Kronos.from_pretrained(args.model)
    predictor = KronosPredictor(mdl, tok, device=dev, max_context=512)

    series = list_series(bar="15m")
    cache: dict[str, pd.DataFrame | None] = {}

    def frame(sym: str):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                fr = load_series(series[key])
                fr["timestamps"] = pd.to_datetime(fr["open_time"], utc=True).dt.tz_localize(None)
                fr["_t"] = pd.to_datetime(fr["open_time"], utc=True)
                cache[sym] = fr
            else:
                cache[sym] = None
        return cache[sym]

    pend_x, pend_xt, pend_yt, pend_meta = [], [], [], []
    written = 0
    t0 = time.perf_counter()
    header_needed = not out_path.exists()

    def flush():
        nonlocal pend_x, pend_xt, pend_yt, pend_meta, written, header_needed
        if not pend_x:
            return
        try:
            outs = predictor.predict_batch(
                df_list=pend_x, x_timestamp_list=pend_xt, y_timestamp_list=pend_yt,
                pred_len=args.pred_len, T=1.0, top_p=0.9, sample_count=1,
                verbose=False)
        except Exception as exc:  # noqa: BLE001
            print(f"   批失败,跳过 {len(pend_x)} 条: {str(exc)[:80]}", flush=True)
            pend_x, pend_xt, pend_yt, pend_meta = [], [], [], []
            return
        rows = []
        for o, (sym, ts, entry) in zip(outs, pend_meta):
            f = features_from(o, entry)
            if f:
                rows.append({"symbol": sym, "signal_time": ts, **f})
            else:
                globals()["_N_DEGEN"] = globals().get("_N_DEGEN", 0) + 1
        if rows:
            pd.DataFrame(rows).to_csv(out_path, mode="a",
                                      header=header_needed, index=False)
            header_needed = False
            written += len(rows)
        pend_x, pend_xt, pend_yt, pend_meta = [], [], [], []

    for n, r in enumerate(d.itertuples(), 1):
        key = (r.symbol, str(r.signal_time))
        if key in done:
            continue
        fr = frame(r.symbol)
        if fr is None:
            continue
        i = int(fr["_t"].searchsorted(r.t))
        if i < LOOKBACK or i >= len(fr) - 1:
            continue
        # context ends at the signal bar: nothing to its right is ever shown
        x = fr.iloc[i - LOOKBACK + 1:i + 1]
        y_ts = pd.Series(pd.date_range(x["timestamps"].iloc[-1],
                                       periods=args.pred_len + 1, freq="15min")[1:])
        pend_x.append(x[["open", "high", "low", "close", "volume"]].reset_index(drop=True))
        pend_xt.append(x["timestamps"].reset_index(drop=True))
        pend_yt.append(y_ts.reset_index(drop=True))
        pend_meta.append((r.symbol, str(r.signal_time), float(x["close"].iloc[-1])))
        if len(pend_x) >= args.batch:
            flush()
        if n % 500 == 0:
            el = time.perf_counter() - t0
            rate = el / max(written, 1)
            print(f"  [{n}/{len(d)}] 已写 {written}   {el/60:.1f}min   "
                  f"剩余约 {(len(d)-n)*rate/3600:.1f}h", flush=True)
    flush()

    el = time.perf_counter() - t0
    n_deg = globals().get("_N_DEGEN", 0)
    print(f"\n写入 {written} 行 -> {out_path}   用时 {el/3600:.2f} 小时")
    print(f"  丢弃发散预测 {n_deg} 条 = {100*n_deg/max(written+n_deg,1):.1f}%"
          f"(|涨跌| > {MAX_ABS_MOVE*100:.0f}% 视为采样失败)")
    if written:
        chk = pd.read_csv(out_path)
        print(f"  列: {[c for c in chk.columns if c.startswith('kr_')]}")
        for c in [c for c in chk.columns if c.startswith("kr_")]:
            print(f"    {c:<16} 中位 {chk[c].median():+.5f}  缺失 {chk[c].isna().mean()*100:.1f}%")
    print("\n注:仅生成特征,不训练、不采纳。判据:顶档提升 > +17.82bp、"
          "置换 p<0.01、带匹配随机对照。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
