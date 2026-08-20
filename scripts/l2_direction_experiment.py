"""L2, asked properly: at the decision bar, is the break direction predictable?

Today's trajectory study showed the four quality grades are geometrically
indistinguishable until the box ends, and separate only by which way price then
left. That was four features and median curves. Before concluding that L2 cannot
filter on geometry, the question deserves a real attempt: a wide causal feature
set, a mechanical target, and a model that can find interactions.

Target is first-touch, not owner's grade. Owner's grade is the thing suspected of
encoding the outcome, so training on it would prove nothing. Instead: after the
box ends, does price reach -1.5 ATR before +1.5 ATR, within HORIZON bars.

The decision bar is the tightest bar inside the box, not box_end. box_end is
drawn by owner after the move has started -- the trajectory study shows price is
already 2 ATR clear of the cluster there -- so a snapshot at box_end predicts a
break that has already happened. The tightest bar is where price is still inside
the band for every quality grade, and it is where the live detector's box sits.

Split is symbol-disjoint and time-ordered (C6), and a within-symbol label shuffle
gives the null, because a 0.55 AUC means nothing without knowing what chance
looks like on this many correlated samples.
"""
from __future__ import annotations
import json, re, sys, collections
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
from src.detection.data import add_mas

MA = ["sma20","ema20","sma60","ema60","sma120","ema120"]
HOLD = np.datetime64("2026-05-04T00:00:00")
HORIZON, BARRIER = 48, 1.5
EV = "/Users/zhangzc/yolo-xx/reports/pattern_event_v3/pattern_events.jsonl"

files = {}
for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
    m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
    if m: files[m.group(1)] = p


