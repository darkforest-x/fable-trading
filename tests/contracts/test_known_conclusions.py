"""Facts that must survive the merge, checked against the migrated reports.

Task book section 11.8 asks for a regression test over the historical
conclusions, so that a later refactor cannot quietly change what an experiment
found. The check is deliberately made against the MIGRATED REPORT TEXT and the
experiment registry, not against a number retyped here: a test that asserts its
own constant only proves the constant was copied twice.

One of the four conclusions the task book lists did not happen. That is asserted
too, in the opposite direction, because the correction is itself a fact that
must not drift back.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from yoyo.artifacts import load_registries

REPO = Path(__file__).resolve().parents[2]
HISTORICAL = REPO / "experiments" / "historical"


@pytest.fixture(scope="module")
def registries():
    return load_registries(root=REPO)


def _read(*parts: str) -> str:
    path = HISTORICAL.joinpath(*parts)
    assert path.is_file(), f"{path.relative_to(REPO)} is missing"
    return path.read_text(encoding="utf-8")


# -- 1. yoyo-eth: the compression pool did not beat random entry ------------

def test_the_loose_compression_pool_lost_to_its_matched_control(registries):
    """Both directions, on both splits, from the migrated report itself."""
    report = _read("yoyo_eth", "MVP_REPORT.md")
    assert "matched random control" in report

    row = next(
        e for e in registries.experiments if e.experiment_id == "exp-yoyo-eth-semantic-mvp"
    )
    assert row.status == "rejected"
    assert row.production_eligible is False
    assert "underperformed_matched_random_control" in row.result
    # the two gaps, as stated in the registered result
    assert "34.5bp" in row.result and "37.1bp" in row.result


def test_the_model_ranking_reversed_out_of_sample(registries):
    row = next(
        e for e in registries.experiments if e.experiment_id == "exp-yoyo-eth-semantic-mvp"
    )
    assert "+0.167" in row.result and "-0.068" in row.result, (
        "the val-to-test sign flip is the finding; if it is edited out, the "
        "experiment reads as inconclusive rather than negative"
    )


def test_the_anchored_walkforward_did_not_reproduce_its_own_headline(registries):
    report = _read("yoyo_eth", "P03_ANCHORED_WF_REPORT.md")
    assert "0.465" in report, "the number that failed to reproduce is missing from the report"
    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-yoyo-eth-p03-anchored-walkforward"
    )
    assert row.status == "rejected"
    assert "not_reproducible" in row.result


# -- 2. darkforest-one: P2 never happened ----------------------------------

def test_darkforest_one_p2_was_never_started_not_failed(registries):
    """The task book says P2's economic gate failed. It was never run.

    Asserted against the ROADMAP as it stood at the freeze commit: every P2
    checkbox unticked. If someone later 'restores' the task book's version,
    this fails.
    """
    roadmap = _read("darkforest_one", "ROADMAP_at_freeze.md")
    p2_section = roadmap.split("## P2")[1].split("## P3")[0]
    ticked = re.findall(r"- \[x\]", p2_section)
    assert not ticked, f"P2 now shows {len(ticked)} completed items; it had none at fd36dd1"

    p1_section = roadmap.split("## P1")[1].split("## P2")[0]
    assert re.findall(r"- \[x\]", p1_section), "P1 was complete at the freeze commit"

    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-darkforest-one-p1-causal-candidate-dataset"
    )
    assert row.status == "superseded", (
        "darkforest-one is superseded, not closed_negative: there is no negative "
        "result because there is no result"
    )
    assert "P2 never started" in row.result


def test_the_correction_is_recorded_where_someone_will_read_it():
    readme = _read("darkforest_one", "README.md")
    assert "对任务书的事实更正" in readme
    for claim in ("P2 经济门失败", "P3 被阻塞"):
        assert claim in readme, f"the corrected claim {claim!r} is not quoted in the README"


# -- 3. yolo-xx: the tip collapse, and the pooled-AUC trap ------------------

def test_the_old_detector_collapses_at_the_tip(registries):
    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-yolo-xx-teacher-tip-vs-full-context"
    )
    assert row.status == "rejected"
    assert "9-10%" in row.result and "62-72%" in row.result, (
        "the tip-vs-full-context gap is why YOLO is a teacher and not the trigger"
    )
    readme = _read("yolo_xx", "README.md")
    assert "62–72%" in readme and "9–10%" in readme


def test_the_pooled_auc_was_source_discrimination(registries):
    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-yolo-xx-pooled-auc-source-discrimination"
    )
    assert row.status == "rejected"
    # the headline number is what was asked; the collapse is what was found
    assert "0.8067" in row.question
    assert "0.57-0.65" in row.result
    assert "85% / 84% / 29%" in row.result, "the per-source base rates are the evidence"


def test_the_quality_ranker_lift_was_leakage(registries):
    row = next(
        e for e in registries.experiments if e.experiment_id == "exp-yolo-xx-quality-ranker-v1"
    )
    assert row.status == "rejected" and "leakage" in row.result


# -- 4. yoyo-trading: frozen is not training-eligible ----------------------

def test_the_reviewed_gold_core_is_frozen_but_not_training_eligible(registries):
    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-yoyo-trading-fixed-w10-gold-freeze"
    )
    assert row.training_eligible is False
    assert row.production_eligible is False
    assert "DIRECT=0" in row.result, (
        "the reason training_eligible is false -- an unmeasured spot-check error "
        "rate -- must stay attached to the fact"
    )
    assert "1,247" in row.result and "1,402" in row.result

    freeze = _read("yoyo_trading", "fixed_w10_freeze.md")
    assert "20686feba41d15b8" in freeze, "the manifest SHA is the dataset's identity"


def test_the_one_holdout_consumption_is_still_recorded(registries):
    row = next(
        e
        for e in registries.experiments
        if e.experiment_id == "exp-yoyo-trading-fixed-w10-classifier-holdout3d"
    )
    assert row.holdout_consumed is True
    assert row.reuse_allowed is False, "the window is spent for this configuration"
    assert row.status == "inconclusive"
    assert "31.9%" in row.result


def test_every_authorized_holdout_consumer_remains_explicit(registries):
    consumers = {e.experiment_id for e in registries.experiments if e.holdout_consumed}
    assert consumers == {
        "exp-btc-4h-ma-launch-similarity-v1",
        "exp-btc-4h-ma-launch-similarity-top20-v2",
        "exp-15m-ma-launch-owner-yolo-recent5d-v1",
        "exp-15m-ma-launch-t3-daily-movers3d-v1",
        "exp-15m-ma-launch-t3-daily-movers3d-v2",
        "exp-pine-eth-15m-v1",
        "exp-yoyo-trading-fixed-w10-classifier-holdout3d",
    }


# -- the historical tree itself -------------------------------------------

@pytest.mark.parametrize(
    "repo_dir", ["darkforest_one", "yolo_xx", "yoyo_trading", "yoyo_eth"]
)
def test_each_historical_directory_can_answer_the_six_questions(repo_dir: str):
    """What was tested, which variable, what data, holdout, result, reuse."""
    readme = HISTORICAL / repo_dir / "README.md"
    summary = HISTORICAL / repo_dir / "summary.json"
    assert readme.is_file(), f"{repo_dir} has no README"
    assert summary.is_file(), f"{repo_dir} has no machine summary"

    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["source_commit"], f"{repo_dir} summary does not name a source commit"
    assert payload["experiments"], f"{repo_dir} summary registers no experiments"
    for experiment in payload["experiments"]:
        for field in ("experiment_id", "status", "question", "result", "holdout_consumed"):
            assert experiment.get(field) not in (None, ""), (
                f"{repo_dir}/{experiment.get('experiment_id')} cannot answer {field!r}"
            )


def test_no_negative_result_was_softened_into_a_pending_one(registries):
    """`rejected` is terminal. It does not become `active` by being unwelcome."""
    negative = {
        "exp-yoyo-eth-semantic-mvp",
        "exp-yoyo-eth-p03-anchored-walkforward",
        "exp-yolo-xx-teacher-tip-vs-full-context",
        "exp-yolo-xx-quality-ranker-v1",
        "exp-yolo-xx-pooled-auc-source-discrimination",
    }
    by_id = {e.experiment_id: e for e in registries.experiments}
    for experiment_id in negative:
        assert experiment_id in by_id, f"{experiment_id} was removed from the registry"
        assert by_id[experiment_id].status == "rejected", (
            f"{experiment_id} is now {by_id[experiment_id].status}; a negative result "
            "does not become pending optimisation"
        )
