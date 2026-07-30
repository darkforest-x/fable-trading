"""v3 stack portfolio backtest on the SWAP mainline (val window only).

Grid: {tp5_sl2, scaled_25_t3} x {all signals, maker-filled only}
      x {no filter, 1h-EMA120 filter} at costs {0.16%, 0.06%}.
Reuses the stage-3 simulator (per-symbol lock, 10-slot cap, val-q90
threshold fixed ex ante). Discovery tier: acceptance window untouched,
forward data remains the judge.

Also renders src/webapp/static/v3_backtest.html for the dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]

from src.backtest.run import BAR, simulate, window_metrics  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.judgment.candidates import add_indicators, scan_candidates  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import label_candidate, label_candidate_scaled  # noqa: E402
from src.judgment.train import load_splits, train_model  # noqa: E402
from src.judgment.trend_filter import add_h9_flags  # noqa: E402

OUT_JSON = PROJECT_DIR / "analysis" / "output" / "v3_portfolio_sim.json"
OUT_HTML = PROJECT_DIR / "src" / "webapp" / "static" / "v3_backtest.html"
POOL_DIR = PROJECT_DIR / "data" / "sweep_v3_portfolio"
COSTS = {"maker016": 0.0016, "swap006": 0.0006}
CONFIGS = {
    "tp5_sl2": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "scaled_25_t3": lambda f, i: label_candidate_scaled(f, i, tp1_mult=2.5, trail_mult=3.0),
}


def build_pools() -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict]] = {k: [] for k in CONFIGS}
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        featured = add_features(enriched)
        frows = extract_feature_rows(featured, idxs)
        opens, lows = enriched["open"].to_numpy(), enriched["low"].to_numpy()
        for pos, si in enumerate(idxs):
            ei = si + 1
            mf = bool(ei < len(lows) and lows[ei] < opens[ei])
            feats = frows.iloc[pos].to_dict()
            for name, labeler in CONFIGS.items():
                o = labeler(enriched, si)
                if o is None:
                    continue
                records[name].append({
                    "source": source, "symbol": symbol,
                    "signal_time": enriched["open_time"].iloc[si], "maker_filled": mf,
                    "label": o.label, "outcome": o.outcome, "exit_offset": o.exit_offset,
                    "entry_price": o.entry_price, "realized_ret": o.realized_ret, **feats,
                })
    return {k: pd.DataFrame(v).sort_values("signal_time").reset_index(drop=True)
            for k, v in records.items()}


def main() -> int:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for name, df in build_pools().items():
        path = POOL_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        train, val, _ = load_splits(path, horizon_bars=72)  # holdout untouched
        model = train_model(train, val)
        val = val.copy()
        val["score"] = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        thr = float(np.quantile(val["score"], 0.90))
        val = add_h9_flags(val)
        val["entry_time"] = val["signal_time"] + BAR
        val["exit_time"] = val["entry_time"] + val["exit_offset"] * BAR
        val = val.sort_values(["entry_time", "score"], ascending=[True, False])
        variants = {
            "raw": val,
            "maker": val[val["maker_filled"]],
            "maker+h9": val[val["maker_filled"] & val["h1_above_ma"] & val["h1_ok"]],
        }
        for vname, pool in variants.items():
            trades = simulate(pool, thr)
            for cname, cost in COSTS.items():
                m = window_metrics(trades, cost)
                results.append({
                    "config": name, "variant": vname, "cost": cname,
                    "n_trades": m.get("n_trades", 0),
                    "net_pct": round(100 * m.get("net_return_on_capital", 0), 2),
                    "pf": m.get("profit_factor", 0),
                    "maxdd_pct": round(100 * m.get("max_drawdown_pct", 0), 2),
                    "win": m.get("win_rate", 0),
                })
                print(results[-1], flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    rows_html = "".join(
        f"<tr><td>{r['config']}</td><td>{r['variant']}</td><td>{r['cost']}</td>"
        f"<td class='num'>{r['n_trades']}</td><td class='num {'pos' if r['net_pct'] > 0 else 'neg'}'>{r['net_pct']}%</td>"
        f"<td class='num {'pos' if r['pf'] >= 1.3 else ''}'>{r['pf']}</td>"
        f"<td class='num'>{r['maxdd_pct']}%</td><td class='num'>{round(100 * r['win'], 1)}%</td></tr>"
        for r in results)
    OUT_HTML.write_text(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>v3 组合回测 · SWAP 主线</title><style>
body{{background:#131519;color:#e8e9eb;font-family:"PingFang SC",system-ui,sans-serif;padding:24px;line-height:1.7}}
h1{{font-size:20px}} p{{color:#9aa0a8;font-size:13.5px;max-width:60em}}
table{{border-collapse:collapse;margin-top:14px;font-size:13.5px}}
th,td{{padding:6px 14px;border-bottom:1px solid #2e3340;text-align:left}}
th{{color:#9aa0a8;font-weight:400}} .num{{text-align:right;font-family:ui-monospace,Menlo,monospace}}
.pos{{color:#1fa77d}} .neg{{color:#e66767}}
</style></head><body>
<h1>v3 优化叠加后的组合回测（SWAP 主线 · val 窗口）</h1>
<p>生成于 {datetime.now():%Y-%m-%d %H:%M}。历史参照：v2 现货 taker 验收窗口 PF <b>1.01</b>；
v3 现货 maker val PF <b>1.27</b>。本表为发现级（val 已多轮使用），最终裁决以 8 月初前向数据为准。
variant 说明：raw=全部信号按该成本；maker=仅 maker 可成交单；maker+h9=再叠加 1h EMA120 顺势过滤。
成本：maker016=0.16% 往返（保守）；swap006=0.06% 往返（合约 maker）。PF≥1.3 标绿。</p>
<table><thead><tr><th>出场结构</th><th>执行变体</th><th>成本</th><th>笔数</th>
<th>净收益(对资金)</th><th>PF</th><th>maxDD</th><th>胜率</th></tr></thead>
<tbody>{rows_html}</tbody></table></body></html>""", encoding="utf-8")
    print(f"wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
