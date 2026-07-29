"""Safety and geometry tests for the ETH 3m v2 classification pilot."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from PIL import Image

from scripts.train_eth3m_short_pilot_v2_cls import full_frame_transforms, train_kwargs
from src.detection.eth3m_v2_classification import (
    EXPECTED_COUNTS,
    IMAGE_SIZE,
    OUTPUT_DATASET,
    PREREG,
    letterbox_full_frame,
    validate_authorization,
    verify_prepared,
)

PROJECT = Path(__file__).resolve().parents[1]


def test_prereg_freezes_authorization_recipe_and_gates() -> None:
    prereg = validate_authorization(PREREG)
    assert prereg["owner_authorization"]["owner_exact_words"] == "直接去3060跑吧"
    assert prereg["owner_authorization"]["existing_job_must_not_be_killed_or_waited"] is True
    assert prereg["acceptance_gates"] == {
        "probability_threshold": 0.5,
        "threshold_sweep": False,
        "val_short_start_true_positives_min": 6,
        "val_short_start_total": 8,
        "val_no_start_false_positives_max": 2,
        "val_no_start_total": 34,
        "continuous_smoke_raw_fires_per_day_max": 3.0,
        "continuous_smoke_event_merge_bars": 18,
        "continuous_smoke_events_per_day_max": 1.0,
        "owner_review_events": 30,
        "owner_review_yes_rate_min": 0.6,
    }


def test_letterbox_preserves_full_right_edge(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    output = tmp_path / "square.png"
    image = Image.new("RGB", (1280, 742), (10, 20, 30))
    for y in range(742):
        image.putpixel((1279, y), (255, 0, 0))
    image.save(source)
    geometry = letterbox_full_frame(source, output)
    assert geometry == {
        "source_width": 1280,
        "source_height": 742,
        "resized_width": 960,
        "resized_height": 557,
        "pad_left": 0,
        "pad_top": 201,
        "pad_right": 0,
        "pad_bottom": 202,
    }
    with Image.open(output) as square:
        assert square.size == (IMAGE_SIZE, IMAGE_SIZE)
        assert square.getpixel((0, 0)) == (255, 255, 255)
        # The original rightmost time edge lands at x=959, never outside a crop.
        red = square.getpixel((959, 480))
        assert red[0] > red[1] * 3 and red[0] > red[2] * 3


def test_prepared_real_dataset_is_train_val_only_and_hash_clean() -> None:
    meta = verify_prepared(OUTPUT_DATASET)
    assert meta["total"] == 137
    assert meta["included_roots"] == ["train", "val"]
    assert meta["excluded_from_training"] == ["weak_or_review", "continuous_smoke", "holdout"]
    actual_counts = {
        (split, class_name): len(list((OUTPUT_DATASET / split / class_name).glob("*.png")))
        for split, class_name in EXPECTED_COUNTS
    }
    assert actual_counts == EXPECTED_COUNTS


def test_full_frame_transform_is_deterministic_and_uncropped() -> None:
    image = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (0, 0, 0))
    image.putpixel((IMAGE_SIZE - 1, IMAGE_SIZE // 2), (255, 0, 0))
    transform = full_frame_transforms(IMAGE_SIZE)
    first = transform(image)
    second = transform(image)
    assert first.equal(second)
    assert tuple(first.shape) == (3, IMAGE_SIZE, IMAGE_SIZE)
    assert float(first[0, IMAGE_SIZE // 2, IMAGE_SIZE - 1]) == 1.0


def test_train_kwargs_disable_every_semantic_breaking_augmentation() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    kwargs = train_kwargs(prereg, OUTPUT_DATASET, prereg["training"]["run_name"])
    for key in (
        "fliplr", "flipud", "hsv_h", "hsv_s", "hsv_v", "scale", "translate",
        "erasing", "degrees", "shear", "perspective", "bgr", "mosaic", "mixup",
        "cutmix", "copy_paste",
    ):
        assert kwargs[key] == 0.0
    assert kwargs["auto_augment"] is None
    assert kwargs["seed"] == 42
    assert kwargs["deterministic"] is True
    assert kwargs["exist_ok"] is False


def test_3060_launcher_has_no_queue_kill_retry_or_promotion_path() -> None:
    text = (PROJECT / "scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh").read_text(encoding="utf-8")
    assert "Stop-Process" not in text
    assert "Wait-Process" not in text
    assert "--wait-pid" not in text
    assert "promote_owner_best" not in text
    assert "models/ACTIVE" not in text
    assert "PID=93656" not in text
    assert "Invoke-CimMethod -ClassName Win32_Process" in text
    assert "exit_code" in text


def test_3060_launcher_requires_an_explicit_current_host() -> None:
    script = PROJECT / "scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh"
    env = os.environ.copy()
    env.pop("FABLE_3060_HOST", None)
    result = subprocess.run(
        ["bash", str(script), "--check"],
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FABLE_3060_HOST is required" in result.stderr
    assert "SSH" not in result.stdout


def test_3060_batch_sets_project_pythonpath_before_trainer() -> None:
    text = (PROJECT / "scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh").read_text(encoding="utf-8")
    pythonpath_line = "printf 'set \"PYTHONPATH=%s\"\\r\\n' \"$REMOTE_WIN\""
    trainer_line = "python.exe -u C:\\\\fable\\\\scripts\\\\train_eth3m_short_pilot_v2_cls_"
    assert "REMOTE_WIN=\"${REMOTE//\\//\\\\}\"" in text
    assert pythonpath_line in text
    assert trainer_line in text
    assert text.index(pythonpath_line) < text.index(trainer_line)


def test_remote_powershell_encodes_the_entire_multiline_program() -> None:
    text = (PROJECT / "scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh").read_text(encoding="utf-8")
    assert 'data.encode("utf-16le")' in text
    assert "base64.b64encode" in text
    assert "-EncodedCommand $encoded" in text
    assert "NonInteractive -Command -" not in text
    assert '[[ -n "$encoded" ]]' in text


def test_wmi_start_is_gated_by_exact_staging_sentinel() -> None:
    text = (PROJECT / "scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh").read_text(encoding="utf-8")
    sentinel_check = '[[ "$STAGE_CONFIRMED" == 1 ]]'
    wmi_start = 'say "start one detached WMI job'
    assert "STAGE_OK|$NAME|$MODEL_SHA|$BATCH_SHA" in text
    assert sentinel_check in text
    assert text.index(sentinel_check) < text.index(wmi_start)
    assert "Finalize-Immutable" in text
    assert "Assert-Dataset" in text
