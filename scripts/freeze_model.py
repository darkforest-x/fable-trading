# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/freeze_model.py --write-active
"""Freeze the selected judgment model for forward validation.

Default (2026-07-15+ owner): tp5_sl2_swap_yolo_reg — LightGBM regression on
realized_ret over YOLO candidates (data/judgment_yolo_swap.csv).

Rollback:
  --binary-yolo   previous YOLO binary freeze config name
  --legacy-rules  pre-cutover rule-scan config
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.judgment.frozen import (
    binary_yolo_shadow_config,
    default_config,
    rules_legacy_config,
    train_frozen_artifact,
    yolo_v8_pool_config,
    yolo_v11_pool_config,
)

PROJECT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument(
        "--legacy-rules",
        action="store_true",
        help="use pre-cutover rule-scan dataset/config name (rollback)",
    )
    parser.add_argument(
        "--binary-yolo",
        action="store_true",
        help="freeze/point at previous YOLO binary config (shadow / rollback)",
    )
    parser.add_argument(
        "--yolo-v8-pool",
        action="store_true",
        help="freeze regression on the v8_chain candidate pool "
             "(judgment_yolo_swap_v8.csv; rollback / compare)",
    )
    parser.add_argument(
        "--yolo-v11-pool",
        action="store_true",
        help="freeze regression on the v11_chain candidate pool "
             "(judgment_yolo_swap_v11.csv; mainline after 2026-07-18 cutover)",
    )
    parser.add_argument(
        "--write-active",
        action="store_true",
        help="write models/ACTIVE to the new artifact model path",
    )
    return parser.parse_args()


def run_walkforward(config, n_folds: int = 5) -> dict:
    """Expanding-window fold stability for the freeze's own metadata.

    Same recipe as the freeze (train_model on the config's objective), five
    sequential val slices over the dev period, purge on both sides. A single
    val split can look fine while the model is broken -- the judgment audit's
    five-fold table is what exposed stability, so every freeze now ships one.
    Holdout is never touched (load_splits already fences it).
    """
    import numpy as np
    from scipy.stats import spearmanr

    from src.costs import SWAP_MAKER
    from src.data.bars import purge_window
    from src.judgment.features import FEATURE_COLUMNS
    from src.judgment.train import HOLDOUT_START, load_splits, train_model

    purge = purge_window(config.horizon_bars, "15m")
    import pandas as pd

    data = pd.read_csv(config.dataset_path, parse_dates=["signal_time"])
    data = data.sort_values("signal_time").reset_index(drop=True)
    dev = data[data["signal_time"] < HOLDOUT_START - purge].reset_index(drop=True)
    n = len(dev)
    edges = [int(n * x) for x in (0.40, 0.52, 0.64, 0.76, 0.88, 1.00)][: n_folds + 1]
    folds = []
    for f in range(len(edges) - 1):
        val = dev.iloc[edges[f]: edges[f + 1]]
        train = dev[dev["signal_time"] < val["signal_time"].min() - purge]
        if len(train) < 500 or len(val) < 100:
            continue
        model = train_model(train, val, objective=config.objective)
        sc = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        ret = val["realized_ret"].to_numpy()
        k = max(1, len(val) // 10)
        top = np.argsort(-sc)[:k]
        folds.append({
            "val_start": str(val["signal_time"].min())[:10],
            "n_val": int(len(val)),
            "spearman": round(float(spearmanr(sc, ret).statistic), 4),
            "top_decile_net_maker": round(float(ret[top].mean() - SWAP_MAKER), 5),
            "top_decile_win": round(float((ret[top] > 0).mean()), 3),
        })
    rhos = [f["spearman"] for f in folds]
    nets = [f["top_decile_net_maker"] for f in folds]
    out = {
        "folds": folds,
        "rho_mean": round(sum(rhos) / len(rhos), 4) if rhos else None,
        "rho_min": min(rhos) if rhos else None,
        "net_min": min(nets) if nets else None,
        "all_folds_net_positive": bool(nets and all(x > 0 for x in nets)),
    }
    print(f"walkforward: rho_mean={out['rho_mean']} rho_min={out['rho_min']} "
          f"all_net_positive={out['all_folds_net_positive']}")
    return out


def main() -> int:
    args = parse_args()
    pool_flags = (args.legacy_rules, args.binary_yolo, args.yolo_v8_pool, args.yolo_v11_pool)
    if sum(pool_flags) > 1:
        raise SystemExit(
            "choose at most one of --legacy-rules / --binary-yolo / "
            "--yolo-v8-pool / --yolo-v11-pool"
        )
    if args.legacy_rules:
        config = rules_legacy_config()
        candidate_source = "rules"
    elif args.binary_yolo:
        config = binary_yolo_shadow_config()
        candidate_source = "yolo"
    elif args.yolo_v8_pool:
        config = yolo_v8_pool_config()
        candidate_source = "yolo"
    elif args.yolo_v11_pool:
        config = yolo_v11_pool_config()
        candidate_source = "yolo"
    else:
        config = default_config()
        candidate_source = "yolo"
    if not config.dataset_path.exists():
        raise SystemExit(f"dataset missing: {config.dataset_path}")
    artifact = train_frozen_artifact(config, args.date)
    walkforward = run_walkforward(config)
    meta = {
        "model_path": artifact.relative_model_path,
        "metadata_path": artifact.metadata_path.relative_to(artifact.config.project_dir).as_posix(),
        "dataset_path": artifact.relative_dataset_path,
        "dataset_sha256": artifact.dataset_sha256,
        "threshold_val_q90": artifact.threshold,
        "best_iteration": artifact.best_iteration,
        "config": config.name,
        "objective": config.objective,
        "candidate_source": candidate_source,
        "walkforward": walkforward,
    }
    # Persist fold stability inside the artifact metadata so every freeze
    # carries its own robustness evidence (single-split val numbers alone hid
    # the lr bug for weeks; the five-fold table is what caught the pattern).
    art_meta = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    art_meta["walkforward"] = walkforward
    artifact.metadata_path.write_text(
        json.dumps(art_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if args.write_active:
        active = PROJECT / "models" / "ACTIVE"
        prev = PROJECT / "models" / "ACTIVE_PREV"
        if active.exists():
            prev.write_text(active.read_text(encoding="utf-8"), encoding="utf-8")
        active.write_text(artifact.relative_model_path + "\n", encoding="utf-8")
        # Keep prior mainline as SHADOW for dashboard compare / rollback.
        shadow = PROJECT / "models" / "SHADOW_V8_REG"
        shadow.write_text(
            "models/frozen_tp5_sl2_swap_yolo_v8_reg_20260716.txt\n"
            "# previous v8 pool regression freeze; compare + emergency rollback\n",
            encoding="utf-8",
        )
        shadow_bin = PROJECT / "models" / "SHADOW_BINARY_YOLO"
        if not shadow_bin.exists():
            shadow_bin.write_text(
                "models/frozen_tp5_sl2_swap_yolo_20260715.txt\n"
                "# previous binary YOLO freeze; emergency rollback\n",
                encoding="utf-8",
            )
        print(f"ACTIVE -> {artifact.relative_model_path}")
        print(f"ACTIVE_PREV kept; SHADOW_V8_REG -> v8 pool freeze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
