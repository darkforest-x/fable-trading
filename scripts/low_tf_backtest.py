"""Low-timeframe judgment + portfolio backtest vs 15m mainline (discovery, val-only).

Question: mainline is 15m — does 1m / 2m / 3m / 5m beat it on the same
rules (expanded dense-MA) + TP5/SL2 + LightGBM, measured by top-decile net
and stage-3 concurrent portfolio metrics?

Data reality (local kline_fetched snapshot):
  1m: none
  2m: ~18 major SWAP series
  3m: BTC+ETH only
  5m: ~14 major SWAP
  15m: full pool (baseline uses majors that also exist on 2m for fairness)

Horizon is wall-clock matched to 15m × 72 bars ≈ 18h.
No holdout eval. No frozen/ACTIVE change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.backtest.run import ACCEPT_START, BASE_COST, COST_SWEEP, MAX_CONCURRENT, SCORE_QUANTILE, simulate, window_metrics
from src.data.bars import bar_to_timedelta
from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import LGB_PARAMS, evaluate, load_splits, permutation_pvalue, train_model

PROJECT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT / "data" / "low_tf_bt"
OUT_JSON = PROJECT / "analysis" / "output" / "low_tf_backtest.json"
OUT_MD = PROJECT / "analysis" / "p2b_low_tf_backtest_report.md"

# Wall-clock match: 15m * 72 = 18 hours
HORIZON_15M = 72
WALL_CLOCK_MIN = 15 * HORIZON_15M  # 1080

# Symbol universe = whatever 2m majors we have; 15m baseline restricted to same set when possible
MAJOR_LOW_TF = frozenset(
    {
        "BTC_USDT_SWAP",
        "ETH_USDT_SWAP",
        "SOL_USDT_SWAP",
        "BNB_USDT_SWAP",
        "XRP_USDT_SWAP",
        "DOGE_USDT_SWAP",
        "ADA_USDT_SWAP",
        "LINK_USDT_SWAP",
        "AVAX_USDT_SWAP",
        "TRX_USDT_SWAP",
        "LTC_USDT_SWAP",
        "DOT_USDT_SWAP",
        "ARB_USDT_SWAP",
        "OP_USDT_SWAP",
        "AAVE_USDT_SWAP",
        "ATOM_USDT_SWAP",
        "BCH_USDT_SWAP",
        "FIL_USDT_SWAP",
        "NEAR_USDT_SWAP",
    }
)


@dataclass(frozen=True)
class Cfg:
    bar: str
    horizon_bars: int
    note: str = ""

    @property
    def name(self) -> str:
        return f"{self.bar}_h{self.horizon_bars}"

    @property
    def wall_clock_hours(self) -> float:
        minutes = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15}[self.bar]
        return self.horizon_bars * minutes / 60.0


CONFIGS = (
    Cfg("1m", WALL_CLOCK_MIN // 1, "no local data expected"),
    Cfg("2m", WALL_CLOCK_MIN // 2),
    Cfg("3m", WALL_CLOCK_MIN // 3, "BTC/ETH only if present"),
    Cfg("5m", WALL_CLOCK_MIN // 5),
    Cfg("15m", HORIZON_15M, "majors baseline for fair TF compare"),
)


def _horizon_for(bar: str) -> int:
    minutes = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15}[bar]
    return max(12, WALL_CLOCK_MIN // minutes)


def build_pool(bar: str, horizon_bars: int) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    scanned = 0
    with_cand = 0
    symbols: list[str] = []
    min_bars = max(500, horizon_bars + 300)
    for source, symbol, frame in iter_series(bar=bar, min_bars=min_bars):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        # Low TF: use whatever series exist. 15m baseline: majors only (fair TF compare).
        if bar == "15m" and symbol not in MAJOR_LOW_TF:
            continue
        scanned += 1
        symbols.append(symbol)
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=horizon_bars, mode="expanded")
        if not idxs:
            continue
        with_cand += 1
        featured = add_features(enriched)
        feats = extract_feature_rows(featured, idxs)
        for row_pos, signal_i in enumerate(idxs):
            outcome = label_candidate(
                enriched, signal_i, tp_mult=5.0, sl_mult=2.0, horizon=horizon_bars
            )
            if outcome is None:
                continue
            entry_i = signal_i + 1
            maker_filled = bool(
                entry_i < len(enriched)
                and float(enriched["low"].iloc[entry_i]) < float(enriched["open"].iloc[entry_i])
            )
            rows.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "signal_i": int(signal_i),
                    "signal_time": enriched["open_time"].iloc[signal_i],
                    "label": outcome.label,
                    "outcome": outcome.outcome,
                    "exit_offset": outcome.exit_offset,
                    "entry_price": outcome.entry_price,
                    "realized_ret": outcome.realized_ret,
                    "maker_filled": maker_filled,
                    **feats.iloc[row_pos].to_dict(),
                }
            )
    coverage = {
        "bar": bar,
        "horizon_bars": horizon_bars,
        "series_scanned": scanned,
        "series_with_candidates": with_cand,
        "n_candidates": len(rows),
        "symbols": sorted(set(symbols)),
    }
    return pd.DataFrame(rows), coverage


def train_and_score(df: pd.DataFrame, bar: str, horizon_bars: int, objective: str = "binary") -> dict:
    path = OUT_DIR / f"pool_{bar}_h{horizon_bars}.csv"
    df = df.sort_values("signal_time").reset_index(drop=True)
    df.to_csv(path, index=False)
    train, val, _ = load_splits(path, horizon_bars=horizon_bars, bar=bar)
    if len(train) < 50 or len(val) < 20 or train["label"].nunique() < 2 or val["label"].nunique() < 2:
        return {
            "status": "skipped_insufficient",
            "n_candidates": int(len(df)),
            "n_train": int(len(train)),
            "n_val": int(len(val)),
            "dataset": str(path),
        }

    if objective == "regression":
        params = dict(LGB_PARAMS)
        params["objective"] = "regression"
        dtrain = lgb.Dataset(train[FEATURE_COLUMNS], label=train["realized_ret"])
        dval = lgb.Dataset(val[FEATURE_COLUMNS], label=val["realized_ret"], reference=dtrain)
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=600,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
    else:
        model = train_model(train, val)

    best_it = int(model.best_iteration or model.current_iteration())
    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=best_it)
    thr = float(np.quantile(val_scores, SCORE_QUANTILE))
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    metrics = evaluate(y, val_scores, rets)
    # score full history for portfolio sim
    full = df.copy()
    full["score"] = model.predict(full[FEATURE_COLUMNS], num_iteration=best_it)
    bar_td = bar_to_timedelta(bar)
    full["entry_time"] = pd.to_datetime(full["signal_time"], utc=True) + bar_td
    full["exit_time"] = full["entry_time"] + full["exit_offset"].astype(int) * bar_td
    full = full.sort_values(["entry_time", "score"], ascending=[True, False])
    trades = simulate(full, thr)
    accept = trades[trades["entry_time"] >= ACCEPT_START] if not trades.empty else trades
    insample = trades[trades["entry_time"] < ACCEPT_START] if not trades.empty else trades

    return {
        "status": "ok",
        "objective": objective,
        "dataset": str(path),
        "n_candidates": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "val_range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
        "pos_rate_val": round(float(val["label"].mean()), 4),
        "best_iteration": best_it,
        "threshold_val_q90": thr,
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(y, val_scores), 4),
        "top_decile": metrics["top_decile"],
        "top_net_0p2": metrics["top_decile"]["mean_net_ret"],
        "portfolio": {
            "n_eligible": int((full["score"] >= thr).sum()),
            "accept": {f"{c:.3f}": window_metrics(accept, c) for c in COST_SWEEP},
            "full": window_metrics(trades, BASE_COST),
            "insample_pre": window_metrics(insample, BASE_COST),
            "accept_checks_0p3": {
                "net_positive": window_metrics(accept, BASE_COST).get("net_total_units", 0) > 0,
                "pf_ge_1_3": window_metrics(accept, BASE_COST).get("profit_factor", 0) >= 1.3,
                "n_ge_100": window_metrics(accept, BASE_COST).get("n_trades", 0) >= 100,
            },
        },
    }


def write_report(payload: dict) -> None:
    lines = [
        "# 低周期回测：1m / 2m / 3m / 5m vs 15m\n\n",
        f"**日期**：{pd.Timestamp.utcnow().strftime('%Y-%m-%d')}  \n",
        "**纪律**：train/val only，未评估 holdout；未改 ACTIVE/冻结。  \n",
        "**设定**：规则 expanded 密集扫描 + TP5/SL2；horizon 墙钟对齐 15m×72≈18h；"
        "LightGBM binary；组合回测 10 仓帽、成本 0.2/0.3/0.4%。\n\n",
        "## 复现\n\n```bash\nPYTHONPATH=. python3 scripts/low_tf_backtest.py\n```\n\n",
        "## 数据覆盖\n\n",
        "| bar | horizon | 墙钟(h) | 扫描序列 | 有候选 | 候选数 | 备注 |\n|---|---:|---:|---:|---:|---:|---|\n",
    ]
    for cov in payload["coverage"]:
        note = ""
        if cov["series_scanned"] == 0:
            note = "无本地 K 线"
        lines.append(
            f"| {cov['bar']} | {cov['horizon_bars']} | {cov.get('wall_clock_hours', '?')} | "
            f"{cov['series_scanned']} | {cov['series_with_candidates']} | {cov['n_candidates']} | {note} |\n"
        )

    lines.append("\n## Val 排序指标（top-decile @0.2%）\n\n")
    lines.append(
        "| bar | status | val n | AUC | p | top-n | top 毛 | top 净@0.2% | top 胜率 |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    for r in payload["results"]:
        if r.get("status") != "ok":
            lines.append(
                f"| {r['bar']} | {r.get('status')} | {r.get('n_val', 0)} | — | — | — | — | — | — |\n"
            )
            continue
        td = r["top_decile"]
        lines.append(
            f"| {r['bar']} | ok | {r['n_val']} | {r['val_auc']:.4f} | {r['perm_p']:.3f} | "
            f"{td['n']} | {td['mean_realized_ret']:+.5f} | **{td['mean_net_ret']:+.5f}** | {td['win_rate']:.3f} |\n"
        )

    lines.append("\n## 组合回测（验收窗 ≥2026-05-04，成本 0.3%）\n\n")
    lines.append(
        "| bar | 合格 | 验收笔数 | 净/资金 | 净/笔 | 胜率 | PF | maxDD | ≥100笔 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---|\n"
    )
    for r in payload["results"]:
        if r.get("status") != "ok":
            lines.append(f"| {r['bar']} | — | — | — | — | — | — | — | — |\n")
            continue
        p = r["portfolio"]
        a = p["accept"]["0.003"]
        ok100 = "✓" if p["accept_checks_0p3"]["n_ge_100"] else "✗"
        lines.append(
            f"| {r['bar']} | {p['n_eligible']} | {a.get('n_trades', 0)} | "
            f"{a.get('net_return_on_capital', 0):+.2%} | {a.get('mean_net_per_trade', 0):+.4%} | "
            f"{a.get('win_rate', 0):.1%} | {a.get('profit_factor', 0):.2f} | "
            f"{a.get('max_drawdown_pct', 0):.2%} | {ok100} |\n"
        )

    lines.append("\n## 组合回测（全期 @0.3%）\n\n")
    lines.append("| bar | 笔数 | 净/资金 | PF | 胜率 |\n|---|---:|---:|---:|---:|\n")
    for r in payload["results"]:
        if r.get("status") != "ok":
            continue
        f = r["portfolio"]["full"]
        lines.append(
            f"| {r['bar']} | {f.get('n_trades', 0)} | {f.get('net_return_on_capital', 0):+.2%} | "
            f"{f.get('profit_factor', 0):.2f} | {f.get('win_rate', 0):.1%} |\n"
        )

    lines.append("\n## 解读\n\n")
    lines.append(payload.get("interpretation", "") + "\n")
    lines.append("\n## 风险与诚实声明\n\n")
    lines.append(
        "- 低周期本地数据覆盖远小于 15m 全池，**不能**与 YOLO 主线 401 币回测直接比绝对利润。\n"
        "- 15m 对照行限制在 majors，仅作同规则公平对照。\n"
        "- EMA 周期仍是 **bar 计数**（8/13/21…），不是固定分钟，细周期上「密集」语义会变。\n"
        "- 未评估 holdout；未切换主线 bar。\n"
        "- 1m 若无文件则跳过；3m 仅 2 币时样本极薄。\n"
    )
    OUT_MD.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    coverage = []
    results = []
    for cfg in CONFIGS:
        h = cfg.horizon_bars
        print(f"=== scanning {cfg.bar} h={h} ({cfg.wall_clock_hours:.1f}h) ===", flush=True)
        try:
            df, cov = build_pool(cfg.bar, h)
        except Exception as exc:  # noqa: BLE001
            cov = {
                "bar": cfg.bar,
                "horizon_bars": h,
                "wall_clock_hours": cfg.wall_clock_hours,
                "series_scanned": 0,
                "series_with_candidates": 0,
                "n_candidates": 0,
                "symbols": [],
                "error": str(exc),
            }
            coverage.append(cov)
            results.append({"bar": cfg.bar, "horizon_bars": h, "status": f"error:{exc}"})
            print(f"  error: {exc}", flush=True)
            continue
        cov["wall_clock_hours"] = cfg.wall_clock_hours
        coverage.append(cov)
        print(
            f"  scanned={cov['series_scanned']} with_cand={cov['series_with_candidates']} "
            f"n={cov['n_candidates']}",
            flush=True,
        )
        if df.empty or cov["series_scanned"] == 0:
            results.append(
                {
                    "bar": cfg.bar,
                    "horizon_bars": h,
                    "status": "no_data",
                    "n_candidates": 0,
                    "n_val": 0,
                }
            )
            continue
        if len(df) < 80:
            results.append(
                {
                    "bar": cfg.bar,
                    "horizon_bars": h,
                    "status": "too_few_candidates",
                    "n_candidates": int(len(df)),
                    "n_val": 0,
                }
            )
            continue
        print("  training + portfolio sim ...", flush=True)
        res = train_and_score(df, cfg.bar, h, objective="binary")
        res["bar"] = cfg.bar
        res["horizon_bars"] = h
        res["wall_clock_hours"] = cfg.wall_clock_hours
        results.append(res)
        if res.get("status") == "ok":
            a = res["portfolio"]["accept"]["0.003"]
            print(
                f"  AUC={res['val_auc']:.3f} top_net={res['top_net_0p2']:+.4f} "
                f"accept_n={a.get('n_trades')} PF={a.get('profit_factor')} "
                f"net/cap={a.get('net_return_on_capital')}",
                flush=True,
            )

    # interpretation
    ok = [r for r in results if r.get("status") == "ok"]
    base = next((r for r in ok if r["bar"] == "15m"), None)
    interp = []
    if not ok:
        interp.append("所有低周期配置均无有效结果（缺数据或样本不足）。")
    else:
        best = max(ok, key=lambda r: r.get("top_net_0p2") or -1e9)
        interp.append(
            f"按 val top 净@0.2% 最优：**{best['bar']}** "
            f"({best.get('top_net_0p2'):+.5f})。"
        )
        if base:
            for r in ok:
                if r["bar"] == "15m":
                    continue
                d = (r.get("top_net_0p2") or 0) - (base.get("top_net_0p2") or 0)
                interp.append(
                    f"- {r['bar']} vs 15m majors：top 净 Δ={d:+.5f}，"
                    f"验收笔数 {r['portfolio']['accept']['0.003'].get('n_trades')} "
                    f"vs {base['portfolio']['accept']['0.003'].get('n_trades')}。"
                )
        # 5m historical note
        five = next((r for r in ok if r["bar"] == "5m"), None)
        if five and (five.get("top_net_0p2") or 0) <= 0:
            interp.append("5m top 净未过 0.2% 成本线，与历史 H7 证伪一致。")
        two = next((r for r in ok if r["bar"] == "2m"), None)
        if two:
            interp.append(
                "2m 有数据但币种少、事件密、噪声大；若 top 净/组合 PF 不稳，"
                "不宜替代 15m 主线。"
            )

    payload = {
        "discipline": "val-only, no holdout; majors-limited 15m baseline; rules expanded TP5/SL2",
        "horizon_policy": "wall-clock match 15m*72 ≈ 18h",
        "costs": {"base_portfolio": BASE_COST, "top_decile_rt": 0.002},
        "max_concurrent": MAX_CONCURRENT,
        "coverage": coverage,
        "results": results,
        "interpretation": " ".join(interp),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "report": str(OUT_MD), "interpretation": payload["interpretation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
