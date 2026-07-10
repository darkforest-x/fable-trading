from src.detection.train import SAFE_AUG


def test_yolo_training_disables_semantic_augmentations() -> None:
    forbidden_augmentations = (
        "fliplr",
        "flipud",
        "mosaic",
        "mixup",
        "hsv_h",
        "hsv_s",
        "hsv_v",
    )

    assert {name: SAFE_AUG[name] for name in forbidden_augmentations} == {
        name: 0.0 for name in forbidden_augmentations
    }
