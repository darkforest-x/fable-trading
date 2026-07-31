"""ACTIVE pointer is the runtime freeze authority (H11)."""
from __future__ import annotations

import json
from pathlib import Path

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.frozen import (
    FrozenConfig,
    load_runtime_artifact,
)


def _write_freeze(project: Path, stem: str, config_name: str, *, side_hint: str = "short") -> None:
    models = project / "models"
    models.mkdir(parents=True, exist_ok=True)
    (project / "data").mkdir(parents=True, exist_ok=True)
    ds = project / "data" / "ds.csv"
    ds.write_text("x\n1\n", encoding="utf-8")
    txt = models / f"{stem}.txt"
    txt.write_text("booster\n", encoding="utf-8")
    meta = {
        "config": config_name,
        "model_path": f"models/{stem}.txt",
        "dataset_path": "data/ds.csv",
        "dataset_sha256": "abc",
        "dataset_size_bytes": 3,
        "threshold_val_q90": 0.01,
        "feature_columns": list(FEATURE_COLUMNS),
        "best_iteration": 1,
        "side_note": side_hint,
    }
    (models / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")


def test_load_runtime_honors_active_pointer(tmp_path: Path) -> None:
    stem = "frozen_tp5_sl2_swap_yolo_v10_reg_20260731"
    _write_freeze(tmp_path, stem, "tp5_sl2_swap_yolo_v10_reg")
    (tmp_path / "models" / "ACTIVE").write_text(f"models/{stem}.txt\n", encoding="utf-8")

    art = load_runtime_artifact(tmp_path)
    assert art is not None
    assert art.metadata_path.name == f"{stem}.json"
    assert art.config.side == "short"
    assert art.config.name == "tp5_sl2_swap_yolo_v10_reg"


def test_signal_key_excludes_score() -> None:
    import pandas as pd

    from src.execution.executor import signal_key

    a = pd.Series(
        {"source": "okx", "symbol": "ETH_USDT_SWAP", "signal_time": "2026-07-01", "score": 0.1}
    )
    b = pd.Series(
        {"source": "okx", "symbol": "ETH_USDT_SWAP", "signal_time": "2026-07-01", "score": 0.9}
    )
    assert signal_key(a) == signal_key(b)
    assert "0.1" not in signal_key(a)
