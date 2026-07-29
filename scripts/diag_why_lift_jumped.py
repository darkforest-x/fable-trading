"""Why did the same pool go from -17bp to +245bp? Isolate the two changes.

Measured twice today on judgment_yolo_owner_side_short_100_6m, same 47 features,
same CPCV, same pool:

    diag_judgment_big_pool     top-decile lift  -17.08bp
    diag_combo_reg_tponly      top-decile lift +245.00bp

A tenfold swing on identical data is a bug until proven otherwise, and the two
runs differ in exactly two ways:

  TARGET  binary classifier on the stored `label` versus a regressor on net
          return. This one has a reason to matter -- win rate is flat at
          36.2-37.7% across ATR quintiles while net spans fivefold, so the
          classifier is trained on the dimension carrying no information.
  FILTER  the second run requires a complete 72-bar horizon and drops rows whose
          window runs past the end of the data. That should be a small, boring
          sample change. If it moves the number sevenfold, it is selecting
          something -- most likely rows near the end of the period -- and the
          +245bp is a sampling artefact rather than a finding.

Runs the 2x2 with everything else pinned, then describes what the filter actually
removes, so the answer is not just "which cell" but "why".

Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_why_lift_jumped.py
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
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
HORIZON = 72
TOP_Q = 0.90
SEED = 20260729


def main() -> int:
    import argparse
    _ap = argparse.ArgumentParser(description=__doc__)
    _ap.add_argument("--pool", default=None,
                     help="candidate pool CSV; defaults to the tip_v1b 100x6m pool")
    _ap.add_argument("--tag", default=None, help="suffix for the output json")
    _a = _ap.parse_args()
    global POOL
    if _a.pool:
        POOL = Path(_a.pool) if Path(_a.pool).is_absolute() else PROJECT / _a.pool

    import lightgbm as lgb

    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].sort_values("t").reset_index(drop=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}

    print("① 标注每一行:它的 72 根窗口走完了吗…", flush=True)
    complete, bars_avail = [], []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = ((add_indicators(add_mas(load_series(series[key]))),)
                          if key in series else None)
        e = cache[sym]
        if e is None:
            for idx in grp.index:
                complete.append((idx, False)); bars_avail.append((idx, 0))
            continue
        ind = e[0]
        times = pd.to_datetime(ind["open_time"], utc=True)
        for idx, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            avail = len(ind) - (i + 1)
            complete.append((idx, 200 <= i < len(ind) - 2 and avail >= HORIZON))
            bars_avail.append((idx, avail))
    d["complete"] = pd.Series(dict(complete))
    d["bars_avail"] = pd.Series(dict(bars_avail))
    d["net"] = d["realized_ret"].astype(float) - SWAP_TAKER
    d["label_bin"] = (d["net"] > 0).astype(int)

    n_drop = int((~d["complete"]).sum())
    print(f"   总 {len(d)}   过滤会丢掉 {n_drop} 行 = {100*n_drop/len(d):.1f}%\n")

    print("② 被丢掉的是什么样的行 ===")
    kept, dropped = d[d["complete"]], d[~d["complete"]]
    for name, g in (("保留", kept), ("丢弃", dropped)):
        if g.empty:
            continue
        print(f"   {name} n={len(g):>6}  时间 {str(g['t'].min())[:10]}~{str(g['t'].max())[:10]}"
              f"  净收益中位 {g['net'].median()*1e4:+8.2f}bp  均值 {g['net'].mean()*1e4:+8.2f}bp")
    if len(dropped):
        late = (dropped["t"] > d["t"].quantile(0.9)).mean()
        print(f"   丢弃行里落在最后 10% 时间段的占比: {late*100:.1f}%"
              f"   ← 若接近 100%,过滤等于砍掉了期末")
    print()

    print("③ 2x2:{过滤开/关} x {分类器/回归器},其余全部固定", flush=True)
    base = d.copy()
    base, alpha_cols = attach_alphas(base)
    good = [c for c in alpha_cols if base[c].notna().mean() > 0.8]
    feats = [c for c in FEATURE_COLUMNS if c in base.columns] + good
    params = {"learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 80,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": SEED}

    def run(df: pd.DataFrame, kind: str) -> tuple[float, int, int]:
        df = df.reset_index(drop=True)
        lifts = []
        for tr_i, te_i in cpcv_groups(df, 6, 2):
            tr, te = df.iloc[tr_i], df.iloc[te_i]
            if kind in ("cls", "cls_csv"):
                col = "label" if kind == "cls_csv" and "label" in tr else "label_bin"
                if tr[col].nunique() < 2:
                    continue
                b = lgb.train({**params, "objective": "binary"},
                              lgb.Dataset(tr[feats].astype(float),
                                          label=tr[col].astype(int)), 250)
            else:
                b = lgb.train({**params, "objective": "regression"},
                              lgb.Dataset(tr[feats].astype(float),
                                          label=tr["net"].astype(float)), 250)
            s = b.predict(te[feats].astype(float))
            sel = te["net"].to_numpy()[s >= np.nanquantile(s, TOP_Q)]
            if len(sel) >= 30:
                lifts.append(float(sel.mean()) - float(te["net"].mean()))
        if not lifts:
            return float("nan"), 0, 0
        return float(np.median(lifts)) * 1e4, sum(1 for x in lifts if x > 0), len(lifts)

    print(f"\n{'样本':<12}{'目标':<20}{'顶档提升':>12}{'为正折数':>12}")
    rows = []
    for fname, df in (("全部", base), ("仅走满72根", base[base["complete"]])):
        for kind, kname in (("cls_csv", "二分类·CSV的label"),
                            ("cls", "二分类·净>0"), ("reg", "回归·净收益")):
            m, p, n = run(df, kind)
            rows.append({"sample": fname, "target": kname, "lift_bp": round(m, 2),
                         "pos": p, "folds": n, "n_rows": len(df)})
            print(f"{fname:<12}{kname:<20}{m:>+11.2f}bp{p:>8}/{n:<4}")

    by = {(r["sample"], r["target"]): r["lift_bp"] for r in rows}
    d_target = by[("全部", "回归·净收益")] - by[("全部", "二分类·CSV的label")]
    d_filter = by[("仅走满72根", "回归·净收益")] - by[("全部", "回归·净收益")]
    print(f"\n拆解:")
    print(f"  换目标(二分类→回归)贡献   {d_target:+8.2f}bp")
    print(f"  加过滤(全部→仅走满)贡献   {d_filter:+8.2f}bp")

    if abs(d_filter) > abs(d_target):
        verdict = (f"主因是【样本过滤】({d_filter:+.2f}bp vs 换目标 {d_target:+.2f}bp)。"
                   f"过滤丢掉 {100*n_drop/len(d):.1f}% 的行,而这些行的净收益中位 "
                   f"{dropped['net'].median()*1e4:+.2f}bp vs 保留行 "
                   f"{kept['net'].median()*1e4:+.2f}bp —— +245bp 里有很大一部分是选择偏差,"
                   f"不能当作发现。")
    else:
        verdict = (f"主因是【换目标】({d_target:+.2f}bp vs 过滤 {d_filter:+.2f}bp),"
                   f"与「边在幅度不在胜率」一致;过滤只是次要因素。")
    print(f"\n判读: {verdict}")
    print("注:训练池内,CPCV,未碰 holdout;此脚本只解释差异来源,不产生新结论。")

    (PROJECT / "analysis" / "output" /
     f"diag_why_lift_jumped{'_'+_a.tag if _a.tag else ''}.json").write_text(
        json.dumps({"pool": POOL.name, "n_total": len(d), "n_dropped": n_drop,
                    "grid": rows, "delta_target_bp": round(d_target, 2),
                    "delta_filter_bp": round(d_filter, 2), "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
