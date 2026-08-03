"""One exact bundle decides what production runs. No discovery, no fallback.

The failure this exists to prevent is not hypothetical. frozen.py finds the newest
loadable JSON matching a glob, skips a corrupt one, and quietly serves an older
model; runtime never reads models/ACTIVE at all. That the pointer and the default
config happen to agree today is luck, not governance -- and on 2026-08-03 a
related gap did fire for real, with a short model served six sign-flipped
features because the extractor was chosen from trade side rather than from what
the model was trained on (analysis/p0_baseline_audit_20260803.md).

So a bundle names everything that has to agree, and every one of them is checked:
identity by sha256 of the actual files, semantics by explicit enum. A field that
is absent is an error rather than a default, because every default in this area
has a direction, and the wrong direction is what breaks serving.

Two invariants are enforced beyond field presence, because they cannot be true
together and both have already been assumed at some point:

  legacy_unaligned semantics can never be execution eligible -- that model was
  fitted in a different coordinate system, and "just fix the live features"
  is precisely the half-step that produced the 2026-08-03 fault

  paper_only and execution_eligible cannot both be true -- a bundle that says it
  is paper must not be reachable by the order path

This module does NOT activate anything. Presence of models/active_bundle.json is
the owner's switch; absence leaves the existing runtime path untouched. Iron rule
10 and the takeover plan's D-07/O-03 both put that decision with the owner, and a
loader that promotes itself is the thing they forbid.

Takeover plan: docs/protocol_repair/P0_SAFETY_SPEC.md section 3, acceptance
C-01..C-08 and A-05/A-06.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_DIR = Path(__file__).resolve().parents[2]
ACTIVE_BUNDLE = PROJECT_DIR / "models" / "active_bundle.json"

SIDES = ("long", "short")
FEATURE_SEMANTICS = ("legacy_unaligned", "side_aligned_v1")
THRESHOLD_OPERATORS = (">", ">=")
SAME_BAR_POLICIES = ("conservative_sl",)
CANDIDATE_SOURCES = ("yolo",)

# Every one is required. Absent is an error, not a default: see module docstring.
REQUIRED_FIELDS = (
    "bundle_version", "protocol_version", "strategy_id", "side", "timeframe",
    "window_bars", "candidate_source", "max_tip_age_bars",
    "feature_schema", "feature_semantics", "score_semantics",
    "threshold", "threshold_operator", "tie_policy",
    "research_entry_mode", "live_entry_mode",
    "tp_atr_mult", "sl_atr_mult", "horizon_bars", "same_bar_policy",
    "return_convention", "cost_route",
    "detector_path", "detector_sha256",
    "model_path", "model_sha256",
    "dataset_path", "dataset_sha256",
    "execution_eligible", "paper_only",
)

# (field carrying the path, field carrying the digest)
HASHED_ARTEFACTS = (
    ("model_path", "model_sha256"),
    ("dataset_path", "dataset_sha256"),
    ("detector_path", "detector_sha256"),
)


class BundleError(RuntimeError):
    """Raised for any bundle that cannot be trusted. Never downgraded to a warning."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class StrategyProtocol:
    """The whole contract, flat and typed. Construct only via load_bundle."""

    path: Path
    bundle_version: int
    protocol_version: str
    strategy_id: str
    side: str
    timeframe: str
    window_bars: int
    candidate_source: str
    max_tip_age_bars: int
    feature_schema: str
    feature_semantics: str
    score_semantics: str
    threshold: float
    threshold_operator: str
    tie_policy: str
    research_entry_mode: str
    live_entry_mode: str
    tp_atr_mult: float
    sl_atr_mult: float
    horizon_bars: int
    same_bar_policy: str
    return_convention: str
    cost_route: str
    detector_path: Path
    detector_sha256: str
    model_path: Path
    model_sha256: str
    dataset_path: Path
    dataset_sha256: str
    execution_eligible: bool
    paper_only: bool

    def passes_threshold(self, score: float) -> bool:
        """Apply the bundle's own operator. > and >= differ exactly where ties sit.

        The takeover plan reports the production q90 gate letting through about
        91.2% of val because scores tie on the boundary, so which operator a
        bundle declares is load-bearing, not cosmetic.
        """
        return score > self.threshold if self.threshold_operator == ">" else score >= self.threshold

    def accepts_row_side(self, row_side: object) -> bool:
        """Acceptance A-05: a row whose side disagrees with the strategy is a mismatch.

        Anything absent or unparseable is refused rather than assumed, matching
        executor.signal_trade_side since 2026-08-03.
        """
        if row_side is None:
            return False
        return str(row_side).strip().lower() == self.side


def _require(raw: Mapping[str, Any], path: Path) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise BundleError(path, f"missing required field(s): {', '.join(sorted(missing))}")


