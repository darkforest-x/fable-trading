"""Cross-sectional search for a gross directional edge (V1, all markets, 2y).

The ETH x V8 ledgers show no gross edge at any timeframe, so a martingale has no
base to stand on there. This asks whether the V1 signal has a gross edge anywhere
in the cross-section of venues/symbols/timeframes.

Multiple comparisons are the obvious trap: scanning N symbols guarantees some
positive cells by chance. So tests are run in a fixed order of increasing
granularity, and every family is Holm-corrected within itself.

Source: exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz
gross_r := gross_return / risk_fraction_at_entry  (R units, before fees)
cost_r  := |fees_return| / risk_fraction_at_entry
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
SRC = BASE / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz"
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
RNG = np.random.default_rng(20260915)
MIN_N = 30          # cells below this are reported but never tested
N_BOOT = 20000


def boot_mean(vals, months, n=N_BOOT):
    """Month-block bootstrap; returns mean, 95% CI, one-sided p for H0: mean<=0."""
    uniq = np.unique(months)
    if len(uniq) < 2:
        return float(vals.mean()), np.nan, np.nan, np.nan
    groups = [vals[months == m] for m in uniq]
    k = len(groups)
    means = np.empty(n)
    for b in range(n):
        means[b] = np.concatenate([groups[i] for i in RNG.integers(0, k, k)]).mean()
    return (float(vals.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)), float((means <= 0).mean()))


def holm(pvals):
    """Holm-Bonferroni adjusted p-values, order preserved."""
    p = np.asarray(pvals, dtype=float)
    ok = ~np.isnan(p)
    adj = np.full_like(p, np.nan)
    idx = np.where(ok)[0]
    order = idx[np.argsort(p[idx])]
    m = len(order)
    running = 0.0
    for rank, i in enumerate(order):
        val = (m - rank) * p[i]
        running = max(running, val)
        adj[i] = min(1.0, running)
    return adj


d = pd.read_csv(SRC)
d = d[~d["censored"].astype(bool)].copy()
d = d[d["risk_fraction_at_entry"] > 0].copy()
d["gross_r"] = d["gross_return"] / d["risk_fraction_at_entry"]
d["cost_r"] = d["fees_return"].abs() / d["risk_fraction_at_entry"]
d["_month"] = pd.to_datetime(d["entry_time"], utc=True, format="mixed").dt.strftime("%Y-%m")

print(f"账本 {len(d)} 笔 | {d['symbol'].nunique()} 标的 | 周期 {sorted(d['timeframe_min'].unique())}")
print(f"时间 {d['_month'].min()} -> {d['_month'].max()} | 场所 {sorted(d['venue'].unique())}")
print(f"中位 cost_R {d['cost_r'].median():.3f} | 中位止损距离 {d['risk_fraction_at_entry'].median()*100:.3f}%")
print()

report = {}

# --- Family 0: the whole pool, one test, no correction needed -------------
m, lo, hi, p = boot_mean(d["gross_r"].to_numpy(), d["_month"].to_numpy())
mn, lon, hin, pn = boot_mean(d["net_r"].to_numpy(), d["_month"].to_numpy())
print("=== 全池（1 次检验，无需校正）===")
print(f"毛R 均值 {m:+.4f}  95%CI [{lo:+.4f}, {hi:+.4f}]  单侧p {p:.4f}")
print(f"净R 均值 {mn:+.4f}  95%CI [{lon:+.4f}, {hin:+.4f}]  单侧p {pn:.4f}")
report["pool"] = {"n": len(d), "gross_mean": m, "gross_lo": lo, "gross_hi": hi, "gross_p": p,
                  "net_mean": mn, "net_lo": lon, "net_hi": hin, "net_p": pn}
print()

# --- Family 1: per timeframe, Holm within family --------------------------
rows = []
for tf, g in d.groupby("timeframe_min"):
    tested = len(g) >= MIN_N
    m, lo, hi, p = boot_mean(g["gross_r"].to_numpy(), g["_month"].to_numpy()) if tested else (float(g["gross_r"].mean()), np.nan, np.nan, np.nan)
    mn = float(g["net_r"].mean())
    rows.append({"周期min": tf, "笔数": len(g), "标的数": g["symbol"].nunique(),
                 "中位cost_R": float(g["cost_r"].median()),
                 "均值毛R": m, "毛R下界": lo, "毛R上界": hi, "毛R_p": p, "均值净R": mn})
tf_df = pd.DataFrame(rows)
tf_df["毛R_Holm_p"] = holm(tf_df["毛R_p"].to_numpy())
tf_df.to_csv(OUT / "cross_section_by_timeframe.csv", index=False)
print("=== 按周期（Holm 校正，family size = 已检验格子数）===")
print(tf_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print()

# --- Family 2: per symbol (pooled over timeframes), Holm within family -----
rows = []
for sym, g in d.groupby("symbol"):
    if len(g) < MIN_N:
        continue
    m, lo, hi, p = boot_mean(g["gross_r"].to_numpy(), g["_month"].to_numpy())
    rows.append({"标的": sym, "笔数": len(g), "中位cost_R": float(g["cost_r"].median()),
                 "均值毛R": m, "毛R下界": lo, "毛R上界": hi, "毛R_p": p,
                 "均值净R": float(g["net_r"].mean())})
sym_df = pd.DataFrame(rows).sort_values("均值毛R", ascending=False)
if len(sym_df):
    sym_df["毛R_Holm_p"] = holm(sym_df["毛R_p"].to_numpy())
    sym_df.to_csv(OUT / "cross_section_by_symbol.csv", index=False)
    print(f"=== 按标的（{len(sym_df)} 个达到 n>={MIN_N}，Holm 校正）===")
    print(sym_df.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print()
    surv = sym_df[(sym_df["毛R_Holm_p"] < 0.05) & (sym_df["均值毛R"] > 0)]
    print(f"Holm 校正后毛R显著为正的标的: {surv['标的'].tolist() if len(surv) else '无'}")
    report["symbols_tested"] = len(sym_df)
    report["symbols_surviving_holm"] = surv["标的"].tolist()
else:
    print(f"没有标的达到 n>={MIN_N}")
    report["symbols_tested"] = 0
    report["symbols_surviving_holm"] = []

report["min_n"] = MIN_N
report["n_boot"] = N_BOOT
report["holdout_reads"] = 0
(OUT / "cross_section_meta.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
