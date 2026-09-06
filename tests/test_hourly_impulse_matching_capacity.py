"""Synthetic oracle and hostile-certificate tests; no price/outcome fixtures.

The independent oracle enumerates ownership of each candidate (a mother or no
owner), rather than solving the production edge/y MILP a second time. Exhaustive
small graph domains cover complete-group and global non-reuse invariants without
introducing a property-testing dependency.
"""
from itertools import product
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_matching_capacity as capacity


def edge_frame(rows=()):
    return pd.DataFrame(rows, columns=capacity.EDGE_COLUMNS)


def brute_capacity(mothers, rows, count):
    """Enumerate every admissible candidate ownership and discard partial groups."""
    candidates = sorted({candidate for _, candidate in rows})
    possibilities = [[None] + [m for m in mothers if (m, c) in rows] for c in candidates]
    maximum = 0
    for owners in product(*possibilities):
        totals = [owners.count(m) for m in mothers]
        if all(total in (0, count) for total in totals):
            maximum = max(maximum, sum(total == count for total in totals))
    return maximum


def assert_allocation(mothers, rows, count, allocation, summary):
    assert list(allocation.columns) == capacity.EDGE_COLUMNS
    assert len(allocation) == summary["matched_mothers"] * count
    assert not allocation.candidate_id.duplicated().any()
    assert set(allocation.itertuples(index=False, name=None)).issubset(rows)
    assert allocation.groupby("event_id").size().eq(count).all()
    assert set(allocation.event_id).issubset(mothers)
    assert summary["mother_count"] == len(mothers)
    assert summary["unmatched_mothers"] + summary["matched_mothers"] == len(mothers)
    assert summary["connected_component_upper_bound"] >= summary["matched_mothers"]
    assert summary["complete_mother_upper_bound"] == summary["matched_mothers"]
    assert summary["optimal"] and summary["solution_verified"]


@pytest.mark.parametrize("mother_count,candidate_count,count", [(3, 3, 2), (2, 4, 3)])
def test_exhaustive_small_graphs_match_independent_ownership_oracle(mother_count, candidate_count, count):
    mothers = ["m{}".format(i) for i in range(mother_count)]
    possible_edges = list(product(mothers, ["c{}".format(i) for i in range(candidate_count)]))
    for mask in range(1 << len(possible_edges)):
        rows = [edge for i, edge in enumerate(possible_edges) if mask & (1 << i)]
        allocation, summary = capacity.maximum_complete_matching(mothers, edge_frame(rows), count=count)
        assert summary["matched_mothers"] == brute_capacity(mothers, rows, count), rows
        assert_allocation(mothers, rows, count, allocation, summary)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_seeded_graphs_oracle_order_invariance_and_inputs_untouched(count):
    rng = np.random.default_rng(20260906 + count)
    mothers = ["m0", "m1", "m2"]
    possibilities = list(product(mothers, ["c{}".format(i) for i in range(5)]))
    for _ in range(12):
        rows = [edge for edge in possibilities if rng.random() < .5]
        frame = edge_frame(rows)
        saved_frame = frame.copy(deep=True)
        a, first = capacity.maximum_complete_matching(mothers, frame, count=count)
        b, second = capacity.maximum_complete_matching(mothers[::-1], frame.iloc[::-1], count=count)
        assert first["matched_mothers"] == second["matched_mothers"] == brute_capacity(mothers, rows, count)
        # Canonical input order also makes tied allocations reproducible in the
        # pinned deterministic solver; correctness itself only requires the value.
        pd.testing.assert_frame_equal(a, b)
        assert first == second
        pd.testing.assert_frame_equal(frame, saved_frame)
        assert mothers == ["m0", "m1", "m2"]


