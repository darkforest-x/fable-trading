"""Does "drop the barriers" survive a time split, or is it the usual in-sample win?

Removing TP/SL beat the current barriers 8.5x on the pooled train candidates
(+0.2645% vs +0.0312%). This project's most reliable pattern is that a pooled
in-sample win does not survive: judgment top-decile, detector confidence, the BTC
mid-volatility band, regime selection and 7-day trend buckets all worked pooled
and failed on the later data. So the number means nothing until it is cut by time.

Split entirely inside the train pool (<2026-05-04) -- the holdout stays untouched
and unread (iron rule 1). Two cuts, because they fail differently:

  HALVES    early vs late half. A finding that only lives in one half is a regime
            artefact, not an exit property.
  QUARTERS  four consecutive blocks. Monotone decay across them is the signature
            of an edge that is being arbitraged away or of a changing market,
            and it is invisible in a two-way split.

Reported per block for both exits side by side, so "the hold is better" can be
checked block by block rather than on the pooled average that hides it.

Read-only, train pool only, no holdout, no promote, no config change.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_no_barrier_time_stability.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72


def simulate(ind, i: int, barriers: bool) -> float | None:
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None
    if not barriers:
        return 1 - cl[-1] / entry
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = int(np.argmax(lo <= tp)) if (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        return 1 - tp / entry
    if dn < up:
        return 1 - sl / entry
    return 1 - cl[-1] / entry


def block(df: pd.DataFrame) -> dict:
    b = df["barrier"].to_numpy() - SWAP_TAKER
    h = df["hold"].to_numpy() - SWAP_TAKER
    def pf(x):
        w, l = x[x > 0].sum(), x[x < 0].sum()
        return round(float(w / -l), 3) if l < 0 else None
    return {"n": len(df),
            "from": str(df["t"].min())[:10], "to": str(df["t"].max())[:10],
            "barrier_net": round(float(b.mean()), 6), "barrier_pf": pf(b),
            "hold_net": round(float(h.mean()), 6), "hold_pf": pf(h),
            "hold_wins": bool(h.mean() > b.mean())}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START]                     # iron rule 1: holdout untouched
    series = list_series(bar="15m")
    cache: dict[str, object] = {}
    rows = []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = add_indicators(add_mas(load_series(series[key]))) if key in series else None
        ind = cache[sym]
        if ind is None:
            continue
        times = pd.to_datetime(ind["open_time"], utc=True)
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if not (200 <= i < len(ind) - 2):
                continue
            a, b = simulate(ind, i, True), simulate(ind, i, False)
            if a is None or b is None:
                continue
            rows.append({"t": r["t"], "barrier": a, "hold": b})
    df = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    print(f"训练池候选 {len(df)}   {str(df['t'].min())[:10]} ~ {str(df['t'].max())[:10]}")
    print(f"(holdout >= {str(HOLDOUT_START)[:10]} 未读取)\n")

    out = {"pooled": block(df), "halves": [], "quarters": []}
    print(f"{'区块':<22} {'笔数':>6} {'期间':>24} {'现行障碍净':>12} {'PF':>6} "
          f"{'纯持72根净':>12} {'PF':>6} {'谁赢':>6}")

    def show(label: str, g: pd.DataFrame, bucket: list) -> None:
        r = block(g)
        bucket.append({"label": label, **r})
        print(f"{label:<22} {r['n']:>6} {r['from']}~{r['to']:>10} "
              f"{r['barrier_net']*100:>+11.4f}% {str(r['barrier_pf']):>6} "
              f"{r['hold_net']*100:>+11.4f}% {str(r['hold_pf']):>6} "
              f"{'纯持' if r['hold_wins'] else '障碍':>6}")

    p = out["pooled"]
    print(f"{'全池':<22} {p['n']:>6} {p['from']}~{p['to']:>10} "
          f"{p['barrier_net']*100:>+11.4f}% {str(p['barrier_pf']):>6} "
          f"{p['hold_net']*100:>+11.4f}% {str(p['hold_pf']):>6} "
          f"{'纯持' if p['hold_wins'] else '障碍':>6}")
    print()
    for k, g in enumerate(np.array_split(df, 2)):
        show(f"前后半 {k+1}/2", g, out["halves"])
    print()
    for k, g in enumerate(np.array_split(df, 4)):
        show(f"四分位 {k+1}/4", g, out["quarters"])

    wins_h = sum(x["hold_wins"] for x in out["halves"])
    wins_q = sum(x["hold_wins"] for x in out["quarters"])
    qn = [x["hold_net"] for x in out["quarters"]]
    decay = all(qn[i] >= qn[i + 1] for i in range(len(qn) - 1))
    if wins_h == 2 and wins_q == 4:
        verdict = (f"纯持在 2/2 半段、4/4 四分位全部胜出 → 不是单一时段的产物;"
                   f"但仍是训练池样本内,确认级需 holdout(owner 批准)")
    elif wins_q >= 3:
        verdict = (f"纯持在 {wins_q}/4 四分位胜出,{4-wins_q} 段落败 → 大体稳定但非一致,"
                   f"落败段需单独看")
    else:
        verdict = (f"纯持只在 {wins_q}/4 四分位胜出 → 池化的 8.5 倍是少数时段撑起来的,"
                   f"与本项目「样本内有效、样本外不复现」的老模式一致")
    if decay and len(qn) == 4:
        verdict += f";且四分位单调衰减({qn[0]*100:+.3f}% → {qn[-1]*100:+.3f}%),边在变薄"
    print(f"\n判读: {verdict}")

    (PROJECT / "analysis" / "output" / "diag_no_barrier_time_stability.json").write_text(
        json.dumps({"pool": POOL.name, **out, "verdict": verdict},
                   indent=2, ensure_ascii=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
