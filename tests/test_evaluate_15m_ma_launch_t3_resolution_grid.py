from pathlib import Path

import pytest

from scripts.evaluate_15m_ma_launch_t3_resolution_grid import (
    ResolutionGridError,
    validate_weight,
)


def test_validate_weight_requires_exact_hash(tmp_path: Path) -> None:
    weight = tmp_path / "best.pt"
    weight.write_bytes(b"frozen")
    expected = "ffb304816a1090313e833215c08dae3d209cfad1ffd1f674f0909a2ae99e1394"
    assert validate_weight(weight, expected) == expected


def test_validate_weight_rejects_drift(tmp_path: Path) -> None:
    weight = tmp_path / "best.pt"
    weight.write_bytes(b"drift")
    with pytest.raises(ResolutionGridError, match="weight hash drifted"):
        validate_weight(weight, "0" * 64)
