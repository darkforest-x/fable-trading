from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.judgment.frozen import file_sha256
from src.judgment.train import HOLDOUT_START
from src.webapp.dashboard_cache import (
    UniverseSpec,
    _cache_matches_universe,
    relative_path,
    universe_spec,
)


def test_dashboard_rejects_legacy_spot_universe() -> None:
    with pytest.raises(HTTPException, match="MA206 mainline supports swap only"):
        universe_spec("spot")


def test_score_cache_requires_pre_holdout_scope(tmp_path: Path) -> None:
    dataset = tmp_path / "swap.csv"
    dataset.write_text("signal_time\n", encoding="utf-8")
    spec = UniverseSpec("swap", "合约/SWAP", dataset)
    metadata = {
        "threshold": 0.34,
        "universe": "swap",
        "dataset_path": relative_path(dataset),
        "dataset_sha256": file_sha256(dataset),
    }

    assert not _cache_matches_universe(metadata, spec, artifact=None)

    metadata.update(
        {
            "score_scope": "pre_holdout_only",
            "score_end_before": str(HOLDOUT_START),
        }
    )
    assert _cache_matches_universe(metadata, spec, artifact=None)
