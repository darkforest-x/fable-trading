"""L1 into L2: does the direction filter survive on the detector's own output?

L2 scored 0.727 / 0.689 on owner's boxes. Those boxes are hand-picked and 43%
A-grade. Live, L1 supplies the candidates, and L1's population is a different
animal — v10's fresh detections graded 14.3% A. A filter measured on the clean
population tells you nothing about the dirty one until you run it there.

Two decision-bar conventions are tried, because they disagree about what "now"
means:

  tip     the bar the scan fired on. This is what live actually has.
  tight-5 the convention L2 was trained under, five bars before the tightest bar
          in the box, chosen to sit before the break.

Everything is computed from bars <= the decision bar. The outcome is the same
mechanical first-touch of +/-1.5 ATR within 48 bars.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
exec(open("scripts/l2_feature_expansion.py").read().split("from sklearn.ensemble")[0])
from sklearn.ensemble import HistGradientBoostingClassifier

COLS = BASE + STRUCT
print(f"L2 训练集: {len(D)} 个 owner 框")

def train(offset):
    """Retrain L2 with the decision bar at `offset` bars before the tightest bar."""
    rows = []
    for line in open(EV):
        e = json.loads(line)
        if e["source"] != "golden_pool": continue
        sym = e["symbol"]
        if sym not in files: continue
        if sym not in cache: cache[sym] = prep(files[sym], BTC)
        F,c,h,l,a,t,n = cache[sym]
        bs,be = e["original_box"]["box_start_i"], e["original_box"]["box_end_i"]
        if bs is None or be is None or bs < 560 or be+HORIZON >= n: continue
        seg = F["width"][bs:be+1]
        if not np.isfinite(seg).any(): continue
        d = bs + int(np.nanargmin(seg)) - offset
        if d < 560 or d+HORIZON >= n or t[d] >= HOLD: continue
        x = {k: float(F[k][d]) for k in COLS}
        if not all(np.isfinite(list(x.values()))): continue
        atr,c0 = a[d], c[d]
        if not np.isfinite(atr) or atr <= 0: continue
        up,dn = c0+BARRIER*atr, c0-BARRIER*atr; y=None
        for i in range(d+1, d+HORIZON+1):
            if l[i] <= dn: y=1; break
            if h[i] >= up: y=0; break
        if y is None: continue
        x["y"]=y; rows.append(x)
    T = pd.DataFrame(rows)
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=.06, max_depth=4, random_state=1)
    m.fit(T[COLS].to_numpy(), T.y.to_numpy())
    return m, len(T)

models = {}
for off in (0, 5):
    models[off] = train(off)
    print(f"  L2(offset={off}) 训练 {models[off][1]} 条")

scan = json.load(open("analysis/output/smallwin_scan_plain.json"))
fires = scan["fires"]
print(f"\nL1 输出: {len(fires)} 次新鲜开火，{len({f['symbol'] for f in fires})} 个 val 币")

rows = []
for f in fires:
    sym = f["symbol"]
    if sym not in files: continue
    if sym not in cache: cache[sym] = prep(files[sym], BTC)
    F,c,h,l,a,t,n = cache[sym]
    tip = int(f["tip"])
    if tip < 560 or tip+HORIZON >= n: continue
    x = {k: float(F[k][tip]) for k in COLS}
    if not all(np.isfinite(list(x.values()))): continue
    atr,c0 = a[tip], c[tip]
    if not np.isfinite(atr) or atr <= 0: continue
    up,dn = c0+BARRIER*atr, c0-BARRIER*atr; y=None
    for i in range(tip+1, tip+HORIZON+1):
        if l[i] <= dn: y=1; break
        if h[i] >= up: y=0; break
    if y is None: continue
    x.update(y=y, symbol=sym, conf=f["conf"], matched=f["matched"]); rows.append(x)

L = pd.DataFrame(rows)
print(f"能定结果的: {len(L)} 次   基准向下率 {L.y.mean()*100:.1f}%")

def wilson(k,n,z=1.96):
    if n==0: return (np.nan,np.nan)
    p=k/n; d=1+z*z/n; ce=(p+z*z/(2*n))/d; hw=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (ce-hw, ce+hw)
def auc(y,s):
    y=np.asarray(y); s=np.asarray(s)
    if y.sum() in (0,len(y)): return np.nan
    r=pd.Series(s).rank().to_numpy(); n1=int(y.sum())
    return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*(len(y)-n1)))

y = L.y.to_numpy()
for off in (0,5):
    m,_ = models[off]
    s = m.predict_proba(L[COLS].to_numpy())[:,1]
    print(f"\n=== L2(训练 offset={off}) 打在 L1 输出上 · AUC {auc(y,s):.3f} ===")
    print(f"{'取分数最高':<10}{'n':>6}{'向下率':>9}{'95%CI':>18}{'提升':>9}")
    for frac in (1.0,0.5,0.3,0.2,0.1):
        k=max(20,int(len(s)*frac)); idx=np.argsort(-s)[:k]
        p=y[idx].mean(); lo,hi=wilson(int(y[idx].sum()),k)
        lab='全部' if frac==1 else f'{frac*100:.0f}%'
        print(f"{lab:<10}{k:>6}{p*100:>8.1f}%{f'[{lo*100:.1f}, {hi*100:.1f}]':>18}"
              f"{(p-y.mean())*100:>+8.1f}pp")
    L[f"s{off}"]=s

print(f"\n=== 对照：只用 L1 自己的 conf 排序 · AUC {auc(y, L.conf.to_numpy()):.3f} ===")
cs = L.conf.to_numpy()
for frac in (0.3,0.1):
    k=max(20,int(len(cs)*frac)); idx=np.argsort(-cs)[:k]
    p=y[idx].mean(); lo,hi=wilson(int(y[idx].sum()),k)
    print(f"  conf 最高 {frac*100:.0f}%  n={k}  向下率 {p*100:.1f}%  [{lo*100:.1f}, {hi*100:.1f}]")
L.to_csv("analysis/output/l1_l2_endtoend.csv", index=False)
print("\nwrote analysis/output/l1_l2_endtoend.csv")
