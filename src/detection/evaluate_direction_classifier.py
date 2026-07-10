"""Pure reconciliation metrics for the fixed causal direction experiment.

Class order follows the numeric baseline contract. Image inference and report
I/O live in the CLI so these functions remain deterministic and testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.judgment.direction_economics import DIRECTION_CLASSES


class DirectionEvaluationError(RuntimeError):
    """Raised when labels, predictions, or model classes cannot reconcile."""


@dataclass(frozen=True)
class PerClassMetrics:
    class_name: str
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationMetrics:
    n_samples: int
    accuracy: float
    balanced_accuracy: float
    confusion: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]
    per_class: tuple[PerClassMetrics, PerClassMetrics, PerClassMetrics]


def _validate_classes(values: list[str], *, role: str) -> None:
    unsupported = sorted(set(values) - set(DIRECTION_CLASSES))
    if unsupported:
        raise DirectionEvaluationError(f"unsupported {role} classes: {unsupported}")


def candidate_side_predictions(manifest: pd.DataFrame) -> list[str]:
    """Return a fixed side-only baseline, treating overlap as no-trade."""
    required = {"long_candidate", "short_candidate"}
    if not required.issubset(manifest.columns):
        raise DirectionEvaluationError(f"candidate baseline requires columns: {sorted(required)}")
    predictions: list[str] = []
    for row in manifest.itertuples(index=False):
        if bool(row.long_candidate) and not bool(row.short_candidate):
            predictions.append("long")
        elif bool(row.short_candidate) and not bool(row.long_candidate):
            predictions.append("short")
        else:
            predictions.append("no_trade")
    return predictions


def ordered_model_names(names: Mapping[int, str]) -> tuple[str, str, str]:
    """Parse YOLO class indices without assuming its alphabetical order."""
    if sorted(names) != [0, 1, 2] or set(names.values()) != set(DIRECTION_CLASSES):
        raise DirectionEvaluationError(f"model classes must equal {DIRECTION_CLASSES}, observed {dict(names)}")
    return names[0], names[1], names[2]


def classification_metrics(truth: list[str], predictions: list[str]) -> ClassificationMetrics:
    """Compute fixed three-class confusion, accuracy and per-class metrics."""
    if len(truth) != len(predictions):
        raise DirectionEvaluationError(
            f"truth length={len(truth)} differs from predictions length={len(predictions)}"
        )
    if not truth:
        raise DirectionEvaluationError("classification metrics require at least one row")
    _validate_classes(truth, role="truth")
    _validate_classes(predictions, role="prediction")
    class_to_index = {name: index for index, name in enumerate(DIRECTION_CLASSES)}
    confusion = np.zeros((3, 3), dtype=int)
    for expected, observed in zip(truth, predictions):
        confusion[class_to_index[expected], class_to_index[observed]] += 1

    per_class: list[PerClassMetrics] = []
    for index, class_name in enumerate(DIRECTION_CLASSES):
        true_positive = int(confusion[index, index])
        predicted = int(confusion[:, index].sum())
        support = int(confusion[index, :].sum())
        precision = true_positive / predicted if predicted else 0.0
        recall = true_positive / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class.append(PerClassMetrics(class_name, precision, recall, f1, support))

    matrix = tuple(tuple(int(value) for value in row) for row in confusion)
    typed_matrix = (matrix[0], matrix[1], matrix[2])
    return ClassificationMetrics(
        n_samples=len(truth),
        accuracy=float(np.trace(confusion) / len(truth)),
        balanced_accuracy=float(np.mean([item.recall for item in per_class])),
        confusion=typed_matrix,
        per_class=(per_class[0], per_class[1], per_class[2]),
    )
