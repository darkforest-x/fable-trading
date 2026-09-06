"""Outcome-free maximum capacity of complete, non-reused control groups.

Input contains only mother IDs and known admissible mother/candidate edges;
this module neither constructs matching features nor reads prices or outcomes.
Every mother is either assigned exactly ``count`` distinct controls or none.
Maximizing ordinary edge flow and dividing it by ``count`` is NOT equivalent:
partial groups must not count as matched mothers.

Source: SciPy 1.13.1 ``scipy.optimize.milp`` documentation:
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
Binary edge variables x and mother variables y have constraints sum(x_m) =
count*y_m and sum(x_candidate) <= 1. Minimize -sum(y), with binary bounds,
integrality=1 and mip_rel_gap=0. Status 0 alone is insufficient here: the
returned integer allocation, objective and dual certificate are rechecked.
Limits/non-optimal results fail closed, never masquerading as maximum capacity.

The independently computed connected-component upper bound is the sum of
min(number of mothers, floor(number of candidates/count)) over each bipartite
component, including isolated mothers. This is an upper bound, not generally
an attainable matching. The solver optimizes support only, not financial value.
"""
from __future__ import annotations

from collections import Counter
from numbers import Integral, Real
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


EDGE_COLUMNS = ["event_id", "candidate_id"]
_TOLERANCE = 1e-6