def _enum(raw: Mapping[str, Any], path: Path, field: str, allowed: tuple[str, ...]) -> str:
    value = str(raw[field]).strip()
    if value not in allowed:
        raise BundleError(path, f"{field}={value!r} not in {allowed}")
    return value


def _resolve(project_dir: Path, value: Any) -> Path:
    p = Path(str(value))
    return p if p.is_absolute() else project_dir / p


def load_bundle(path: Path, project_dir: Path = PROJECT_DIR) -> StrategyProtocol:
    """Load and fully verify one bundle. Any doubt raises; nothing is skipped.

    Deliberately has no sibling that "finds" a bundle. Discovery is the defect
    being removed, so the caller must name the file.
    """
    path = Path(path)
    if not path.exists():
        raise BundleError(path, "bundle file does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(path, f"unreadable bundle: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise BundleError(path, "bundle must be a JSON object")

    _require(raw, path)
    side = _enum(raw, path, "side", SIDES)
    semantics = _enum(raw, path, "feature_semantics", FEATURE_SEMANTICS)
    operator = _enum(raw, path, "threshold_operator", THRESHOLD_OPERATORS)
    _enum(raw, path, "same_bar_policy", SAME_BAR_POLICIES)
    _enum(raw, path, "candidate_source", CANDIDATE_SOURCES)

    for field in ("execution_eligible", "paper_only"):
        if not isinstance(raw[field], bool):
            raise BundleError(path, f"{field} must be a JSON boolean, got {raw[field]!r}")
    eligible = bool(raw["execution_eligible"])
    paper_only = bool(raw["paper_only"])

    if eligible and semantics == "legacy_unaligned":
        raise BundleError(
            path,
            "execution_eligible with feature_semantics=legacy_unaligned: that model "
            "was fitted in a different coordinate system; repairing the live "
            "extractor does not make it executable (plan D-03)",
        )
    if eligible and paper_only:
        raise BundleError(path, "execution_eligible and paper_only cannot both be true")

    try:
        numbers = {
            "bundle_version": int(raw["bundle_version"]),
            "window_bars": int(raw["window_bars"]),
            "max_tip_age_bars": int(raw["max_tip_age_bars"]),
            "horizon_bars": int(raw["horizon_bars"]),
            "threshold": float(raw["threshold"]),
            "tp_atr_mult": float(raw["tp_atr_mult"]),
            "sl_atr_mult": float(raw["sl_atr_mult"]),
        }
    except (TypeError, ValueError) as exc:
        raise BundleError(path, f"non-numeric field: {exc}") from exc

    from src.judgment.frozen import file_sha256  # local: avoids an import cycle

    resolved: dict[str, Path] = {}
    for path_field, hash_field in HASHED_ARTEFACTS:
        target = _resolve(project_dir, raw[path_field])
        if not target.exists():
            raise BundleError(path, f"{path_field} does not exist: {target}")
        declared = str(raw[hash_field]).strip().lower()
        actual = file_sha256(target)
        if declared != actual:
            raise BundleError(
                path,
                f"{hash_field} mismatch for {target.name}: "
                f"declared {declared[:16]}… actual {actual[:16]}…",
            )
        resolved[path_field] = target

    return StrategyProtocol(
        path=path,
        protocol_version=str(raw["protocol_version"]),
        strategy_id=str(raw["strategy_id"]),
        side=side,
        timeframe=str(raw["timeframe"]),
        candidate_source=str(raw["candidate_source"]),
        feature_schema=str(raw["feature_schema"]),
        feature_semantics=semantics,
        score_semantics=str(raw["score_semantics"]),
        threshold_operator=operator,
        tie_policy=str(raw["tie_policy"]),
        research_entry_mode=str(raw["research_entry_mode"]),
        live_entry_mode=str(raw["live_entry_mode"]),
        same_bar_policy=str(raw["same_bar_policy"]),
        return_convention=str(raw["return_convention"]),
        cost_route=str(raw["cost_route"]),
        detector_path=resolved["detector_path"],
        detector_sha256=str(raw["detector_sha256"]).strip().lower(),
        model_path=resolved["model_path"],
        model_sha256=str(raw["model_sha256"]).strip().lower(),
        dataset_path=resolved["dataset_path"],
        dataset_sha256=str(raw["dataset_sha256"]).strip().lower(),
        execution_eligible=eligible,
        paper_only=paper_only,
        **numbers,
    )


def load_active_bundle(project_dir: Path = PROJECT_DIR) -> StrategyProtocol | None:
    """The production entry point. Returns None only when no bundle is configured.

    None means "the owner has not switched this on", and the caller keeps its
    existing behaviour. A bundle that exists but does not verify raises -- there
    is no third outcome where production quietly runs something else, which is
    exactly what latest_artifact() does today and acceptance C-06 forbids.
    """
    bundle = project_dir / "models" / "active_bundle.json"
    if not bundle.exists():
        return None
    return load_bundle(bundle, project_dir=project_dir)
