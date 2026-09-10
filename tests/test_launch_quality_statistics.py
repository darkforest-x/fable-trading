"""Closed-feature filters and count nulls never read future trade outcomes."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.launch_quality_statistics import (
    filter_variants, holm_adjust, paired_event_statistics, random_thinning,
)


def fixture():
    rows = []
    for venue in ("gate", "okx"):
        for asset in ("A", "B"):
            for week in ("2026-06-01T00:00Z", "2026-06-08T00:00Z"):
                for position in range(5):
                    rows.append(dict(event_id=f"{venue}:{asset}:{week}:{position}",
                                     venue=venue, asset=asset, valid=True,
                                     decision_time=pd.Timestamp(week) + pd.Timedelta(hours=position),
                                     relative_volume=position + 1., tr_expansion=position + .5,
                                     arm="focus_md", net_return=(-1.) ** position,
                                     exit_price=position + 100, excess_bp=position * 50.))
    return pd.DataFrame(rows)


def group_counts(frame, ids):
    selected = frame.loc[frame.event_id.isin(ids)].copy()
    day = selected.decision_time.dt.tz_convert("UTC").dt.normalize()
    selected["week"] = day - pd.to_timedelta(day.dt.weekday, unit="D")
    return selected.groupby(["venue", "asset", "week"]).size().to_dict()


def test_exact_thresholds_and_nonfinite_values_are_independent():
    frame = pd.DataFrame(dict(relative_volume=[3.99, 4., np.nan, np.inf, -np.inf, 5.],
                              tr_expansion=[3., 2.99, np.inf, np.nan, -np.inf, 4.],
                              arm=["focus_md"] * 6, valid=[True] * 5 + [False]))
    got = filter_variants(frame)
    assert got["volume4"].index.tolist() == [1, 5]
    assert got["expansion3"].index.tolist() == [0, 5]
    pd.testing.assert_frame_equal(got["baseline"], frame)
    assert got["volume4"].arm.eq("focus_md").all()
    got["baseline"].loc[0, "relative_volume"] = 999
    assert frame.loc[0, "relative_volume"] == 3.99


def test_thinning_group_counts_match_and_rejected_candidates_can_return():
    base = fixture()
    filtered = filter_variants(base)["volume4"]
    result = random_thinning(base, filtered)
    expected = group_counts(base, filtered.event_id)
    assert len(result["selections"]) == 19
    assert any(set(ids) - set(filtered.event_id) for ids in result["selections"].values())
    for ids in result["selections"].values():
        assert ids == sorted(ids) and len(set(ids)) == len(filtered)
        assert group_counts(base, ids) == expected
    assert result["summary"]["replaceable_selected_group_fraction"] == 1.
    assert result["summary"]["replaceable_target_candidate_fraction"] == 1.


def test_future_outcomes_and_row_order_cannot_change_selection():
    base = fixture()
    filtered = filter_variants(base)["volume4"]
    expected = random_thinning(base, filtered)
    scrambled = base.sample(frac=1, random_state=90).copy()
    scrambled["net_return"] = np.arange(len(scrambled)) * 1e20
    scrambled["exit_price"] = np.nan
    scrambled["excess_bp"] = -np.inf
    reversed_filter = scrambled.loc[scrambled.event_id.isin(filtered.event_id)].iloc[::-1]
    actual = random_thinning(scrambled, reversed_filter)
    assert actual["selections"] == expected["selections"]
    pd.testing.assert_frame_equal(actual["groups"], expected["groups"])
    assert actual["summary"] == expected["summary"]


def test_identity_only_inputs_prove_no_required_future_fields():
    base = fixture().drop(columns=["net_return", "exit_price", "excess_bp"])
    selected = filter_variants(base)["expansion3"]
    assert len(random_thinning(base, selected)["selections"][20260910]) == len(selected)


def test_invalid_rows_and_nonreplaceable_groups_are_explicit():
    base = fixture().iloc[:6].copy()
    base.loc[base.index[0], "valid"] = False
    filtered = base.iloc[[0, 5]].copy()
    got = random_thinning(base, filtered, seeds=[1])
    assert got["selections"][1] == [base.iloc[5].event_id]
    assert got["summary"]["baseline_valid_candidates"] == 5
    assert got["summary"]["filtered_valid_candidates"] == 1
    assert got["summary"]["selected_groups"] == 1
    assert got["summary"]["replaceable_groups"] == 0
    assert got["groups"].target_count.tolist() == [0, 1]


def test_empty_target_and_empty_base_return_empty_schedules():
    base = fixture()
    target = base.iloc[:0]
    for population in (base, target):
        got = random_thinning(population, target, seeds=[0])
        assert got["selections"] == {0: []}
        assert got["summary"]["filtered_valid_candidates"] == 0
        assert np.isnan(got["summary"]["replaceable_selected_group_fraction"])


@pytest.mark.parametrize("defect", ["duplicate", "unknown", "metadata", "naive", "validity", "seed"])
def test_bad_identity_or_seed_fails_closed(defect):
    base = fixture()
    selected = base.iloc[:2].copy()
    seeds = [1]
    if defect == "duplicate":
        base = pd.concat([base, base.iloc[:1]])
    elif defect == "unknown":
        selected.loc[selected.index[0], "event_id"] = "unknown"
    elif defect == "metadata":
        selected.loc[selected.index[0], "venue"] = "other"
    elif defect == "naive":
        base["decision_time"] = base.decision_time.dt.tz_localize(None)
    elif defect == "validity":
        base["valid"] = "True"
    else:
        seeds = [1, 1]
    with pytest.raises(ValueError):
        random_thinning(base, selected, seeds=seeds)


def test_utc_week_uses_decision_close_not_display_timezone():
    base = fixture().iloc[:2].copy()
    base["decision_time"] = pd.to_datetime(["2026-06-08T00:00:00+08:00", "2026-06-08T08:00:00+08:00"])
    result = random_thinning(base, base, seeds=[0])
    assert result["groups"].week.tolist() == [pd.Timestamp("2026-06-01T00:00Z"), pd.Timestamp("2026-06-08T00:00Z")]


def test_paired_test_weights_assets_and_weeks_not_repeat_events():
    base = fixture().iloc[:1].copy()
    base["excess_bp"] = 100.
    b = base.assign(event_id="b", asset="B", excess_bp=-100.)
    copies = pd.concat([base.assign(event_id=f"a{i}") for i in range(8)])
    events = pd.concat([copies, b], ignore_index=True)
    got = paired_event_statistics(events)
    assert got["asset_balanced_excess_bp"] == 0.
    assert got["event_mean_excess_bp"] > 0.
    assert got["permutation_assets"] == 2 and np.isnan(got["permutation_p"])
    # A second A-week has the same weight as its eight-copy first week.
    second_week = base.assign(event_id="a-next", decision_time=pd.Timestamp("2026-06-08T00:00Z"), excess_bp=-100.)
    extended = paired_event_statistics(pd.concat([events, second_week], ignore_index=True))
    assert extended["asset_balanced_excess_bp"] == -50.


def test_permutation_is_deterministic_excludes_missing_pairs_and_handles_all_zero():
    first = fixture().iloc[:1].copy()
    rows = [first.assign(event_id=f"e{i}", asset=f"A{i}", excess_bp=100.) for i in range(6)]
    rows += [first.assign(event_id="missing", asset="M", excess_bp=np.nan),
             first.assign(event_id="invalid", asset="I", excess_bp=1e9, valid=False)]
    events = pd.concat(rows, ignore_index=True)
    a = paired_event_statistics(events, permutations=1000)
    b = paired_event_statistics(events.sample(frac=1, random_state=8), permutations=1000)
    assert a == b and 0 < a["permutation_p"] < .05
    assert a["matched_events"] == 6 and a["valid_events"] == 7
    assert a["matched_fraction"] == 6/7
    events["excess_bp"] = 0.
    assert paired_event_statistics(events)["permutation_p"] == 1.


def test_holm_preserves_nan_and_rejects_impossible_values():
    result = holm_adjust([.01, np.nan, .03, .02])
    np.testing.assert_allclose(result, [.03, np.nan, .04, .04], equal_nan=True)
    for values in ([np.inf], [-.1], [1.1], [[.1]]):
        with pytest.raises(ValueError):
            holm_adjust(values)
