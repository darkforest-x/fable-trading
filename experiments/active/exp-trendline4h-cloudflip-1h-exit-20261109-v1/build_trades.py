"""Build owner-rule signals and matched cloud-flip-only controls from OKX 4H bars.

Definitions frozen in config.json. Causal inputs only: the V1.2 break at bar x
(known at x's close), the 4H EMA20/50 cloud flip at bar e (known at e's close),
the stop = min(low[x..e]) (known at e's close). Writes trades.csv with the 1H
window each trade needs; no outcome is read here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "exp-trendline4h-breakout-runs-20261109-v1"))
import trendline_v12  # noqa: E402

CFG = json.loads((HERE / "config.json").read_text())
SRC = ROOT / "data/crypto/market/okx_swap_4h_20261109"
H4 = 4 * 3600 * 1000
PRE_H, POST_H = 300, 480


def ema(c: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(c)
    a = 2 / (n + 1)
    out[0] = c[0]
    for i in range(1, len(c)):
        out[i] = out[i - 1] + a * (c[i] - out[i - 1])
    return out


def main() -> int:
    rng = np.random.default_rng(CFG["control"]["seed"])
    rows = []
    for f in sorted(SRC.glob("okx_*_4H_*.csv")):
        sym = f.name[4:].split("_4H_")[0]
        df = pd.read_csv(f)
        n = len(df)
        if n < 200:
            continue
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        ts = df.ts.to_numpy(np.int64)
        sp = ema(c, 20) - ema(c, 50)
        cross = np.zeros(n, bool)
        cross[1:] = (sp[:-1] <= 0) & (sp[1:] > 0)
        cross[:60] = False
        breaks = [e.x for e in trendline_v12.run(o, h, l, c).events if e.kind == "break"]
        near_break = np.zeros(n, bool)
        for x in breaks:
            near_break[x:min(n, x + 11)] = True  # flips at e with a break in [e-10, e]
        pool = np.flatnonzero(cross & ~near_break)
        used_ctrl: set[int] = set()
        for x in breaks:
            if x < 60:
                continue
            w = np.flatnonzero(cross[x:min(n, x + 11)])
            if not len(w):
                continue
            e = x + int(w[0])
            d = e - x
            stop = float(l[x:e + 1].min())
            rows.append(dict(kind="signal", symbol=sym, break_i=x, entry4h_i=e, delay=d, group=f"{sym}:{x}",
                             entry_close_ms=int(ts[e] + H4), entry=float(c[e]), stop=stop))
            cand = [b for b in pool if abs(b - e) <= 180 and b - d >= 0 and b not in used_ctrl]
            if cand:
                for b in rng.choice(cand, size=min(CFG["control"]["draws_per_signal"], len(cand)), replace=False):
                    b = int(b)
                    used_ctrl.add(b)
                    rows.append(dict(kind="control", symbol=sym, break_i=-1, entry4h_i=b, delay=d, group=f"{sym}:{x}",
                                     entry_close_ms=int(ts[b] + H4), entry=float(c[b]), stop=float(l[b - d:b + 1].min())))
    tr = pd.DataFrame(rows)
    tr["risk_pct"] = (tr.entry - tr.stop) / tr.entry * 100
    tr["win_start_ms"] = tr.entry_close_ms - PRE_H * 3600_000
    tr["win_end_ms"] = tr.entry_close_ms + POST_H * 3600_000
    tr.insert(0, "trade_id", [f"{k[0]}{i:05d}" for i, k in enumerate(tr.kind)])
    tr.to_csv(HERE / "trades.csv", index=False)
    print(tr.kind.value_counts().to_dict(), "zero-risk:", int((tr.risk_pct <= 0).sum()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
