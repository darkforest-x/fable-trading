"""The registries are only worth having if a bad row fails loudly.

Two halves. The first proves the *rules* fire -- a registry validator that
accepts everything is decoration. The second runs the rules against the two
registry files actually committed here, so a later edit that breaks the schema
breaks the suite instead of being discovered by whoever cites the row next.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from yoyo.artifacts import load_artifact_registry, load_experiment_registry, load_registries
from yoyo.contracts.artifacts import ArtifactRecord, ExperimentRecord, RegistryError

REPO = Path(__file__).resolve().parents[2]


def _artifact(**overrides):
    base = {
        "artifact_id": "a1",
        "artifact_type": "model",
        "role": "judgment",
        "source_repo": "darkforest-x/example",
        "source_commit": "0" * 40,
        "source_path": "models/x.txt",
        "holdout_status": "pre_holdout",
        "training_eligible": False,
        "production_eligible": False,
    }
    base.update(overrides)
    return base


def _experiment(**overrides):
    base = {
        "experiment_id": "e1",
        "source_repo": "darkforest-x/example",
        "source_commit": "0" * 40,
        "status": "rejected",
        "question": "does it work",
        "result": "no",
        "holdout_consumed": False,
        "production_eligible": False,
        "training_eligible": False,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# the rules must fire
# --------------------------------------------------------------------------

def test_a_missing_required_field_is_refused():
    row = _artifact()
    del row["source_commit"]
    with pytest.raises(RegistryError, match="source_commit"):
        ArtifactRecord.from_mapping(row)


def test_an_unknown_holdout_status_is_refused():
    with pytest.raises(RegistryError, match="holdout_status"):
        ArtifactRecord.from_mapping(_artifact(holdout_status="probably_fine"))


def test_eligibility_flags_must_be_real_booleans():
    """YAML turns "no" into False but "false-ish" strings into truthy text."""
    with pytest.raises(RegistryError, match="boolean"):
        ArtifactRecord.from_mapping(_artifact(production_eligible="false"))


def test_promotion_requires_established_holdout_provenance():
    with pytest.raises(RegistryError, match="holdout_status=unknown"):
        ArtifactRecord.from_mapping(
            _artifact(holdout_status="unknown", production_eligible=True, sha256="a" * 64)
        )


def test_promotion_requires_a_hash():
    with pytest.raises(RegistryError, match="without sha256"):
        ArtifactRecord.from_mapping(_artifact(production_eligible=True))


def test_a_malformed_sha256_is_refused():
    with pytest.raises(RegistryError, match="SHA-256"):
        ArtifactRecord.from_mapping(_artifact(sha256="not-a-hash"))


def test_a_rejected_experiment_cannot_be_production_eligible():
    with pytest.raises(RegistryError, match="did not pass"):
        ExperimentRecord.from_mapping(_experiment(status="rejected", production_eligible=True))


def test_an_accepted_experiment_needs_a_readable_report():
    with pytest.raises(RegistryError, match="canonical_report"):
        ExperimentRecord.from_mapping(_experiment(status="accepted", canonical_report=None))


def test_an_unknown_experiment_status_is_refused():
    with pytest.raises(RegistryError, match="status"):
        ExperimentRecord.from_mapping(_experiment(status="looks_promising"))


def test_duplicate_ids_are_refused(tmp_path: Path):
    registry = tmp_path / "artifacts" / "registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "schema_version: 1\n"
        "artifacts:\n"
        "  - {artifact_id: dup, artifact_type: model, role: r, source_repo: x,\n"
        "     source_commit: c, source_path: p, holdout_status: pre_holdout,\n"
        "     training_eligible: false, production_eligible: false}\n"
        "  - {artifact_id: dup, artifact_type: dataset, role: r, source_repo: x,\n"
        "     source_commit: c, source_path: q, holdout_status: pre_holdout,\n"
        "     training_eligible: false, production_eligible: false}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="duplicate artifact_id"):
        load_artifact_registry(registry)


def test_an_unsupported_schema_version_is_refused(tmp_path: Path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("schema_version: 99\nartifacts: []\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="schema_version"):
        load_artifact_registry(registry)


def test_an_experiment_citing_an_unregistered_artifact_is_refused(tmp_path: Path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "experiments").mkdir()
    (tmp_path / "artifacts" / "registry.yaml").write_text(
        "schema_version: 1\nartifacts: []\n", encoding="utf-8"
    )
    (tmp_path / "experiments" / "registry.yaml").write_text(
        "schema_version: 1\n"
        "experiments:\n"
        "  - {experiment_id: e, source_repo: x, source_commit: c, status: rejected,\n"
        "     question: q, result: r, holdout_consumed: false,\n"
        "     production_eligible: false, training_eligible: false,\n"
        "     artifacts: [nobody-declared-this]}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="no artifact row declares"):
        load_registries(root=tmp_path)


# --------------------------------------------------------------------------
# the committed registries must obey them
# --------------------------------------------------------------------------

def test_the_committed_registries_load_and_cross_check():
    registries = load_registries(root=REPO)
    assert registries.artifacts, "artifact registry is empty"
    assert registries.experiments, "experiment registry is empty"


def test_nothing_in_the_registries_is_production_eligible_yet():
    """Consolidation promotes nothing. Task book section 3.2."""
    registries = load_registries(root=REPO)
    promoted = [r.artifact_id for r in registries.artifacts if r.production_eligible]
    promoted += [e.experiment_id for e in registries.experiments if e.production_eligible]
    assert not promoted, (
        f"{promoted} are marked production_eligible. Promotion is an owner decision "
        "and this task is not allowed to make it."
    )


def test_holdout_consumption_is_declared_per_experiment_not_assumed():
    """The count has to survive the merge, so it is asserted, not narrated."""
    registries = load_registries(root=REPO)
    consumers = {e.experiment_id for e in registries.experiments if e.holdout_consumed}
    assert consumers == {
        "exp-btc-4h-ma-launch-similarity-top20-v2",
        "exp-btc-4h-ma-launch-similarity-v1",
        "exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2",
        "exp-15m-ma-launch-owner-yolo-recent5d-v1",
        "exp-15m-ma-launch-t3-daily-movers3d-v1",
        "exp-15m-ma-launch-t3-daily-movers3d-v2",
        "exp-pine-eth-15m-v1",
        "exp-yoyo-trading-fixed-w10-classifier-holdout3d",
    }, (
        "the set of experiments declaring holdout consumption changed: "
        f"{sorted(consumers)}. Every entry needs an owner authorisation recorded "
        "with it (CLAUDE.md rule 1)."
    )


def test_the_consolidation_itself_declares_no_holdout_consumption():
    experiments = load_experiment_registry(root=REPO)
    row = next(e for e in experiments if e.experiment_id == "exp-consolidation-single-repo-v1")
    assert row.holdout_consumed is False
