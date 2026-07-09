"""Freeze model artifacts (P1-5): train once on the SWAP pools and save the
exact bytes forward validation will use. Both current contenders are frozen
(tp5_sl2 and scaled_25_t3); the forward tracker records both, the 08-05
verdict picks.

Artifacts: models/frozen_{config}_{date}.txt (LightGBM booster) +
           models/frozen_{config}_{date}.json (threshold, features, fingerprint)
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
MODELS = PROJECT_DIR / "models"
POOLS = {
    "tp5_sl2": PROJECT_DIR / "data" / "sweep_v3_portfolio" / "tp5_sl2.csv",
    "scaled_25_t3": PROJECT_DIR / "data" / "sweep_v3_portfolio" / "scaled_25_t3.csv",
}


def main() -> int:
    MODELS.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    for name, pool in POOLS.items():
        train, val, _ = load_splits(pool, horizon_bars=72)  # holdout untouched
        model = train_model(train, val)
        prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        threshold = float(np.quantile(prob, 0.90))
        base = MODELS / f"frozen_{name}_{stamp}"
        model.save_model(str(base.with_suffix(".txt")), num_iteration=model.best_iteration)
        base.with_suffix(".json").write_text(json.dumps({
            "config": name, "frozen_on": stamp, "threshold_val_q90": round(threshold, 6),
            "features": FEATURE_COLUMNS, "best_iteration": model.best_iteration,
            "n_train": len(train), "n_val": len(val),
            "pool_sha256": hashlib.sha256(pool.read_bytes()).hexdigest()[:16],
            "universe": "okx *_USDT_SWAP 15m expanded", "horizon_bars": 72,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"frozen {name}: thr={threshold:.4f} iter={model.best_iteration}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