def test_partial_flow_cannot_be_divided_to_count_complete_mothers():
    # A conventional <=3 flow can assign all six distinct controls, yet no mother
    # has three neighbours. floor(flow/3)=2 is wrong; complete-group optimum=0.
    mothers = ["a", "b", "c"]
    rows = [(m, "{}{}".format(m, i)) for m in mothers for i in range(2)]
    allocation, summary = capacity.maximum_complete_matching(mothers, edge_frame(rows))
    assert allocation.empty and summary["matched_mothers"] == 0
    assert summary["connected_component_upper_bound"] == 0


def test_connected_component_bound_is_not_misreported_as_attainable():
    rows = [("a", "x"), ("a", "y"), ("b", "y"), ("b", "z"), ("c", "z"), ("c", "x")]
    allocation, summary = capacity.maximum_complete_matching(["a", "b", "c"], edge_frame(rows))
    assert summary["connected_component_upper_bound"] == 1
    assert summary["matched_mothers"] == 0
    assert allocation.empty


def test_optimizer_reallocates_controls_instead_of_greedy_starvation():
    rows = [("a_flexible", c) for c in ("c1", "c2", "c3", "c4", "c5", "c6")]
    rows += [("b_restricted", c) for c in ("c1", "c2", "c3")]
    allocation, summary = capacity.maximum_complete_matching(["a_flexible", "b_restricted"], edge_frame(rows))
    assert summary["matched_mothers"] == 2
    assert set(allocation.loc[allocation.event_id.eq("b_restricted"), "candidate_id"]) == {"c1", "c2", "c3"}


def test_shared_bottleneck_and_unselected_id_does_not_mean_individually_impossible():
    mothers = ["a", "b", "c"]
    rows = list(product(mothers, ["x", "y", "z"]))
    allocation, summary = capacity.maximum_complete_matching(mothers, edge_frame(rows))
    assert summary["matched_mothers"] == 1
    assert summary["connected_component_upper_bound"] == 1
    assert summary["mothers_without_edges"] == []
    assert "not individual impossibility" in summary["unmatched_ids_scope"]
    assert len(summary["unmatched_mother_ids"]) == 2
    # Each unselected mother can receive the exact three controls in an equally
    # optimal full-graph allocation; being absent from one solution proves nothing.
    for mother in summary["unmatched_mother_ids"]:
        alternate = {(mother, candidate) for candidate in ("x", "y", "z")}
        assert alternate.issubset(rows)
        assert len(alternate) == len(allocation)


def test_opposite_direction_names_do_not_partition_candidate_reuse():
    mothers = ["event_LONG", "event_SHORT"]
    rows = [(mother, candidate) for mother in mothers for candidate in ("time1", "time2", "time3")]
    allocation, summary = capacity.maximum_complete_matching(mothers, edge_frame(rows))
    assert summary["matched_mothers"] == 1  # not one per direction
    assert allocation.candidate_id.nunique() == 3
    assert allocation.event_id.nunique() == 1


def test_component_bound_respects_disconnected_pools_and_isolated_mothers():
    rows = [("a", "a1"), ("a", "a2"), ("b", "b1"), ("b", "b2"), ("b", "b3"), ("b", "b4")]
    allocation, summary = capacity.maximum_complete_matching(["a", "b", "no_edges"], edge_frame(rows))
    assert summary["candidate_count"] // 3 == 2  # naive global upper bound
    assert summary["connected_component_upper_bound"] == 1
    assert summary["connected_component_count"] == 3
    assert summary["mothers_without_edges"] == ["no_edges"]
    assert summary["unmatched_mother_ids"] == ["a", "no_edges"]
    assert_allocation(["a", "b", "no_edges"], rows, 3, allocation, summary)


@pytest.mark.parametrize("mothers", [[], ["isolated"], ["b", "a"]])
def test_empty_graph_has_explicit_structural_proof_without_calling_solver(monkeypatch, mothers):
    def forbidden_solver(**kwargs):
        raise AssertionError("Empty graph requires no numerical solver")
    monkeypatch.setattr(capacity, "milp", forbidden_solver)
    allocation, summary = capacity.maximum_complete_matching(mothers, edge_frame())
    assert allocation.empty
    assert summary["proof"] == "empty_edge_graph"
    assert summary["solver_called"] is False
    assert summary["solver_status"] is None  # never fabricate a solver status
    assert_allocation(mothers, [], 3, allocation, summary)


