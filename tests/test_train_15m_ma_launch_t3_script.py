from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "train_15m_ma_launch_t3_on_3060.sh"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_launcher_uses_exact_frozen_recipe() -> None:
    text = source()
    for token in (
        "--epochs 40",
        "--patience 10",
        "--batch 8",
        "--seed 0",
        "--finetune",
        "--cache false",
        "--workers 2",
    ):
        assert token in text


def test_launcher_hashes_dataset_model_and_trainer_before_wmi() -> None:
    text = source()
    for token in ("MANIFEST_SHA", "MODEL_SHA", "TRAINER_SHA", "STAGE_SENTINEL"):
        assert token in text
    assert text.index("STAGE_SENTINEL=") < text.index("start one detached WMI training job")


def test_launcher_has_no_promotion_or_trading_mutation() -> None:
    text = source()
    forbidden = ("promote_owner_best.py", "active_bundle.json", "forward_log.csv")
    assert all(token not in text for token in forbidden)


def test_launcher_requires_explicit_dhcp_target() -> None:
    text = source()
    assert 'HOST="${FABLE_3060_HOST:-}"' in text
    assert "DHCP addresses are never guessed" in text
