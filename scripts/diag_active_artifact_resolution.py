"""Does the live-wired L2 artifact have enough resolution to select a top decile?

The project's only stable positive result is "judgment layer picks the top 10%".
That claim is about a research model. This script asks a different question about
a different object: the artifact behind ``models/ACTIVE`` -- the one production
actually scores with -- and whether its score distribution can express a decile
at all.

Two ways an artifact fails that are invisible from AUC or from the metadata:

1. ``best_iteration = 1``. LightGBM early stopping on a near-noise target is
   minimised by the constant predictor, so the saved booster can be a single
   tree. A single tree has as many distinct outputs as it has leaves.
2. The frozen threshold sits inside a tie mass. ``threshold_val_q90`` is the 90th
   percentile of val scores, but when most rows share one score the percentile
   lands on that score, and ``score >= threshold`` passes the whole mass.

Reads only the artifact's own dataset (pre-holdout by construction; the v10 pool
ends 2026-05-03 and holdout starts 2026-05-04). Trains nothing, writes nothing,
touches neither ACTIVE nor forward_log.

Usage:
    PYTHONPATH=. python3 scripts/diag_active_artifact_resolution.py
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_PATH = PROJECT_DIR / "models" / "ACTIVE"


def main() -> None:
    txt_rel = ACTIVE_PATH.read_text().strip()
    meta_path = PROJECT_DIR / txt_rel.replace(".txt", ".json")
    meta = json.loads(meta_path.read_text())

    features = meta["feature_columns"]
    threshold = meta["threshold_val_q90"]
    booster = lgb.Booster(model_file=str(PROJECT_DIR / meta["model_path"]))

    pool = pd.read_csv(PROJECT_DIR / meta["dataset_path"])
    pool["signal_time"] = pd.to_datetime(pool["signal_time"], utc=True)
    pool = pool.sort_values("signal_time").reset_index(drop=True)
    scores = booster.predict(pool[features].astype(float))

    val_range = meta["splits"]["val"]["range"]
    val = pool[pool["signal_time"] >= pd.Timestamp(val_range[0])]
    val_scores = booster.predict(val[features].astype(float))

    values, counts = np.unique(scores, return_counts=True)
    top = int(np.argmax(counts))

    print(f"artifact          {meta_path.name}")
    print(f"  best_iteration  {meta.get('best_iteration')}")
    print(f"  trees in file   {booster.num_trees()}")
    print(f"  features        {len(features)}")
    print(f"  pool            {meta['dataset_path']}  n={len(pool)}")
    print(f"  pool range      {pool['signal_time'].min()} -> {pool['signal_time'].max()}")
    print()
    print(f"distinct scores   {len(values)} for {len(scores)} candidates")
    print(f"  modal score     {values[top]:+.8f}  covering {counts[top]} rows "
          f"({counts[top] / len(scores):.1%})")
    print(f"  threshold       {threshold:+.8f}"
          f"{'   <-- equals the modal score' if np.isclose(values[top], threshold) else ''}")
    print()
    print(f"gate pass rate    pool {float(np.mean(scores >= threshold)):.1%}"
          f"   val {float(np.mean(val_scores >= threshold)):.1%}")
    print(f"  a decile gate would pass 10.0%")

    verdict = "CANNOT" if float(np.mean(val_scores >= threshold)) > 0.20 else "can"
    print()
    print(f"verdict: this artifact {verdict} express a top decile on its own pool.")


if __name__ == "__main__":
    main()