def test_same_string_can_name_mother_and_candidate_in_separate_namespaces():
    rows = [("a", "a"), ("a", "b"), ("a", "c")]
    allocation, summary = capacity.maximum_complete_matching(["a", "isolated"], edge_frame(rows))
    assert summary["matched_mothers"] == 1
    assert summary["connected_component_count"] == 2
    assert_allocation(["a", "isolated"], rows, 3, allocation, summary)


@pytest.mark.parametrize("mothers", [None, "a", ("a",), ["a", "a"], [""], ["  "], [None], [1], [np.nan], [pd.NA]])
def test_invalid_mother_ids_rejected(mothers):
    with pytest.raises(ValueError):
        capacity.maximum_complete_matching(mothers, edge_frame())


@pytest.mark.parametrize("count", [True, False, np.bool_(True), 0, -1, 1.5, 3.0, np.nan, np.inf, "3", None])
def test_invalid_count_rejected(count):
    with pytest.raises(ValueError):
        capacity.maximum_complete_matching(["a"], edge_frame(), count=count)


@pytest.mark.parametrize("time_limit", [True, np.bool_(True), False, 0, -1, np.nan, np.inf, -np.inf, "30", None, 10**400])
def test_invalid_time_limit_rejected(time_limit):
    with pytest.raises(ValueError):
        capacity.maximum_complete_matching(["a"], edge_frame(), time_limit=time_limit)


@pytest.mark.parametrize("edges", [
    None, [], pd.DataFrame(), pd.DataFrame(columns=["event_id", "other"]),
    pd.DataFrame(columns=["event_id", "candidate_id", "net_return"]),
    pd.DataFrame(columns=["event_id", "event_id"]),
    edge_frame([("orphan", "c")]), edge_frame([("a", "c"), ("a", "c")]),
    edge_frame([("a", "")]), edge_frame([("a", "   ")]), edge_frame([("a", None)]),
    edge_frame([("a", np.nan)]), edge_frame([("a", pd.NA)]), edge_frame([("a", 3)]),
    edge_frame([(None, "c")]), edge_frame([(1, "c")]),
])
def test_invalid_edges_rejected(edges):
    with pytest.raises(ValueError):
        capacity.maximum_complete_matching(["a"], edges)


def test_numpy_integer_count_and_reordered_columns_are_supported():
    allocation, summary = capacity.maximum_complete_matching(
        ["a"], edge_frame([("a", "c")])[["candidate_id", "event_id"]],
        count=np.int64(1), time_limit=np.float64(2.0))
    assert summary["matched_mothers"] == 1
    assert allocation.to_dict("records") == [{"event_id": "a", "candidate_id": "c"}]


def test_arbitrarily_large_integer_count_is_structurally_zero_without_float_overflow():
    allocation, summary = capacity.maximum_complete_matching(["a"], edge_frame([("a", "c")]), count=10**400)
    assert allocation.empty and summary["matched_mothers"] == 0
    assert summary["solver_called"] is False
    assert summary["proof"] == "component_upper_bound_zero"


def good_result(**overrides):
    result = dict(status=0, success=True, message="optimal", x=np.ones(4), fun=-1.0,
                  mip_dual_bound=-1.0, mip_gap=0.0, mip_node_count=0)
    result.update(overrides)
    return SimpleNamespace(**result)