class MatchingCapacityError(RuntimeError):
    """A solver failure/non-certificate; diagnostics must not be called optimal."""

    def __init__(self, message: str, diagnostics: Dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = {**diagnostics, "optimal": False,
                            "solution_verified": False, "failure_reason": message}


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validated_inputs(
    mother_ids: List[str], edges: pd.DataFrame, count: int, time_limit: float,
) -> Tuple[List[str], pd.DataFrame, int, float]:
    if not isinstance(mother_ids, list) or not all(_valid_id(x) for x in mother_ids):
        raise ValueError("mother_ids must be a list of nonempty string IDs")
    if len(mother_ids) != len(set(mother_ids)):
        raise ValueError("Duplicate mother IDs are not allowed")
    if isinstance(count, (bool, np.bool_)) or not isinstance(count, Integral) or count < 1:
        raise ValueError("count must be a positive integer, not bool")
    if isinstance(time_limit, (bool, np.bool_)) or not isinstance(time_limit, Real):
        raise ValueError("time_limit must be a finite positive number, not bool")
    try:
        time_limit = float(time_limit)
    except (ValueError, OverflowError) as exc:
        raise ValueError("time_limit must be finite in solver precision") from exc
    if not np.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("time_limit must be a finite positive number, not bool")
    if not isinstance(edges, pd.DataFrame) or len(edges.columns) != 2 or set(edges.columns) != set(EDGE_COLUMNS):
        raise ValueError("edges must contain only event_id and candidate_id columns")
    frame = edges.loc[:, EDGE_COLUMNS].copy()
    if not all(_valid_id(x) for column in EDGE_COLUMNS for x in frame[column]):
        raise ValueError("Every edge ID must be a nonempty string")
    if frame.duplicated(EDGE_COLUMNS).any():
        raise ValueError("Duplicate edges are not allowed")
    if not set(frame["event_id"]).issubset(mother_ids):
        raise ValueError("Edge event_id is not present in mother_ids")
    frame = frame.sort_values(EDGE_COLUMNS, kind="mergesort").reset_index(drop=True)
    return sorted(mother_ids), frame, int(count), float(time_limit)


def _component_bound(
    mothers: List[str], edge_rows: List[Tuple[str, str]], count: int,
) -> List[Dict[str, Any]]:
    """Graph-only upper bounds, without the optimization matrix or solver."""
    by_mother = {mother: set() for mother in mothers}
    by_candidate: Dict[str, set] = {}
    for mother, candidate in edge_rows:
        by_mother[mother].add(candidate)
        by_candidate.setdefault(candidate, set()).add(mother)
    visited, components = set(), []
    for initial in mothers:
        if initial in visited:
            continue
        pending, component_mothers, component_candidates = [initial], set(), set()
        while pending:
            mother = pending.pop()
            if mother in visited:
                continue
            visited.add(mother)
            component_mothers.add(mother)
            for candidate in by_mother[mother]:
                if candidate not in component_candidates:
                    component_candidates.add(candidate)
                    pending.extend(by_candidate[candidate] - visited)
        components.append({
            "component_id": len(components),
            "mother_ids": sorted(component_mothers),
            "mother_count": len(component_mothers),
            "candidate_count": len(component_candidates),
            "edge_count": sum(len(by_mother[m]) for m in component_mothers),
            "complete_mother_upper_bound": min(len(component_mothers), len(component_candidates) // count),
        })
    return components


def _finite_scalar(value: Any) -> Any:
    """Keep result diagnostics JSON-safe even when a failed solver returns None."""
    try:
        return float(value) if np.ndim(value) == 0 and np.isfinite(float(value)) else None
    except (ValueError, TypeError, OverflowError):
        return None


def maximum_complete_matching(
    mother_ids: List[str], edges: pd.DataFrame, count: int = 3,
    time_limit: float = 30.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Return a verified maximum all-or-none allocation and capacity certificate.

    Inputs may be reordered without affecting the optimum. IDs/edges are sorted
    before solver construction; no financial tie breaker is used. A candidate
    ID can appear in several admissible edges but can be allocated only once.
    Isolated mothers remain in ``mother_count`` and ``unmatched_mother_ids``.
    Other unmatched IDs refer ONLY to the returned optimal allocation: another
    equally optimal allocation may match them. This is no per-mother proof of
    impossibility or a test of whether the optimal allocation is unique.

    Invalid inputs raise ValueError. Non-optimal/time-limited/bad solver results
    raise MatchingCapacityError, whose ``diagnostics`` carries no usable optimum.
    A zero component upper bound is a direct structural zero proof and needs no
    solver invocation. The time limit applies to the solver, not Python checks.
    """
    mothers, frame, count, time_limit = _validated_inputs(mother_ids, edges, count, time_limit)
    edge_rows = list(frame.itertuples(index=False, name=None))
    candidates = sorted(set(frame["candidate_id"]))
    components = _component_bound(mothers, edge_rows, count)
    upper_bound = sum(c["complete_mother_upper_bound"] for c in components)
    summary: Dict[str, Any] = {
        "optimal": False, "solution_verified": False, "mother_count": len(mothers),
        "candidate_count": len(candidates), "edge_count": len(frame),
        "count_per_mother": count, "time_limit": time_limit,
        "mothers_without_edges": sorted(set(mothers) - set(frame["event_id"])),
        "connected_component_count": len(components),
        "connected_component_upper_bound": upper_bound, "components": components,
        "unmatched_ids_scope": "this optimal allocation only; not individual impossibility",
        "solver_called": bool(upper_bound), "solver_status": None,
        "solver_message": None, "solver_objective": None,
        "solver_dual_bound": None, "solver_mip_gap": None, "solver_node_count": None,
    }
    if upper_bound == 0:
        summary.update({"optimal": True, "solution_verified": True,
                        "proof": "empty_edge_graph" if not edge_rows else "component_upper_bound_zero",
                        "matched_mothers": 0,
                        "allocated_controls": 0, "unmatched_mothers": len(mothers),
                        "unmatched_mother_ids": mothers, "complete_mother_upper_bound": 0})
        return pd.DataFrame(columns=EDGE_COLUMNS), summary

    mother_index = {mother: i for i, mother in enumerate(mothers)}
    candidate_index = {candidate: i for i, candidate in enumerate(candidates)}
    edge_count, mother_count = len(edge_rows), len(mothers)
    edge_numbers = np.arange(edge_count)
    # Mother equalities are followed by one <=1 row per candidate. Construct a
    # sparse matrix; a dense mother-by-edge matrix is unnecessary and expensive.
    rows = ([mother_index[m] for m, _ in edge_rows]
            + [mother_count + candidate_index[c] for _, c in edge_rows]
            + list(range(mother_count)))
    columns = list(edge_numbers) + list(edge_numbers) + list(range(edge_count, edge_count + mother_count))
    values = [1.0] * (2 * edge_count) + [-float(count)] * mother_count
    matrix = coo_matrix((values, (rows, columns)),
                        shape=(mother_count + len(candidates), edge_count + mother_count)).tocsc()
    lower = np.zeros(mother_count + len(candidates))
    upper = np.concatenate([np.zeros(mother_count), np.ones(len(candidates))])
    objective = np.concatenate([np.zeros(edge_count), -np.ones(mother_count)])
    try:
        result = milp(c=objective, integrality=np.ones(len(objective), dtype=int),
                      bounds=Bounds(0.0, 1.0), constraints=LinearConstraint(matrix, lower, upper),
                      options={"time_limit": time_limit, "mip_rel_gap": 0.0, "disp": False})
    except Exception as exc:
        raise MatchingCapacityError("MILP invocation failed: {}".format(exc), summary) from exc
    summary.update({
        "solver_status": getattr(result, "status", None),
        "solver_message": str(getattr(result, "message", "")),
        "solver_objective": _finite_scalar(getattr(result, "fun", None)),
        "solver_dual_bound": _finite_scalar(getattr(result, "mip_dual_bound", None)),
        "solver_mip_gap": _finite_scalar(getattr(result, "mip_gap", None)),
        "solver_node_count": _finite_scalar(getattr(result, "mip_node_count", None)),
    })
    status = getattr(result, "status", None)
    if (isinstance(status, (bool, np.bool_)) or not isinstance(status, Integral)
            or status != 0 or not bool(getattr(result, "success", False))):
        raise MatchingCapacityError("MILP did not certify an optimal solution", summary)
    try:
        solution = np.asarray(result.x, dtype=float)
    except (AttributeError, TypeError, ValueError) as exc:
        raise MatchingCapacityError("Invalid solver solution vector", summary) from exc
    if solution.shape != objective.shape or not np.isfinite(solution).all():
        raise MatchingCapacityError("Invalid solver solution vector shape or finiteness", summary)
    rounded = np.rint(solution)
    if ((np.abs(solution - rounded) > _TOLERANCE).any()
            or (rounded < 0).any() or (rounded > 1).any()):
        raise MatchingCapacityError("Solver solution is not binary", summary)
    chosen_edges = [edge_rows[i] for i in range(edge_count) if rounded[i] == 1]
    mother_counts = Counter(m for m, _ in chosen_edges)
    chosen_mothers = {m for m, value in zip(mothers, rounded[edge_count:]) if value == 1}
    if (set(mother_counts) != chosen_mothers
            or any(n != count for n in mother_counts.values())
            or len({c for _, c in chosen_edges}) != len(chosen_edges)
            or not set(chosen_edges).issubset(edge_rows)):
        raise MatchingCapacityError("Solver allocation violates complete groups or non-reuse", summary)
    matched = len(chosen_mothers)
    if matched > upper_bound:
        raise MatchingCapacityError("Solver allocation exceeds component capacity upper bound", summary)
    fun, dual, gap = (summary[k] for k in ("solver_objective", "solver_dual_bound", "solver_mip_gap"))
    if (fun is None or dual is None or gap is None
            or abs(fun + matched) > _TOLERANCE or abs(dual - fun) > _TOLERANCE
            or gap < 0 or gap > _TOLERANCE):
        raise MatchingCapacityError("Solver objective or optimality certificate is inconsistent", summary)
    allocation = pd.DataFrame(chosen_edges, columns=EDGE_COLUMNS)
    summary.update({
        "optimal": True, "solution_verified": True, "proof": "verified_milp_optimum",
        "matched_mothers": matched, "allocated_controls": len(allocation),
        "unmatched_mothers": len(mothers) - matched,
        "unmatched_mother_ids": sorted(set(mothers) - chosen_mothers),
        "complete_mother_upper_bound": min(upper_bound, int(np.floor(-dual + _TOLERANCE))),
    })
    return allocation, summary
