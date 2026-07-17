"""H13: BTC 1h regime as shared features on the SWAP 15m judgment pool.

Features (causal, no lookahead):
  - btc_ema55_slope: 1h EMA55 pct-change over 4 bars (~4h)
  - btc_atr_pctile: 1h ATR% rank in its trailing 168 bars (~1w)

Each 15m signal may only see 1h bars that have *fully closed* before the
signal open_time (available_at = open_time + 1h).

Train/val only. Outputs IC + single-variable net gain vs 28-feature baseline.
Does NOT edit features.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import iter_series, list_series, load_series
from src.data.universe import is_stockish
from src.factors.library import _safe
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import (
    HOLDOUT_START,
    LGB_PARAMS,
    evaluate,
    load_splits,
    permutation_pvalue,
)

OUT_JSON = PROJECT_DIR / "analysis/output/h13_btc_regime.json"
OUT_MD = PROJECT_DIR / "analysis/p2b_h13_btc_regime.md"
POOL_CSV = PROJECT_DIR / "data/h13_btc_regime_pool.csv"
SWAP_MAKER_COST = 0.0006
BTC_FEATURES = ("btc_ema55_slope", "btc_atr_pctile")


def _btc_1h_features() -> pd.DataFrame:
    groups = list_series(bar="1H")
    key = ("okx", "BTC_USDT_SWAP")
    if key not in groups:
        raise FileNotFoundError("BTC_USDT_SWAP 1H series not found in cache/fetched")
    raw = load_series(groups[key])
    if raw.empty:
        raise RuntimeError("BTC 1H frame empty")
    close = raw["close"].replace(0, np.nan)
    high, low = raw["high"], raw["low"]
    ema55 = close.ewm(span=55, adjust=False).mean()
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    atr_pct = atr / close
    # trailing percentile rank of atr_pct (causal)
    atr_pctile = atr_pct.rolling(168, min_periods=48).apply(
        lambda x: (np.argsort(np.argsort(x))[-1] + 1) / len(x), raw=True
    )
    out = pd.DataFrame(
        {
            "open_time": raw["open_time"],
            # bar [t, t+1h) is only known after close → available at t+1h
            "available_at": raw["open_time"] + pd.Timedelta(hours=1),
            "btc_ema55_slope": _safe(ema55.pct_change(4)),
            "btc_atr_pctile": _safe(atr_pctile),
        }
    )
    return out.dropna(subset=["available_at"]).sort_values("available_at").reset_index(drop=True)


def collect_pool(btc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        featured = add_features(enriched)
        feat_rows = extract_feature_rows(featured, idxs)
        close = enriched["close"].to_numpy()
        times = pd.to_datetime(enriched["open_time"], utc=True)
        for pos, si in enumerate(idxs):
            st = times.iloc[si]
            if st >= HOLDOUT_START:
                continue
            outcome = label_candidate(enriched, si, tp_mult=5.0, sl_mult=2.0)
            if outcome is None:
                continue
            ei = si + 1
            if ei + 72 >= len(close):
                continue
            fwd = close[min(ei + 72, len(close) - 1)] / close[ei] - 1
            rec = {
                "source": source,
                "symbol": symbol,
                "signal_time": st,
                "label": outcome.label,
                "realized_ret": outcome.realized_ret,
                "fwd_ret": fwd,
                "month": str(st)[:7],
            }
            rec.update(feat_rows.iloc[pos].to_dict())
            rows.append(rec)
    pool = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    # as-of join: last fully-closed BTC 1h bar
    merged = pd.merge_asof(
        pool.sort_values("signal_time"),
        btc.sort_values("available_at"),
        left_on="signal_time",
        right_on="available_at",
        direction="backward",
    )
    return merged


def factor_ic(df: pd.DataFrame, name: str) -> dict:
    sub = df[[name, "fwd_ret", "month"]].dropna()
    if len(sub) < 200:
        return {"factor": name, "n": len(sub), "note": "样本不足"}
    ic, _ = spearmanr(sub[name], sub["fwd_ret"])
    monthly = []
    for _, g in sub.groupby("month"):
        if len(g) >= 30:
            m_ic, _ = spearmanr(g[name], g["fwd_ret"])
            if np.isfinite(m_ic):
                monthly.append(m_ic)
    ir = (
        float(np.mean(monthly) / np.std(monthly))
        if len(monthly) >= 3 and np.std(monthly) > 0
        else 0.0
    )
    sign_stable = len(monthly) >= 3 and (np.mean(np.sign(monthly) == np.sign(ic)) >= 0.7)
    cls = (
        "alive"
        if abs(ic) >= 0.03 and sign_stable
        else "reversed"
        if abs(ic) >= 0.03 and not sign_stable
        else "dead"
    )
    return {
        "factor": name,
        "n": int(len(sub)),
        "ic": round(float(ic), 4),
        "ir": round(float(ir), 3),
        "n_months": len(monthly),
        "sign_stable": bool(sign_stable),
        "class": cls,
    }


def eval_featureset(train: pd.DataFrame, val: pd.DataFrame, cols: list[str]) -> dict:
    dtr = lgb.Dataset(train[cols], label=train["label"])
    dva = lgb.Dataset(val[cols], label=val["label"], reference=dtr)
    model = lgb.train(
        LGB_PARAMS,
        dtr,
        num_boost_round=600,
        valid_sets=[dva],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    prob = model.predict(val[cols], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    top_net_maker = round(m["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5)
    return {
        "auc": m["roc_auc"],
        "top_gross": m["top_decile"]["mean_realized_ret"],
        "top_net_maker_006": top_net_maker,
        "top_win_rate": m["top_decile"]["win_rate"],
        "p": round(permutation_pvalue(y, prob), 4),
        "n_val": int(len(val)),
    }


def main() -> int:
    print("loading BTC 1H features…", flush=True)
    btc = _btc_1h_features()
    print(f"  BTC 1H bars: {len(btc)}", flush=True)
    print("scanning SWAP 15m candidates…", flush=True)
    pool = collect_pool(btc)
    pool.to_csv(POOL_CSV, index=False)
    print(f"  pool n={len(pool)} symbols={pool['symbol'].nunique()}", flush=True)

    ic_rows = [factor_ic(pool, f) for f in BTC_FEATURES]
    for r in ic_rows:
        print(f"  IC {r.get('factor')}: {r.get('ic')} {r.get('class')}", flush=True)

    # single-var gain: need complete feature rows
    usable = pool.dropna(subset=list(FEATURE_COLUMNS) + list(BTC_FEATURES) + ["label", "realized_ret"])
    usable.to_csv(POOL_CSV, index=False)
    train, val, _ = load_splits(POOL_CSV, horizon_bars=72)
    base = eval_featureset(train, val, list(FEATURE_COLUMNS))
    gains = [{"featureset": "baseline_28", **base}]
    for f in BTC_FEATURES:
        r = eval_featureset(train, val, list(FEATURE_COLUMNS) + [f])
        r["gain_net_maker"] = round(r["top_net_maker_006"] - base["top_net_maker_006"], 5)
        gains.append({"featureset": f"+{f}", **r})
        print(
            f"  +{f}: auc={r['auc']} top_net_maker={r['top_net_maker_006']:+.5f} "
            f"gain={r['gain_net_maker']:+.5f} p={r['p']}",
            flush=True,
        )
    both = eval_featureset(train, val, list(FEATURE_COLUMNS) + list(BTC_FEATURES))
    both["gain_net_maker"] = round(both["top_net_maker_006"] - base["top_net_maker_006"], 5)
    gains.append({"featureset": "+both_btc", **both})

    payload = {
        "n_pool": int(len(pool)),
        "n_usable": int(len(usable)),
        "n_symbols": int(pool["symbol"].nunique()),
        "ic": ic_rows,
        "gains": gains,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    alive = [r for r in ic_rows if r.get("class") == "alive"]
    winners = [r for r in gains[1:] if r.get("gain_net_maker", 0) > 0 and r.get("p", 1) < 0.01]
    md = [
        "# H13 BTC 大盘状态共享特征（SWAP 池, train/val）\n",
        "**日期**：2026-07-15  ",
        "**纪律**：发现级 val-only；holdout 未碰；未改 `features.py` / 冻结模型。  ",
        "**特征**：BTC_USDT_SWAP **1h** EMA55 斜率（4 根 pct_change）+ 1h ATR% 分位（168 根滚动秩）。  ",
        "因果：1h bar 仅在收盘后可用（`available_at = open_time + 1h`），`merge_asof` 后向对齐 15m 信号。\n",
        "## 复现命令\n",
        "```bash",
        "PYTHONPATH=. python3 scripts/h13_btc_regime.py",
        "```\n",
        "## 数据统计\n",
        f"- 候选池（含特征对齐前）：{len(pool)} / 币种 {pool['symbol'].nunique()}",
        f"- 可用（28 维 + BTC 特征非空）：{len(usable)}",
        f"- train/val：{len(train)} / {len(val)}（load_splits 标准 purge）",
        f"- BTC 1h bars：{len(btc)}\n",
        "## IC（因子 vs 72-bar 前向 close ret）\n",
        "| 因子 | n | IC | IR | 月数 | 符号稳定 | 分类 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for r in ic_rows:
        if "ic" in r:
            md.append(
                f"| {r['factor']} | {r['n']} | {r['ic']:+.4f} | {r['ir']:+.2f} | "
                f"{r['n_months']} | {'✓' if r['sign_stable'] else '✗'} | {r['class']} |"
            )
    md += [
        "",
        f"IC 存活（|IC|≥0.03 且符号稳定）：{', '.join(r['factor'] for r in alive) or '（无）'}\n",
        "## 单变量净增益（val，top-decile 净@maker 0.06%）\n",
        f"基线 28 维 top 净@maker：{base['top_net_maker_006']:+.5f}（AUC {base['auc']}，p={base['p']}）\n",
        "| 特征集 | val AUC | p | top 净@maker | 相对基线增益 | top 胜率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in gains:
        g = f"{r.get('gain_net_maker', 0):+.5f}" if "gain_net_maker" in r else "—"
        md.append(
            f"| {r['featureset']} | {r['auc']} | {r['p']} | {r['top_net_maker_006']:+.5f} | {g} | {r['top_win_rate']} |"
        )
    md += [
        "",
        f"## 判定\n",
        f"- IC 候选：{len(alive)} 个",
        f"- 单变量增益>0 且 p<0.01：{', '.join(r['featureset'] for r in winners) or '（无）'}",
        "",
        "### 总判定",
        (
            "**通过（发现级）**：至少一特征 IC 存活或单变量净增益显著为正。"
            if alive or winners
            else "**未通过**：IC 未过线且单变量净@maker 无稳定增益。不并入主线特征表。"
        ),
        "",
        "## 解读\n",
        "- BTC 1h 状态是全市场共享状态变量；若 IC/增益弱，说明密集启动池内个币特异性已主导标签，",
        "  或 15m 信号时刻的大盘状态已被 `slow_slope` / `atr_pct` 等本地特征部分吸收。",
        "- 两特征同时加入用于探测交互，不作主线候选（多变量打包需 owner 批准）。\n",
        "## 风险与诚实声明\n",
        "- val 已多次用于选型，数字只排序不宣称绩效；",
        "- ATR 分位用 rolling apply，计算偏慢但完全因果；",
        "- 未使用真 funding / OI 等合约大盘状态；",
        "- 未碰 holdout、冻结模型、forward_log。\n",
        "## 下一步\n",
        "1. 默认 **不** 把 BTC 特征写入 `features.py`。",
        "2. * 若 owner 想保留弱增益特征，单变量门通过后再冻结新工件。",
        "3. 可后续试 BTC 4h/日线更慢状态，或 BTC 与 alt 的 beta 残差。",
    ]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
