from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "train_15m_ma_launch_t3_on_3060.sh"
IMGSZ1280_SCRIPT = ROOT / "scripts" / "train_15m_ma_launch_t3_imgsz1280_on_3060.sh"
NEG30000_SCRIPT = (
    ROOT / "scripts" / "train_15m_ma_launch_owner_neg30000_on_3060.sh"
)
GRADE_A_NEG24000_SCRIPT = (
    ROOT
    / "scripts"
    / "train_15m_ma_launch_owner_grade_a8000_neg24000_on_3060.sh"
)
IMGSZ1280_PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1"
    / "preregistration.json"
)


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
    for token in (
        "MANIFEST_SHA",
        "MODEL_SHA",
        "TRAINER_SHA",
        "PREFLIGHT_SHA",
        "LAUNCHER_SHA",
        "STAGE_SENTINEL",
    ):
        assert token in text
    assert text.index("STAGE_SENTINEL=") < text.index("start one detached WMI training job")


def test_launcher_runs_full_preflight_on_local_and_remote_copies() -> None:
    text = source()
    for token in (
        'PREFLIGHT="scripts/windows/verify_yolo_dataset.py"',
        "full local dataset preflight",
        "--verify-file-hashes",
        "local_dataset_preflight.json",
        "remote_dataset_preflight.json",
        "data yaml hash mismatch",
        "remote full dataset preflight failed",
    ):
        assert token in text
    assert text.index("full local dataset preflight") < text.index(
        "package ${DATASET_IMAGE_COUNT}-image immutable dataset"
    )
    assert text.index("remote full dataset preflight failed") < text.index(
        "start one detached WMI training job"
    )


def test_launcher_refuses_low_remote_disk_before_staging() -> None:
    text = source()
    assert "Get-PSDrive -Name C" in text
    assert "remote C: has less than 20 GiB free" in text


def test_launcher_has_no_promotion_or_trading_mutation() -> None:
    text = source()
    forbidden = ("promote_owner_best.py", "active_bundle.json", "forward_log.csv")
    assert all(token not in text for token in forbidden)


def test_launcher_requires_explicit_dhcp_target() -> None:
    text = source()
    assert 'HOST="${FABLE_3060_HOST:-}"' in text
    assert "DHCP addresses are never guessed" in text


def test_fetch_keeps_the_full_remote_training_log() -> None:
    text = source()
    assert '"$HOST:/C:/fable/logs/$NAME.log" "$local_run/train.log"' in text
    assert "remote_training_receipt.txt" in text


def test_shared_launcher_binds_dynamic_contract_before_start() -> None:
    text = source()
    for token in (
        "FABLE_T3_EXPERIMENT_ID",
        "FABLE_T3_PREREG",
        "FABLE_T3_RUN_NAME",
        "FABLE_T3_IMGSZ",
        "FABLE_T3_DATASET",
        "FABLE_T3_BUILD_RECEIPT",
        "FABLE_T3_QA_RECEIPT",
        "FABLE_T3_LOCAL_OUTPUT_ROOT",
        "PREREG_OK",
        "INPUTS_OK",
        'launcher_sha256"] == sys.argv[10]',
        'STRICT_PREFLIGHT="${FABLE_T3_STRICT_PREFLIGHT:-false}"',
        'training_authorized"] is True',
        'safety"]["holdout_read"] is False',
        'safety"]["promote"] is False',
        "--imgsz %s",
    ):
        assert token in text
    assert text.index("PREREG_GATE=") < text.index("package ${DATASET_IMAGE_COUNT}-image immutable dataset")


def test_neg30000_wrapper_selects_only_the_preregistered_dataset() -> None:
    text = NEG30000_SCRIPT.read_text(encoding="utf-8")
    assert 'FABLE_T3_IMGSZ="960"' in text
    assert (
        'FABLE_T3_DATASET="datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2"'
        in text
    )
    assert 'FABLE_T3_DATASET_IMAGE_COUNT="40000"' in text
    assert 'FABLE_T3_RUN_NAME="ma_launch_owner_yolo_neg30000_v2_y11s_ft960"' in text
    assert 'FABLE_T3_REMOTE_DATASET_NAME="ma_launch_owner_yolo_neg30000_v2_input"' in text
    assert text.rstrip().endswith(
        'exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"'
    )
    forbidden = ("promote_owner_best.py", "active_bundle.json", "forward_log.csv")
    assert all(token not in text for token in forbidden)


