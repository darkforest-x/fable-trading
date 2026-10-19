"""Static safety contract for the disposable Windows RTX 3060 training box."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_windows_trainer():
    path = ROOT / "scripts" / "windows" / "train_dense.py"
    spec = importlib.util.spec_from_file_location("windows_train_dense", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_trainer_disables_semantics_breaking_augmentations() -> None:
    trainer = _load_windows_trainer()
    forbidden = (
        "fliplr",
        "flipud",
        "mosaic",
        "mixup",
        "copy_paste",
        "hsv_h",
        "hsv_s",
        "hsv_v",
    )
    assert {name: trainer.SAFE_AUG[name] for name in forbidden} == {
        name: 0.0 for name in forbidden
    }


def test_windows_setup_uses_available_cuda_wheels_and_checked_installs() -> None:
    text = (ROOT / "scripts" / "windows" / "setup_3060.ps1").read_text(
        encoding="utf-8"
    )
    assert "https://download.pytorch.org/whl/cu126" in text
    assert "https://download.pytorch.org/whl/cu124" not in text
    assert '"torchvision==$TorchvisionVersion"' in text
    assert "pip install --dry-run --report" in text
    assert "$LASTEXITCODE" in text


def test_remote_training_syncs_controlled_entry_and_never_auto_promotes() -> None:
    text = (ROOT / "scripts" / "train_on_3060.sh").read_text(encoding="utf-8")
    assert 'TRAINER="scripts/windows/train_dense.py"' in text
    assert 'SCP_REMOTE="/C:/fable"' in text
    assert "import torch,torchvision,ultralytics,numpy" in text
    assert "NMS_OK=" in text
    assert "LOCAL_ENTRY_SHA" in text and "COPIED_ENTRY_SHA" in text
    assert "PYTHONPATH=. .venv/bin/python scripts/promote_owner_best.py" not in text
