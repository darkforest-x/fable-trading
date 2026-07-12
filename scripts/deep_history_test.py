"""Deep-history one-shot test: the frozen pipeline evaluated ONCE on the
2021..2025-05 era that no selection step has ever touched.

PRE-REGISTERED before the data arrived (2026-07-12, forward log ~17 rows):
- data: data/kline_deep OKX *_USDT_SWAP 15m, CUT to open_time < 2025-06-01
  (strictly before our entire selection universe starts);
- pipeline: frozen bytes only -- expanded rules, FROZEN boosters + thresholds
  from models/frozen_*.json, NO retraining, NO parameter changes;
- crypto swaps only (tokenized equities excluded per the 07-12 rule);
- verdict criteria (aggregate over the era, maker 0.06%):
  PASS  = portfolio PF >= 1.3 AND net > 0 AND no calendar year with
          net < -5% on capital;
  MIXED = net > 0 but PF < 1.3 -> forward clock stays the judge;
  FAIL  = net <= 0 -> strategy is regime-fragile, stop and rethink.
- this script runs exactly once per freeze; rerunning after seeing results
  to "check something" is a discipline violation logged in PROJECT_STATUS.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from src.backtest.run import BAR, simulate, window_metrics
from src.data.loader import list_series, load_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate, label_candidate_scaled

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEEP_DIR = PROJECT_DIR / "data" / "kline_deep"
MODELS = PROJECT_DIR / "models"
OUT = PROJECT_DIR / "analysis" / "output" / "deep_history_test.json"
CUTOFF = pd.Timestamp("2025-06-01", tz="UTC")  # nothing at/after this is used
MAKER = 0.0006
STOCKISH = ("NFLX", "QQQ", "ORCL", "EWJ", "AAPL", "TSLA", "MSTR", "COIN", "NVDA",
            "META", "MSFT", "GOOGL", "AMZN", "SPY", "HOOD", "PLTR", "CRCL", "INTC")

LABELERS = {
    "tp5_sl2": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "scaled_25_t3": lambda f, i: label_candidate_scaled(f, i, tp1_mult=2.5, trail_mult=3.0),
}


def load_artifacts() -> dict[str, tuple[lgb.Booster, float]]:
    out = {}
    for meta_path in sorted(MODELS.glob("frozen_*.json")):
        meta = json.loads(meta_path.read_text())
        out[meta["config"]] = (lgb.Booster(model_file=str(meta_path.with_suffix(".txt"))),
                               float(meta.get("threshold_val_q90", meta.get("threshold", 0.5))))
    return out


def main() -> int:
    artifacts = load_artifacts()
    assert artifacts, "no frozen artifacts"
    pools: dict[str, list[dict]] = {k: [] for k in artifacts}
    n_series = 0
    for (source, symbol), paths in sorted(list_series(cache_dir=DEEP_DIR).items()):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        if symbol.split("_", 1)[0] in STOCKISH:
            continue
        frame = load_series(paths)
        frame = frame[frame["open_time"] < CUTOFF].reset_index(drop=True)
        if len(frame) < 5000:  # need real deep history, not a recent listing
            continue
        n_series += 1
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        featured = add_features(enriched)
        frows = extract_feature_rows(featured, idxs)
        for pos, si in enumerate(idxs):
            feats = frows.iloc[pos:pos + 1][FEATURE_COLUMNS]
            for config, (booster, thr) in artifacts.items():
                score = float(booster.predict(feats)[0])
                if score < thr:
                    continue
                o = LABELERS.get(config, LABELERS["tp5_sl2"])(enriched, si)
                if o is None:
                    continue
                st = enriched["open_time"].iloc[si]
                pools[config].append({
                    "source": source, "symbol": symbol, "signal_time": st,
                    "entry_time": st + BAR, "exit_time": st + BAR + o.exit_offset * BAR,
                    "score": score, "outcome": o.outcome, "realized_ret": o.realized_ret,
                    "year": st.year,
                })
    results = {"n_series": n_series, "cutoff": str(CUTOFF), "configs": {}}
    for config, rows in pools.items():
        if not rows:
            results["configs"][config] = {"note": "no signals"}
            continue
        df = pd.DataFrame(rows).sort_values(["entry_time", "score"], ascending=[True, False])
        trades = simulate(df, threshold=0.0)  # pool already threshold-filtered
        agg = window_metrics(trades, MAKER)
        years = {}
        for y, g in trades.groupby(trades["entry_time"].dt.year):
            years[int(y)] = window_metrics(g, MAKER)
            years[int(y)].pop("outcome_counts", None)
        agg.pop("outcome_counts", None)
        pf, net = agg.get("profit_factor", 0), agg.get("net_return_on_capital", 0)
        worst_year = min((m.get("net_return_on_capital", 0) for m in years.values()), default=0)
        verdict = ("PASS" if pf >= 1.3 and net > 0 and worst_year > -0.05
                   else "MIXED" if net > 0 else "FAIL")
        results["configs"][config] = {"aggregate": agg, "by_year": years, "verdict": verdict}
        print(config, "verdict:", verdict, "| PF", pf, "| net", net, flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
