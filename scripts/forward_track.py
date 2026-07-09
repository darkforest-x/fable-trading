"""Forward tracker (P1-6): score fresh SWAP candidates with the FROZEN
artifacts and append signals to data/forward_log.csv. Deterministically
reconstructible from stored klines, so it is safe to rerun any time;
dedupe key = (config, symbol, signal_time).

Forward era starts at FORWARD_START (the freeze date). Outcomes are filled
in once enough bars exist; until then rows carry outcome="open" and get
updated in place on later runs.

Run daily after src.data.update_okx (manually or via the owner-approved
scheduled task).
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate, label_candidate_scaled

PROJECT_DIR = Path(__file__).resolve().parents[1]
MODELS = PROJECT_DIR / "models"
LOG = PROJECT_DIR / "data" / "forward_log.csv"
FORWARD_START = pd.Timestamp("2026-07-09 00:00:00", tz="UTC")

LABELERS = {
    "tp5_sl2": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "scaled_25_t3": lambda f, i: label_candidate_scaled(f, i, tp1_mult=2.5, trail_mult=3.0),
}


def load_artifacts() -> dict[str, tuple[lgb.Booster, float]]:
    out = {}
    for meta_path in sorted(MODELS.glob("frozen_*.json")):
        meta = json.loads(meta_path.read_text())
        booster = lgb.Booster(model_file=str(meta_path.with_suffix(".txt")))
        out[meta["config"]] = (booster, meta["threshold_val_q90"])
    return out


def main() -> int:
    artifacts = load_artifacts()
    if not artifacts:
        print("no frozen artifacts in models/ -- run freeze_model.py first")
        return 1
    rows = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
        idxs = [i for i in scan_candidates(enriched, horizon_bars=0, mode="expanded")
                if enriched["open_time"].iloc[i] >= FORWARD_START]
        if not idxs:
            continue
        featured = add_features(enriched)
        frows = extract_feature_rows(featured, idxs)
        opens, lows = enriched["open"].to_numpy(), enriched["low"].to_numpy()
        for pos, si in enumerate(idxs):
            feats = frows.iloc[pos:pos + 1][FEATURE_COLUMNS]
            ei = si + 1
            maker_filled = bool(ei < len(lows) and lows[ei] < opens[ei]) if ei < len(lows) else None
            for config, (booster, thr) in artifacts.items():
                score = float(booster.predict(feats)[0])
                if score < thr:
                    continue
                o = LABELERS[config](enriched, si)
                rows.append({
                    "config": config, "symbol": symbol,
                    "signal_time": str(enriched["open_time"].iloc[si]),
                    "score": round(score, 5), "maker_filled": maker_filled,
                    "outcome": o.outcome if o else "open",
                    "realized_ret": round(o.realized_ret, 6) if o else None,
                    "exit_offset": o.exit_offset if o else None,
                })
    new = pd.DataFrame(rows)
    if LOG.exists():
        old = pd.read_csv(LOG)
        merged = pd.concat([old, new]).drop_duplicates(
            subset=["config", "symbol", "signal_time"], keep="last")
    else:
        merged = new
    if not merged.empty:
        merged = merged.sort_values(["config", "signal_time"])
        merged.to_csv(LOG, index=False)
    n_open = int((merged["outcome"] == "open").sum()) if not merged.empty else 0
    print(f"forward log: {len(merged)} rows total ({n_open} open); +{len(new)} scanned this run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