@pytest.mark.parametrize("overrides", [
    {"status": 1, "success": False, "message": "time limit", "mip_dual_bound": -2., "mip_gap": 1.},
    {"status": 1, "success": True}, {"status": 2, "success": False},
    {"status": 3, "success": False}, {"status": 4, "success": False}, {"success": False},
    {"status": None}, {"status": False}, {"status": 0.0}, {"status": "0"},
    {"x": None}, {"x": []}, {"x": np.ones(5)}, {"x": np.ones((1, 4))},
    {"x": [1, 1, 1, np.nan]}, {"x": [1, 1, 1, np.inf]}, {"x": [1, 1, 1, .5]},
    {"x": [1, 1, 1, 2]}, {"x": [1, 1, 1, -1]}, {"x": [1, 1, 0, 1]},
    {"x": [1, 1, 1, 0]}, {"x": [0, 0, 0, 1]},
    {"fun": -.5}, {"fun": None}, {"fun": np.nan}, {"fun": np.inf},
    {"mip_dual_bound": None}, {"mip_dual_bound": -2.}, {"mip_dual_bound": -.5},
    {"mip_gap": None}, {"mip_gap": .01}, {"mip_gap": -.01}, {"mip_gap": np.nan},
])
def test_nonoptimal_or_bad_certificate_never_returns_a_maximum(monkeypatch, overrides):
    monkeypatch.setattr(capacity, "milp", lambda **kwargs: good_result(**overrides))
    with pytest.raises(capacity.MatchingCapacityError) as caught:
        capacity.maximum_complete_matching(["a"], edge_frame([("a", c) for c in ("x", "y", "z")]))
    diagnostics = caught.value.diagnostics
    assert not diagnostics["optimal"] and not diagnostics["solution_verified"]
    assert "matched_mothers" not in diagnostics
    assert "failure_reason" in diagnostics


def test_shared_control_illegal_solution_rejected_independently(monkeypatch):
    # Both mothers have three edges but share x. The forged objective and gap
    # are self-consistent; only independent non-reuse checking catches this.
    rows = [("a", c) for c in ("x", "y", "z")] + [("b", c) for c in ("x", "u", "v")]
    monkeypatch.setattr(capacity, "milp", lambda **kwargs: good_result(x=np.ones(8), fun=-2., mip_dual_bound=-2.))
    with pytest.raises(capacity.MatchingCapacityError, match="non-reuse"):
        capacity.maximum_complete_matching(["a", "b"], edge_frame(rows))


def test_solver_exception_fails_closed_with_diagnostics(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("Synthetic solver fault")
    monkeypatch.setattr(capacity, "milp", broken)
    with pytest.raises(capacity.MatchingCapacityError, match="invocation failed") as caught:
        capacity.maximum_complete_matching(["a"], edge_frame([("a", "x")]), count=1)
    assert caught.value.diagnostics["solver_status"] is None


def test_small_numerical_integrality_noise_verified_after_rounding(monkeypatch):
    monkeypatch.setattr(capacity, "milp", lambda **kwargs: good_result(x=np.ones(4) - 1e-8))
    allocation, summary = capacity.maximum_complete_matching(["a"], edge_frame([("a", c) for c in ("x", "y", "z")]))
    assert summary["matched_mothers"] == 1 and len(allocation) == 3


def test_solver_formulation_and_options_are_binary_all_or_none(monkeypatch):
    def inspect(**kwargs):
        assert kwargs["options"] == {"time_limit": 12.5, "mip_rel_gap": 0., "disp": False}
        np.testing.assert_array_equal(kwargs["c"], [0, 0, 0, -1])
        np.testing.assert_array_equal(kwargs["integrality"], [1, 1, 1, 1])
        assert np.all(kwargs["bounds"].lb == 0) and np.all(kwargs["bounds"].ub == 1)
        constraint = kwargs["constraints"]
        np.testing.assert_array_equal(constraint.A.toarray(), [[1, 1, 1, -3], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
        np.testing.assert_array_equal(constraint.lb, [0, 0, 0, 0])
        np.testing.assert_array_equal(constraint.ub, [0, 1, 1, 1])
        return good_result()
    monkeypatch.setattr(capacity, "milp", inspect)
    capacity.maximum_complete_matching(["a"], edge_frame([("a", c) for c in ("z", "y", "x")]), time_limit=12.5)