def prep(path):
    fr = add_mas(pd.read_csv(path).sort_values("ts").reset_index(drop=True))
    c = fr["close"].to_numpy(); h = fr["high"].to_numpy(); lo_ = fr["low"].to_numpy()
    mas = fr[MA].to_numpy()
    hi_ma, lo_ma = mas.max(1), mas.min(1)
    tr = pd.concat([fr["high"]-fr["low"], (fr["high"]-fr["close"].shift()).abs(),
                    (fr["low"]-fr["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().to_numpy()
    a = np.where(atr > 0, atr, np.nan)
    v = fr["volume"].to_numpy()
    F = {}
    F["width"] = (hi_ma - lo_ma) / a
    F["pos"] = (c - mas.mean(1)) / a
    sl = np.full((len(fr), 6), np.nan)
    for j, col in enumerate(MA):
        x = fr[col].to_numpy(); sl[:, j] = (x - np.roll(x, 5)) / (5 * a); sl[:5, j] = np.nan
    F["slope"] = sl.mean(1); F["slope_sd"] = sl.std(1)
    F["slope_fast_minus_slow"] = sl[:, [0,1]].mean(1) - sl[:, [4,5]].mean(1)
    for k in (5, 10, 20):
        w = pd.Series(F["width"])
        F[f"width_ratio_{k}"] = (w / w.shift(k)).to_numpy()
        F[f"ret_{k}"] = c / np.roll(c, k) - 1.0
    for k in (48, 120, 200):                       # where we sit in the bigger picture
        rh = pd.Series(h).rolling(k).max().to_numpy()
        rl = pd.Series(lo_).rolling(k).min().to_numpy()
        F[f"range_pos_{k}"] = (c - rl) / np.where(rh - rl > 0, rh - rl, np.nan)
        F[f"trend_{k}"] = (c - np.roll(c, k)) / (a * np.sqrt(k))
    F["atr_pct"] = a / c
    F["atr_ratio_50"] = a / pd.Series(a).rolling(50).mean().to_numpy()
    F["vol_z20"] = ((v - pd.Series(v).rolling(20).mean().to_numpy())
                    / (pd.Series(v).rolling(20).std().to_numpy() + 1e-9))
    F["vol_ratio_5_20"] = (pd.Series(v).rolling(5).mean().to_numpy()
                           / (pd.Series(v).rolling(20).mean().to_numpy() + 1e-9))
    F["dense_run"] = pd.Series((F["width"] <= 1.0).astype(float)).groupby(
        (F["width"] > 1.0).cumsum()).cumsum().to_numpy()
    F["above_all"] = (c > hi_ma).astype(float); F["below_all"] = (c < lo_ma).astype(float)
    t = pd.to_datetime(fr["open_time"], utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    return F, c, h, lo_, a, t, len(fr)


FEATS = None
rows = []
cache = {}
for line in open(EV):
    e = json.loads(line)
    if e["source"] != "golden_pool": continue
    sym = e["symbol"]
    if sym not in files: continue
    if sym not in cache: cache[sym] = prep(files[sym])
    F, c, h, lo_, a, t, n = cache[sym]
    bs, be = e["original_box"]["box_start_i"], e["original_box"]["box_end_i"]
    if bs is None or be is None or bs < 220 or be + HORIZON >= n: continue
    seg = F["width"][bs:be+1]
    if not np.isfinite(seg).any(): continue
    b1 = bs + int(np.nanargmin(seg))          # decision bar = tightest bar in the box
    if b1 + HORIZON >= n: continue
    if t[b1] >= HOLD: continue
    if FEATS is None: FEATS = sorted(F)
    x = {k: float(F[k][b1]) for k in FEATS}
    if not all(np.isfinite(list(x.values()))): continue
    atr = a[b1]; c0 = c[b1]
    if not np.isfinite(atr) or atr <= 0: continue
    up, dn = c0 + BARRIER*atr, c0 - BARRIER*atr
    y = None
    for i in range(b1+1, b1+HORIZON+1):
        hit_dn, hit_up = lo_[i] <= dn, h[i] >= up
        if hit_dn and hit_up: y = 1; break        # same bar: conservative, count as down
        if hit_dn: y = 1; break
        if hit_up: y = 0; break
    if y is None: continue
    x.update(y=y, symbol=sym, t=t[b1], grade=e.get("quality_label"))
    rows.append(x)

D = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
print(f"样本 {len(D)}  向下先到 {int(D.y.sum())} ({D.y.mean()*100:.1f}%)  币种 {D.symbol.nunique()}")
print(f"特征 {len(FEATS)} 个  目标: 框结束后 {HORIZON} 根内先触 ±{BARRIER} ATR 的哪一边")
print("按等级的向下率:", {k: round(float(v),3) for k,v in D.groupby(D.grade.fillna('ungraded')).y.mean().items()})

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

def auc(y, s):
    y = np.asarray(y); s = np.asarray(s)
    if y.sum() in (0, len(y)): return np.nan
    r = pd.Series(s).rank().to_numpy(); n1 = int(y.sum())
    return float((r[y==1].sum() - n1*(n1+1)/2) / (n1*(len(y)-n1)))

def run(D, shuffle=False, seed=0):
    rng = np.random.default_rng(seed)
    d = D.copy()
    if shuffle:
        d["y"] = d.groupby("symbol").y.transform(lambda s: rng.permutation(s.values))
    out = []
    for q in (0.45, 0.55, 0.65, 0.75, 0.85):
        cut = d.t.quantile(q)
        tr, va = d[d.t <= cut], d[d.t > cut]
        va = va[~va.symbol.isin(set(tr.symbol))]
        va = va.iloc[:400]
        if len(va) < 60 or va.y.nunique() < 2: continue
        X, Xv = tr[FEATS].to_numpy(), va[FEATS].to_numpy()
        m = HistGradientBoostingClassifier(max_iter=250, learning_rate=.06,
                                           max_depth=4, random_state=1)
        m.fit(X, tr.y.to_numpy())
        gb = auc(va.y.to_numpy(), m.predict_proba(Xv)[:,1])
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=.3))
        lr.fit(X, tr.y.to_numpy())
        out.append((gb, auc(va.y.to_numpy(), lr.predict_proba(Xv)[:,1]), len(tr), len(va)))
    return out

real = run(D)
print(f"\n{'fold':>5}{'n_tr':>7}{'n_va':>7}{'GBDT AUC':>11}{'Logistic':>11}")
for i,(g,l,a_,b_) in enumerate(real,1): print(f"{i:>5}{a_:>7}{b_:>7}{g:>11.3f}{l:>11.3f}")
mg = np.nanmean([r[0] for r in real]); ml = np.nanmean([r[1] for r in real])
print(f"{'mean':>5}{'':>7}{'':>7}{mg:>11.3f}{ml:>11.3f}")

null = [np.nanmean([x[0] for x in run(D, shuffle=True, seed=s)]) for s in range(30)]
null = np.array([x for x in null if np.isfinite(x)])
print(f"\n打乱标签对照 30 次: 中位 {np.median(null):.3f}  p90 {np.percentile(null,90):.3f}  最大 {null.max():.3f}")
print(f"实测 {mg:.3f} 在 null 中的分位 -> p = {(null >= mg).mean():.3f}")

m = HistGradientBoostingClassifier(max_iter=250, learning_rate=.06, max_depth=4, random_state=1)
m.fit(D[FEATS].to_numpy(), D.y.to_numpy())
from sklearn.inspection import permutation_importance
pi = permutation_importance(m, D[FEATS].to_numpy(), D.y.to_numpy(), n_repeats=5,
                            random_state=1, scoring="roc_auc")
print("\n特征重要性（置换，前 10）:")
for i in np.argsort(-pi.importances_mean)[:10]:
    print(f"  {FEATS[i]:<24}{pi.importances_mean[i]:+.4f}")
json.dump({"n": len(D), "down_rate": float(D.y.mean()), "features": FEATS,
           "gbdt_cv_auc": [float(r[0]) for r in real],
           "logistic_cv_auc": [float(r[1]) for r in real],
           "null_median": float(np.median(null)), "null_p90": float(np.percentile(null,90)),
           "p_value": float((null >= mg).mean()), "horizon": HORIZON, "barrier": BARRIER},
          open("analysis/output/l2_direction_experiment.json","w"), indent=1)
