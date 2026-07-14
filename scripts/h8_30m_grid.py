"""H8 follow-up: 30m SWAP grid TP{4,5,6} x horizon{48,60,72}, val-only.

Confirms whether h60 is stably optimal. Small-n: report confidence caveat.
Reuses mtf_sweep cost/split discipline (bar-aware purge, maker 0.06%).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

SWEEP_DIR = PROJECT_DIR / "data" / "sweep_h8_30m_grid"
OUT_JSON = PROJECT_DIR / "analysis/output/h8_30m_grid.json"
OUT_MD = PROJECT_DIR / "analysis/p2b_h8_30m_grid.md"
SWAP_MAKER_COST = 0.0006
BAR = "30m"
TPS = (4.0, 5.0, 6.0)
HORIZONS = (48, 60, 72)


def run_cell(tp: float, horizon: int) -> dict:
    tag = f"30m_tp{int(tp)}_h{horizon}"
    rows = []
    n_series = 0
    n_with = 0
    for source, symbol, frame in iter_series(bar=BAR, min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        n_series += 1
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=horizon, mode="expanded")
        if not idxs:
            continue
        n_with += 1
        featured = add_features(enriched)
        feats = extract_feature_rows(featured, idxs)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for pos, si in enumerate(idxs):
            o = label_candidate(enriched, si, tp_mult=tp, sl_mult=2.0, horizon=horizon)
            if o is None:
                continue
            entry_i = si + 1
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            rows.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "signal_time": enriched["open_time"].iloc[si],
                    "maker_filled": maker_filled,
                    "label": o.label,
                    "outcome": o.outcome,
                    "exit_offset": o.exit_offset,
                    "realized_ret": o.realized_ret,
                    **feats.iloc[pos].to_dict(),
                }
            )
    if len(rows) < 200:
        return {"config": tag, "n": len(rows), "n_series": n_series, "note": "pool too small"}
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    path = SWEEP_DIR / f"{tag}.csv"
    df.to_csv(path, index=False)
    train, val, _ = load_splits(path, horizon_bars=horizon, bar=BAR)
    if len(val) < 80 or train["label"].nunique() < 2 or val["label"].nunique() < 2:
        return {
            "config": tag,
            "n": int(len(df)),
            "n_series": n_series,
            "n_val": int(len(val)),
            "note": "val too small / single class",
        }
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y, rets = val["label"].to_numpy(), val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    return {
        "config": tag,
        "tp": tp,
        "horizon": horizon,
        "n_series": n_series,
        "n_series_with_cand": n_with,
        "n": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "val_auc": m["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": m["top_decile"]["mean_realized_ret"],
        "top_net_maker_006": round(m["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5),
        "top_win_rate": m["top_decile"]["win_rate"],
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "confidence": "low" if len(val) < 400 else "medium",
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for tp in TPS:
        for h in HORIZONS:
            r = run_cell(tp, h)
            results.append(r)
            print(r, flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    scored = [r for r in results if "top_net_maker_006" in r]
    scored_sorted = sorted(scored, key=lambda r: -r["top_net_maker_006"])
    best = scored_sorted[0] if scored_sorted else {}
    # is h60 best on average across TP?
    by_h = {}
    for r in scored:
        by_h.setdefault(r["horizon"], []).append(r["top_net_maker_006"])
    h_mean = {h: float(np.mean(v)) for h, v in by_h.items()}
    best_h = max(h_mean, key=h_mean.get) if h_mean else None

    lines = [
        "# H8 后续：30m 网格 TP{4,5,6}×horizon{48,60,72}\n",
        "**日期**：2026-07-15  ",
        "**纪律**：val-only；SWAP 30m；maker 成本 0.06%；holdout 未碰。  ",
        "**置信度：低–中**（val 样本通常 <400，top-decile 仅数十笔，数字噪声大）。\n",
        "## 复现命令\n",
        "```bash",
        "PYTHONPATH=. python3 scripts/h8_30m_grid.py",
        "```\n",
        "## 全网格结果\n",
        "| 配置 | n | n_val | AUC | p | top 净@maker | top 胜率 | 置信度 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        if "val_auc" not in r:
            lines.append(f"| {r['config']} | {r.get('n')} | {r.get('n_val','—')} | — | — | — | — | {r.get('note')} |")
            continue
        lines.append(
            f"| {r['config']} | {r['n']} | {r['n_val']} | {r['val_auc']} | {r['perm_p']} | "
            f"{r['top_net_maker_006']:+.5f} | {r['top_win_rate']:.2%} | {r['confidence']} |"
        )
    lines += [
        "",
        "## 按 horizon 平均 top 净@maker\n",
        "| horizon | mean top 净@maker |",
        "|---:|---:|",
    ]
    for h in HORIZONS:
        if h in h_mean:
            lines.append(f"| {h} | {h_mean[h]:+.5f} |")
    lines += [
        "",
        f"单格最优：`{best.get('config','—')}` 净@maker={best.get('top_net_maker_006', float('nan')):+.5f}",
        f"horizon 均值最优：**h{best_h}**" if best_h else "",
        "",
        f"## 判定：h60 是否稳定最优？\n",
        (
            f"**是（在均值意义下）**：h60 的跨 TP 平均净最高。"
            if best_h == 60
            else f"**否/不稳定**：跨 TP 平均最优 horizon 为 **h{best_h}**，单格最优为 `{best.get('config')}`。"
        ),
        "",
        "## 风险与诚实声明\n",
        "- 样本小，禁止把 30m 数字与 15m 主线直接比绝对值后切主线；",
        "- top-decile 在 n_val≈300 时只有 ~30 笔，胜率抖动 ±10pp 正常；",
        "- 未碰 holdout / 冻结 15m 主线。\n",
        "## 下一步\n",
        "30m 仍为低频高质量线索；若继续，需扩币种或更长历史再重跑网格。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
