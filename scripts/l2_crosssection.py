"""Direction on L1's own output, using cross-sectional and long-horizon inputs.

The last run settled that a coin's own 15m geometry carries no direction signal
on an unselected population: AUC 0.523 against a 50.0% base. Two families were
never in that feature set.

  cross-section  what the rest of the market is doing at the same instant. A coin
                 breaking down while 400 others hold is a different event from one
                 breaking down with everything else, and nothing so far could tell
                 those apart.
  long horizon   the old set topped out at 400 bars, about four days. Weekly and
                 monthly position were never available to it.

Measured on L1's firings, not owner's boxes. Training on owner's boxes produced
0.727 that collapsed to 0.523 the moment it met real candidates; that number came
from conditioning on a hand-picked set, so this one is fitted where it is used.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
from src.detection.data import add_mas

HORIZON, BARRIER = 48, 1.5
HOLD = np.datetime64("2026-05-04T00:00:00")

files = {}
for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
    m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
    if m: files[m.group(1)] = p
print(f"universe {len(files)} 个币，开始建横截面面板…", flush=True)

# ---- cross-sectional panel: close of every symbol on one time index ----
ser = {}
for sym, p in files.items():
    d = pd.read_csv(p, usecols=["open_time", "close"])
    s = pd.Series(d["close"].to_numpy(),
                  index=pd.to_datetime(d["open_time"], utc=True).dt.tz_convert(None))
    ser[sym] = s[~s.index.duplicated()]
P = pd.DataFrame(ser).sort_index()
print(f"面板 {P.shape[0]:,} 行 × {P.shape[1]} 币", flush=True)

R = {k: P.pct_change(k) for k in (20, 48, 200)}
XS = {}
for k in (20, 48, 200):
    XS[f"xs_rank_{k}"] = R[k].rank(axis=1, pct=True)          # this coin vs the field
    XS[f"xs_med_{k}"] = R[k].median(axis=1)                   # the field itself
XS["breadth_48"] = (R[48] < 0).mean(axis=1)                   # share of coins falling
XS["disp_48"] = R[48].std(axis=1)
print("横截面特征完成", flush=True)


def prep(path):
    fr = pd.read_csv(path).sort_values("ts").reset_index(drop=True)
    c = fr["close"].to_numpy(); h = fr["high"].to_numpy(); l = fr["low"].to_numpy()
    tr = pd.concat([fr["high"]-fr["low"], (fr["high"]-fr["close"].shift()).abs(),
                    (fr["low"]-fr["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.rolling(14).mean().to_numpy(); a = np.where(a > 0, a, np.nan)
    S = pd.Series
    F = {}
    for k in (96, 672, 2688):                                  # day, week, month
        F[f"lt_trend_{k}"] = (c - np.roll(c, k)) / (a * np.sqrt(k))
        rh = S(h).rolling(k).max().to_numpy(); rl = S(l).rolling(k).min().to_numpy()
        F[f"lt_range_{k}"] = (c - rl) / np.where(rh - rl > 0, rh - rl, np.nan)
    F["lt_dd_2688"] = (c - S(h).rolling(2688).max().to_numpy()) / a
    t = pd.to_datetime(fr["open_time"], utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    return F, c, h, l, a, t, len(fr)


LT = ["lt_trend_96","lt_trend_672","lt_trend_2688","lt_range_96","lt_range_672",
      "lt_range_2688","lt_dd_2688"]
XSC = list(XS)
scan = json.load(open("analysis/output/smallwin_scan_plain.json"))
cache, rows = {}, []
for f in scan["fires"]:
    sym = f["symbol"]
    if sym not in files: continue
    if sym not in cache: cache[sym] = prep(files[sym])
    F, c, h, l, a, t, n = cache[sym]
    tip = int(f["tip"])
    if tip < 2700 or tip + HORIZON >= n: continue
    ts = t[tip]
    x = {k: float(F[k][tip]) for k in LT}
    for k in XSC:
        col = XS[k]
        try:
            v = col.loc[ts, sym] if k.startswith("xs_rank") else col.loc[ts]
        except KeyError:
            v = np.nan
        x[k] = float(v)
    if not all(np.isfinite(list(x.values()))): continue
    atr, c0 = a[tip], c[tip]
    if not np.isfinite(atr) or atr <= 0: continue
    up, dn = c0 + BARRIER*atr, c0 - BARRIER*atr; y = None
    for i in range(tip+1, tip+HORIZON+1):
        if l[i] <= dn: y = 1; break
        if h[i] >= up: y = 0; break
    if y is None: continue
    x.update(y=y, symbol=sym, t=ts, conf=f["conf"]); rows.append(x)

L = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
FE = LT + XSC
print(f"\nL1 输出上可用样本 {len(L)}   基准向下率 {L.y.mean()*100:.1f}%   币 {L.symbol.nunique()}")

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
def auc(y,s):
    y=np.asarray(y); s=np.asarray(s)
    if y.sum() in (0,len(y)): return np.nan
    r=pd.Series(s).rank().to_numpy(); n1=int(y.sum())
    return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*(len(y)-n1)))
def wilson(k,n,z=1.96):
    p=k/n; d=1+z*z/n; ce=(p+z*z/(2*n))/d; hw=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (ce-hw, ce+hw)

for name, cols in (("只长周期", LT), ("只横截面", XSC), ("两者合并", FE)):
    oof = np.full(len(L), np.nan)
    for tr_i, va_i in GroupKFold(n_splits=4).split(L, L.y, groups=L.symbol):
        m = HistGradientBoostingClassifier(max_iter=200, learning_rate=.06,
                                           max_depth=3, random_state=1)
        m.fit(L.iloc[tr_i][cols].to_numpy(), L.iloc[tr_i].y.to_numpy())
        oof[va_i] = m.predict_proba(L.iloc[va_i][cols].to_numpy())[:,1]
    y = L.y.to_numpy()
    print(f"\n=== {name}（{len(cols)} 特征）· 币种隔离 4 折 · AUC {auc(y,oof):.3f} ===")
    for frac in (0.3, 0.1):
        k = max(30, int(len(oof)*frac)); idx = np.argsort(-oof)[:k]
        p = y[idx].mean(); lo,hi = wilson(int(y[idx].sum()), k)
        print(f"  最高 {frac*100:.0f}%  n={k}  向下率 {p*100:.1f}%  [{lo*100:.1f}, {hi*100:.1f}]"
              f"  提升 {(p-y.mean())*100:+.1f}pp")
L.to_csv("analysis/output/l2_crosssection.csv", index=False)
