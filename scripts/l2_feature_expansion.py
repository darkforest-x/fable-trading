"""How much of L2's direction signal is the coin, the regime, or just the market.

The trend-only baseline reached 0.620 at a causal decision bar while the shape
features added +0.024. Before spending more effort on shape, the baseline itself
deserves a proper attempt, and one specific confound needs a number on it: the
2026-07-28 control found +7.2bp of a +16.9bp pool was short beta. If a falling
BTC explains most of these breaks, then L2 is a market-direction model wearing a
pattern costume, and it should be built and judged as one.

Four nested feature blocks, each added on top of the last:

  base    what the earlier run used
  struct  longer horizons, MA ordering, distance to slow MAs, range position
  regime  volatility state, squeeze intensity, volume behaviour
  market  BTC's own move over the same windows, and this symbol relative to it
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
from src.detection.data import add_mas

MA = ["sma20","ema20","sma60","ema60","sma120","ema120"]
HOLD = np.datetime64("2026-05-04T00:00:00")
HORIZON, BARRIER, OFFSET = 48, 1.5, 5      # offset 5 = the honest causal point
EV = "/Users/zhangzc/yolo-xx/reports/pattern_event_v3/pattern_events.jsonl"

files = {}
for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
    m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
    if m: files[m.group(1)] = p

BASE   = ["trend_48","trend_120","trend_200","ret_5","ret_10","ret_20","atr_pct","atr_ratio_50"]
STRUCT = ["trend_400","ret_48","ret_96","ret_200","ma_order_bear","dist_sma120",
          "dist_ema120","range_pos_48","range_pos_200","range_pos_500",
          "dd_from_high_200","rally_from_low_200"]
REGIME = ["atr_pct_rank_200","width","width_ratio_5","width_ratio_20","squeeze_depth",
          "vol_z20","vol_ratio_5_20","vol_trend_10","bar_body_atr","bar_range_atr"]
MARKET = ["btc_ret_20","btc_ret_48","btc_ret_200","btc_trend_120","rel_ret_48","rel_ret_200"]
SHAPE  = ["slope","slope_sd","slope_fast_minus_slow","dense_run","pos"]


def prep(path, btc=None):
    fr = add_mas(pd.read_csv(path).sort_values("ts").reset_index(drop=True))
    c = fr["close"].to_numpy(); h = fr["high"].to_numpy(); l = fr["low"].to_numpy()
    o = fr["open"].to_numpy(); v = fr["volume"].to_numpy()
    mas = fr[MA].to_numpy(); hi_ma, lo_ma = mas.max(1), mas.min(1)
    tr = pd.concat([fr["high"]-fr["low"], (fr["high"]-fr["close"].shift()).abs(),
                    (fr["low"]-fr["close"].shift()).abs()], axis=1).max(axis=1)
    a = np.where(tr.rolling(14).mean().to_numpy() > 0, tr.rolling(14).mean().to_numpy(), np.nan)
    S = lambda x: pd.Series(x)
    F = {}
    for k in (48,120,200,400):
        F[f"trend_{k}"] = (c - np.roll(c,k)) / (a*np.sqrt(k))
    for k in (5,10,20,48,96,200):
        F[f"ret_{k}"] = c/np.roll(c,k) - 1.0
    F["atr_pct"] = a/c
    F["atr_ratio_50"] = a / S(a).rolling(50).mean().to_numpy()
    F["atr_pct_rank_200"] = S(F["atr_pct"]).rolling(200).rank(pct=True).to_numpy()
    F["width"] = (hi_ma-lo_ma)/a
    F["width_ratio_5"] = (S(F["width"])/S(F["width"]).shift(5)).to_numpy()
    F["width_ratio_20"] = (S(F["width"])/S(F["width"]).shift(20)).to_numpy()
    F["squeeze_depth"] = S(F["width"]).rolling(200).rank(pct=True).to_numpy()
    # fast MAs below slow MAs = bearish stack
    F["ma_order_bear"] = ((mas[:,0]<mas[:,4]).astype(float)+(mas[:,1]<mas[:,5]).astype(float)
                          +(mas[:,2]<mas[:,4]).astype(float))/3
    F["dist_sma120"] = (c-fr["sma120"].to_numpy())/a
    F["dist_ema120"] = (c-fr["ema120"].to_numpy())/a
    for k in (48,200,500):
        rh = S(h).rolling(k).max().to_numpy(); rl = S(l).rolling(k).min().to_numpy()
        F[f"range_pos_{k}"] = (c-rl)/np.where(rh-rl>0, rh-rl, np.nan)
    rh200 = S(h).rolling(200).max().to_numpy(); rl200 = S(l).rolling(200).min().to_numpy()
    F["dd_from_high_200"] = (c-rh200)/a
    F["rally_from_low_200"] = (c-rl200)/a
    F["vol_z20"] = (v - S(v).rolling(20).mean().to_numpy())/(S(v).rolling(20).std().to_numpy()+1e-9)
    F["vol_ratio_5_20"] = S(v).rolling(5).mean().to_numpy()/(S(v).rolling(20).mean().to_numpy()+1e-9)
    F["vol_trend_10"] = S(v).rolling(10).mean().to_numpy()/(S(v).rolling(50).mean().to_numpy()+1e-9)
    F["bar_body_atr"] = np.abs(c-o)/a
    F["bar_range_atr"] = (h-l)/a
    sl = np.full((len(fr),6), np.nan)
    for j,col in enumerate(MA):
        x = fr[col].to_numpy(); sl[:,j] = (x-np.roll(x,5))/(5*a); sl[:5,j] = np.nan
    F["slope"] = sl.mean(1); F["slope_sd"] = sl.std(1)
    F["slope_fast_minus_slow"] = sl[:,[0,1]].mean(1)-sl[:,[4,5]].mean(1)
    F["pos"] = (c-mas.mean(1))/a
    F["dense_run"] = S((F["width"]<=1.0).astype(float)).groupby(
        (F["width"]>1.0).cumsum()).cumsum().to_numpy()
    t = pd.to_datetime(fr["open_time"], utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    if btc is not None:
        bt, bc = btc
        j = np.searchsorted(bt, t, side="right") - 1     # causal align: last closed BTC bar
        j = np.clip(j, 250, len(bc)-1)
        for k in (20,48,200):
            F[f"btc_ret_{k}"] = bc[j]/bc[np.clip(j-k,0,None)] - 1.0
        F["btc_trend_120"] = (bc[j]-bc[np.clip(j-120,0,None)])/np.abs(bc[j])
        F["rel_ret_48"] = F["ret_48"] - F["btc_ret_48"]
        F["rel_ret_200"] = F["ret_200"] - F["btc_ret_200"]
    return F, c, h, l, a, t, len(fr)


bp = sorted(Path("data/kline_fetched").glob("okx_BTC_USDT_SWAP_15m_*.csv"))[0]
bfr = pd.read_csv(bp).sort_values("ts").reset_index(drop=True)
BTC = (pd.to_datetime(bfr["open_time"], utc=True).dt.tz_convert(None)
       .to_numpy(dtype="datetime64[ns]"), bfr["close"].to_numpy())

cache, rows = {}, []
ALL = BASE+STRUCT+REGIME+MARKET+SHAPE
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
    d = bs + int(np.nanargmin(seg)) - OFFSET
    if d < 560 or d+HORIZON >= n or t[d] >= HOLD: continue
    x = {k: float(F[k][d]) for k in ALL}
    if not all(np.isfinite(list(x.values()))): continue
    atr, c0 = a[d], c[d]
    if not np.isfinite(atr) or atr <= 0: continue
    up, dn = c0+BARRIER*atr, c0-BARRIER*atr; y = None
    for i in range(d+1, d+HORIZON+1):
        if l[i] <= dn: y = 1; break
        if h[i] >= up: y = 0; break
    if y is None: continue
    x.update(y=y, symbol=sym, t=t[d]); rows.append(x)

D = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
print(f"样本 {len(D)}  向下先到 {D.y.mean()*100:.1f}%  币种 {D.symbol.nunique()}  决策点=最紧前{OFFSET}根")

from sklearn.ensemble import HistGradientBoostingClassifier
def auc(y,s):
    y=np.asarray(y); s=np.asarray(s)
    if y.sum() in (0,len(y)): return np.nan
    r=pd.Series(s).rank().to_numpy(); n1=int(y.sum())
    return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*(len(y)-n1)))
def cv(d, cols):
    out=[]
    for q in (0.45,0.55,0.65,0.75,0.85):
        cut=d.t.quantile(q); tr,va = d[d.t<=cut], d[d.t>cut]
        va = va[~va.symbol.isin(set(tr.symbol))].iloc[:400]
        if len(va)<60 or va.y.nunique()<2: continue
        m=HistGradientBoostingClassifier(max_iter=250,learning_rate=.06,max_depth=4,random_state=1)
        m.fit(tr[cols].to_numpy(), tr.y.to_numpy())
        out.append(auc(va.y.to_numpy(), m.predict_proba(va[cols].to_numpy())[:,1]))
    return np.nanmean(out) if out else np.nan

blocks=[("base 原基线",BASE),("+ struct 结构",BASE+STRUCT),
        ("+ regime 波动状态",BASE+STRUCT+REGIME),("+ market 市场因子",BASE+STRUCT+REGIME+MARKET),
        ("+ shape 形态",ALL)]
print(f"\n{'特征块':<22}{'个数':>5}{'AUC':>9}{'增量':>9}")
prev=None
for name,cols in blocks:
    v=cv(D,cols)
    print(f"{name:<22}{len(cols):>5}{v:>9.3f}" + (f"{v-prev:>+9.3f}" if prev is not None else "        —"))
    prev=v
print(f"\n{'只用 market':<22}{len(MARKET):>5}{cv(D,MARKET):>9.3f}")
print(f"{'只用 shape':<22}{len(SHAPE):>5}{cv(D,SHAPE):>9.3f}")

rng=np.random.default_rng(3); nl=[]
for s in range(15):
    d=D.copy(); r=np.random.default_rng(s)
    d["y"]=d.groupby("symbol").y.transform(lambda x: r.permutation(x.values))
    v=cv(d,ALL)
    if np.isfinite(v): nl.append(v)
print(f"\n打乱标签 null: 中位 {np.median(nl):.3f}  p90 {np.percentile(nl,90):.3f}")
json.dump({"n":len(D),"offset":OFFSET,"blocks":{n:float(cv(D,c)) for n,c in blocks},
           "market_only":float(cv(D,MARKET)),"shape_only":float(cv(D,SHAPE)),
           "null_median":float(np.median(nl))},
          open("analysis/output/l2_feature_expansion.json","w"), indent=1)

print()
print("=== 拿 base+struct 当过滤器，实际能把向下率提到多少 ===")
COLS = BASE + STRUCT
oof = np.full(len(D), np.nan)
for q in (0.45,0.55,0.65,0.75,0.85):
    cut=D.t.quantile(q); tr,va=D[D.t<=cut],D[D.t>cut]
    va=va[~va.symbol.isin(set(tr.symbol))].iloc[:400]
    if len(va)<60 or va.y.nunique()<2: continue
    m=HistGradientBoostingClassifier(max_iter=250,learning_rate=.06,max_depth=4,random_state=1)
    m.fit(tr[COLS].to_numpy(), tr.y.to_numpy())
    oof[va.index] = m.predict_proba(va[COLS].to_numpy())[:,1]
mask=np.isfinite(oof); s=oof[mask]; y=D.y.to_numpy()[mask]
print(f"样本外打分 {mask.sum()} 条，基准向下率 {y.mean()*100:.1f}%")
print(f"{'取分数最高的':<14}{'留下':>7}{'向下率':>9}{'提升':>9}")
for frac in (1.0,0.5,0.3,0.2,0.1):
    k=max(10,int(len(s)*frac)); idx=np.argsort(-s)[:k]
    print(f"{'全部' if frac==1 else f'{frac*100:.0f}%':<14}{k:>7}{y[idx].mean()*100:>8.1f}%"
          f"{(y[idx].mean()-y.mean())*100:>+8.1f}pp")
print()
print("反向（取分数最低的，应该更容易向上破）:")
for frac in (0.3,0.1):
    k=max(10,int(len(s)*frac)); idx=np.argsort(s)[:k]
    print(f"  最低 {frac*100:.0f}%  n={k}  向下率 {y[idx].mean()*100:.1f}%  (向上率 {(1-y[idx].mean())*100:.1f}%)")