def test_grade_a_neg24000_wrapper_selects_only_the_preregistered_dataset() -> None:
    text = GRADE_A_NEG24000_SCRIPT.read_text(encoding="utf-8")
    assert 'FABLE_T3_IMGSZ="960"' in text
    assert (
        'FABLE_T3_DATASET="datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"'
        in text
    )
    assert 'FABLE_T3_DATASET_IMAGE_COUNT="32000"' in text
    assert 'FABLE_T3_STRICT_PREFLIGHT="true"' in text
    assert (
        'FABLE_T3_RUN_NAME="ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960"'
        in text
    )
    assert (
        'FABLE_T3_REMOTE_DATASET_NAME="ma_launch_owner_grade_a8000_neg24000_v1_input"'
        in text
    )
    assert text.rstrip().endswith(
        'exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"'
    )
    forbidden = ("promote_owner_best.py", "active_bundle.json", "forward_log.csv")
    assert all(token not in text for token in forbidden)


def test_shared_launcher_verifies_content_addressed_remote_reuse() -> None:
    text = source()
    assert "Assert-Hash '$REMOTE_MODEL' '$MODEL_SHA' 'existing model'" in text
    assert "Assert-Hash '$REMOTE_TRAINER' '$TRAINER_SHA' 'existing trainer'" in text
    assert "Assert-Hash '$REMOTE/incoming_${NAME}_prereg.json' '$PREREG_SHA'" in text
    assert "Assert-Hash '$REMOTE/incoming_${NAME}_build.json' '$BUILD_SHA'" in text
    assert "Assert-Hash '$REMOTE/incoming_${NAME}_qa.json' '$QA_SHA'" in text


def test_imgsz1280_wrapper_selects_only_the_preregistered_treatment() -> None:
    text = IMGSZ1280_SCRIPT.read_text(encoding="utf-8")
    assert 'FABLE_T3_IMGSZ="1280"' in text
    assert 'FABLE_T3_RUN_NAME="ma_launch_t3_10000_v1_y11s_ft_imgsz1280"' in text
    assert 'FABLE_T3_REMOTE_DATASET_NAME="ma_launch_t3_10000_v1_imgsz1280_input"' in text
    assert text.rstrip().endswith(
        'exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"'
    )
    forbidden = ("promote_owner_best.py", "active_bundle.json", "forward_log.csv")
    assert all(token not in text for token in forbidden)


def test_imgsz1280_prereg_is_a_true_single_variable_contract() -> None:
    import json

    treatment = json.loads(IMGSZ1280_PREREG.read_text(encoding="utf-8"))
    baseline = json.loads(
        (
            ROOT
            / "experiments"
            / "active"
            / "exp-15m-ma-launch-t3-yolo10000-v1"
            / "preregistration.json"
        ).read_text(encoding="utf-8")
    )
    assert treatment["single_variable"] == {
        "name": "native training and validation imgsz",
        "baseline": 960,
        "treatment": 1280,
        "all_other_training_arguments_identical": True,
        "source_pngs_rebuilt": False,
        "source_png_dimensions": [1280, 742],
    }
    excluded = {
        "imgsz",
        "run_name",
        "remote_host",
        "remote_host_discovery_required",
        "trainer_sha256",
    }
    treatment_recipe = {
        key: value for key, value in treatment["training"].items() if key not in excluded
    }
    baseline_recipe = {
        key: value for key, value in baseline["training"].items() if key not in excluded
    }
    assert treatment_recipe == baseline_recipe
    assert treatment["safety"]["holdout_read"] is False
    assert treatment["safety"]["promote"] is False
    assert treatment["safety"]["production_eligible"] is False
