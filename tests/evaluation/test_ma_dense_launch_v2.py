"""V2 parity: similarity layers, quality score, frozen pack and the generated Pine file."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma_dense_launch_references as refs
from yoyo.evaluation import ma_dense_launch_v1_reference as mirror

PACK = Path("experiments/active/exp-ma-dense-launch-pine-20260923-v1/reference_pack.json")
LEDGER = Path("experiments/active/exp-ma-profit3r-20260922-v1/selection_queue_round_017/selection_ledger.jsonl")
pytestmark = pytest.mark.skipif(not PACK.exists(), reason="reference pack absent")


def pack() -> dict:
    """The pack exactly as Pine carries it: references rounded to the emitted precision."""
    value = json.loads(PACK.read_text())
    decimals = int(value["decimals"])
    for key in ("features", "sequences"):
        value["stage1"][key] = np.round(np.asarray(value["stage1"][key], float), decimals).tolist()
    for key in ("anchors", "bad", "family"):
        value["stage2"][key] = np.round(np.asarray(value["stage2"][key], float), decimals).tolist()
    return value


def test_pack_shapes_and_frozen_constants():
    value = json.loads(PACK.read_text())
    assert len(value["stage1"]["features"]) == 50 and len(value["stage1"]["feature_scales"]) == 14
    assert np.asarray(value["stage1"]["sequences"]).shape == (50, 4, 10)
    assert np.asarray(value["stage2"]["anchors"]).shape == (2, 7, 22)
    assert np.asarray(value["stage2"]["bad"]).shape == (6, 7, 22)
    assert np.asarray(value["stage2"]["family"]).shape == (50, 7, 22)
    assert value["stage2"]["perfect_threshold"] == pytest.approx(0.3611898958919062, abs=1e-12)
    assert value["stage2"]["distance_scale"] == pytest.approx(1.1995844782712832, abs=1e-12)


def test_generated_pine_matches_template_and_pack():
    generated = refs.GENERATED.read_text()
    assert generated == refs.render_pine(json.loads(PACK.read_text()), refs.TEMPLATE.read_text())
    assert "__DATA__" not in generated and "__PERFECT_THRESHOLD__" not in generated
    assert generated.count("f_load(S1REF") == 50
    assert generated.count("f_load(S2FAMILY") == 50
    assert generated.count("f_load(S2ANCHOR") == 2 and generated.count("f_load(S2BAD") == 6
    assert "PERFECT_THRESHOLD = 0.3611898959" in generated
    assert "DIST_SCALE = 1.1995844783" in generated


def test_mirror_distances_match_the_frozen_research_functions():
    from yoyo.datasets import ma_launch_owner_perfect_filter as pf

    value = json.loads(PACK.read_text())
    contract = {"sakoe_chiba_radius_bars": value["stage2"]["radius"], "weights": value["stage2"]["component_weights"],
                "segment_slices": value["stage2"]["segment_slices"], "segment_weights": value["stage2"]["segment_weights"],
                "nearest_prefilter_lockstep_k": value["stage2"]["prefilter_k"]}
    left = np.asarray(value["stage2"]["family"][0], float)
    right = np.asarray(value["stage2"]["family"][7], float)
    expected = pf.segmented_sequence_distance(left, right, radius=contract["sakoe_chiba_radius_bars"],
                                              component_weights=contract["weights"],
                                              segment_slices=contract["segment_slices"],
                                              segment_weights=contract["segment_weights"])["combined_distance"]
    assert mirror.segmented_distance(left, right, value["stage2"]) == pytest.approx(expected, abs=1e-12)
    assert mirror.segmented_lockstep(left, right, value["stage2"]) == pytest.approx(
        pf.segmented_lockstep_distance(left, right, segment_slices=contract["segment_slices"],
                                       segment_weights=contract["segment_weights"]), abs=1e-12)
    pool = [np.asarray(s, float) for s in value["stage2"]["family"]]
    assert mirror.nearest_distance(left, pool[1:], value["stage2"]) == pytest.approx(
        refs.nearest_combined(left, pool[1:], contract), abs=1e-12)


def test_resample_matches_numpy_interp():
    values = np.array([1.0, 2.0, 4.0, 8.0])
    expected = np.interp(np.linspace(0, 1, 5), np.linspace(0, 1, 4), values)
    assert mirror.resample5(values) == pytest.approx(expected)
    five = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert mirror.resample5(five) == pytest.approx(five)


@pytest.mark.skipif(not LEDGER.exists(), reason="frozen candidate ledger absent")
def test_frozen_candidates_reproduce_their_recorded_quality_score():
    value = pack()
    rows = []
    for line in LEDGER.open():
        row = json.loads(line)
        if Path(row["source_path"]).exists():
            rows.append(row)
        if len(rows) >= 6:
            break
    if not rows:
        pytest.skip("no local source files for the frozen candidates")
    cache: dict[str, pd.DataFrame] = {}
    for row in rows:
        path = row["source_path"]
        if path not in cache:
            cache[path] = mirror.add_features(pd.read_csv(path))
        out = mirror.evaluate_full(cache[path], int(row["source_core_end_i"]) + 5,
                                   row["direction"], int(row["core_bars"]), value)
        assert out is not None and out["hard_gates"] and out["stage1_similarity"], row["event_id"]
        assert out["quality_score"] == pytest.approx(float(row["quality_score"]), abs=5e-5), row["event_id"]
        assert out["grade_a"] == (str(row["quality_tier"]) == "PERFECT_CANDIDATE"), row["event_id"]
