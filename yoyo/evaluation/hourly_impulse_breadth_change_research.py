"""V22 single-variable external rank CHANGE gate on fixed V18 episodes.

Only saved pre2025 native-hour OHLC/ranks from V21 are read; no new exchange
data, BTC execution prices or parameter grid. Freeze713 own contexts before
opening outcomes; keep all251 cases/462 controls/154 triples/97 unmatched.
An exact integer rank-sum difference determines sign. Half-delta in [-1,1]
is a documented positive rescaling for unchanged V21 sign bookkeeping, NOT
the old absolute-mean gate. Validate all eight rank scores before accounting.

Locked pandas2.3.3: explicit usecols selects metadata before any price parse;
exact joins, no shifted-frequency index relabeling or asof filling.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
"""
from __future__ import annotations

from copy import deepcopy
import json
from numbers import Real
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_breadth import BREADTH_SYMBOLS, TRACE_COLUMNS, _utc
from yoyo.data.hourly_impulse_breadth_change import add_breadth_change_context
from yoyo.evaluation import hourly_impulse_breadth_research as parent
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_research import ROOT, digest, utc, write_csv, write_json

EXPERIMENT_ID = "exp-btcusdtp-1h-external-change-preholdout-20260907-v22"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
TRACE_PARENT = "experiments/active/exp-btcusdtp-1h-external-breadth-preholdout-20260906-v21/results"
TRACE_SHA = "870e898c0db830ad7c724bb93726f89b6842e6eb7462b3eac1c56bba03e853e6"
FREEZE_SHA = "bae01b79e34a0782598e18a9197db1853492fe6f04cb92d0b992fb4015700403"
SOURCES = parent.SOURCES + [
    "yoyo/data/hourly_impulse_breadth_change.py",
    "yoyo/evaluation/hourly_impulse_breadth_change_research.py",
    "tests/test_hourly_impulse_breadth_change.py",
    "tests/test_hourly_impulse_breadth_change_research.py",
]


def frozen_config():
    """Return the sole candidate contract; no runtime optimizable argument."""
    config = deepcopy(parent.frozen_config())
    config.update(experiment_id=EXPERIMENT_ID, trace_parent=TRACE_PARENT,
        trace_sha256=TRACE_SHA, trace_freeze_sha256=FREEZE_SHA,
        trace_source="saved_complete_hour_OHLC_rank_not_new_raw5_aggregation")
    config["gate"].update(history="two_adjacent_rank50_windows_union52_contiguous_hours",
        weights="integer_sum_now_minus_integer_sum_previous_then_divide200",
        accept="own_direction_times_raw_rank_sum_change_strictly_positive",
        bookkeeping_score="raw_rank_sum_change_divided400_in_minus1_plus1",
        join="exact_hours_open_Tminus1h_and_Tminus2h_available_T_and_Tminus1h",
        absolute_mean_alignment=False, change_hours=1)
    return config


def verify_config(config, base):
    if config != frozen_config():
        raise ValueError("Frozen V22 contract changed")
    parent.verify_config(parent.frozen_config(), base)


def load_trace(root=ROOT):
    """Pinned pre2025 saved-hour input; timestamp/identity pass before OHLC.

    Read no V21 economics or original raw price archives. The parent freeze
    receipt contains source lineage and context hashes, never trade outcomes.
    Current and previous per-request clocks are enforced by the pure feature.
    """
    folder = root / TRACE_PARENT
    receipt_path, trace_path = folder/"context_frozen.json", folder/"external_hourly_trace.csv.gz"
    if digest(receipt_path) != FREEZE_SHA:
        raise ValueError("V21 pre-outcome freeze hash changed")
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("outcomes_read") is not False or receipt.get("requests") != 713
            or receipt["output_hashes"].get(trace_path.name) != TRACE_SHA):
        raise ValueError("V21 trace lineage contract changed")
    if digest(trace_path) != TRACE_SHA:
        raise ValueError("V21 saved-hour trace bytes changed")
    meta = pd.read_csv(trace_path, usecols=["symbol", "open_time"])
    times = _utc(meta.open_time)
    if (len(meta) != 70168 or set(meta.symbol) != set(BREADTH_SYMBOLS)
            or not times.eq(times.dt.floor("h")).all()
            or times.lt(utc(parent.WARMUP_START)).any()
            or times.ge(utc(parent.PHASE_END)).any()
            or meta.duplicated(["symbol", "open_time"]).any()):
        raise ValueError("Saved-hour identity/time boundary changed before prices")
    frame = pd.read_csv(trace_path, usecols=TRACE_COLUMNS)
    pd.testing.assert_frame_equal(frame[["symbol", "open_time"]], meta[["symbol", "open_time"]])
    return frame, dict(path=str(trace_path.relative_to(root)), sha256=TRACE_SHA,
        parent_freeze_sha256=FREEZE_SHA, saved_hour_rows=len(frame),
        first_hour=times.min(), last_hour=times.max(), raw5_prices_read=False,
        prices_2025_plus_materialized=0, new_intrabar_replays=0)


def validate_change_accounting(context):
    """Validate integer arithmetic before sharing bounded sign bookkeeping.

    Uses only frozen breadth_* ranks/means/change columns and direction.
    Eight scores each [-50,50] eveninteger, all complete for known events;
    any unknown event has no aggregate rank difference, mean, change or score.
    No portfolio or future-return field is used to determine participation.
    """
    fields = [f"breadth_{s}_{p}score" for p in ("", "previous_") for s in BREADTH_SYMBOLS]
    aggregates = ["breadth_raw_sum_change", "breadth_mean_now", "breadth_mean_previous",
        "breadth_change", "breadth_score"]
    required = [*fields, *aggregates, "breadth_known", "breadth_gate_state", "direction"]
    if not set(required).issubset(context):
        raise ValueError("Full V22 rank-change diagnostics required")
    if not context.breadth_known.map(lambda x: isinstance(x, (bool, np.bool_))).all():
        raise ValueError("Known must be boolean")
    known = context.breadth_known.astype(bool)
    for column in fields + aggregates:
        if not context[column].map(lambda v: pd.isna(v) or isinstance(v, Real)
                and not isinstance(v, (bool, np.bool_))).all():
            raise ValueError("Rank/change diagnostics must be real numeric")
    values = context.loc[known, fields].to_numpy(dtype=float)
    if not (np.isfinite(values) & (np.abs(values) <= 50) & (values % 2 == 0)).all():
        raise ValueError("Eight finite even rank50 scores required")
    now, previous = values[:, :4].sum(axis=1), values[:, 4:].sum(axis=1)
    expected = {"breadth_raw_sum_change": now-previous, "breadth_mean_now": now/200,
        "breadth_mean_previous": previous/200, "breadth_change": (now-previous)/200,
        "breadth_score": (now-previous)/400}
    for field, value in expected.items():
        np.testing.assert_allclose(context.loc[known, field].to_numpy(dtype=float), value,
            rtol=0, atol=0, err_msg="V22 exact rank/change scaling: "+field)
        if context.loc[~known, field].notna().any():
            raise ValueError("Unknown aggregate must stay NaN")
    states = pd.Series("unknown", index=context.index)
    states.loc[known] = np.where((now-previous)*context.loc[known, "direction"] > 0,
        "accepted", "abstain")
    if not states.eq(context.breadth_gate_state).all():
        raise ValueError("Integer rank change must determine V22 gate")


def audit_population(trace, mothers, controls, assignments):
    parent.validate_population(mothers, controls, assignments)
    requests = pd.concat([mothers.assign(population="case"), controls.assign(population="control")], ignore_index=True)
    context = add_breadth_change_context(requests, trace)
    pd.testing.assert_frame_equal(context[requests.columns], requests)
    validate_change_accounting(context)
    view = context.rename(columns={"breadth_gate_state": parent.support.GATE_COLUMN})
    values, gates = parent.support.support_gates(view)
    return dict(entry_context=context, counts=parent.support.support_counts(view),
        matched_support=parent.support.matched_support(view, assignments)), dict(
        population={p: parent.support.count_states(view.loc[view.population.eq(p)]) for p in ("case", "control")},
        support_values=values, support_gates=gates, support_pass=all(gates.values()),
        matching=dict(assigned=154, unassigned=97, coverage=154/251, required=.9, pass_gate=False))


def read_outcomes_after_freeze(results, summary, context):
    # Fail before even V18 byte hashing if support did not pass.
    if not summary["support_pass"] or not all(summary["support_gates"].values()):
        raise ValueError("Insufficient support prohibits V22 outcome access")
    validate_change_accounting(context)
    tables, economics = parent.read_outcomes_after_freeze(results, summary, context)
    economics["gate_semantics"] = "rank_sum_change_over400_not_absolute_mean"
    economics["interpretation"] = "Exploratory fixed V18 exit participation by exact external rank change; reused development, not independent validation."
    return tables, economics


def run():
    config_path, plan_path, base_path = EXPERIMENT/"config.json", EXPERIMENT/"PROJECT_PLAN.md", ROOT/parent.BASE_CONFIG
    if digest(base_path) != parent.BASE_SHA256 or digest(ROOT/parent.PINE) != parent.PINE_SHA256:
        raise ValueError("Frozen base/Pine source changed")
    config, base = json.loads(config_path.read_text()), json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, plan_path, base_path])
    for name, sha in parent.INPUTS.items():
        if digest(ROOT/parent.PARENT/name) != sha:
            raise ValueError("Original request input changed")
    mothers, controls, assignments = [parent.read_table(ROOT/parent.PARENT/n) for n in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv")]
    parent.validate_population(mothers, controls, assignments)
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve previous V22 result; refusing overwrite")
    results.mkdir()
    write_json(results/"started.json", dict(at=pd.Timestamp.now(tz="UTC"), sources=sources,
        builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()))
    try:
        trace, receipt = load_trace()
        tables, summary = audit_population(trace, mothers, controls, assignments)
        for name, frame in tables.items():
            write_csv(results/(name+".csv.gz"), frame)
        write_json(results/"context_frozen.json", dict(at=pd.Timestamp.now(tz="UTC"), requests=713,
            outcomes_read=False, input_receipt=receipt,
            output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))}))
        if summary["support_pass"]:
            outputs, economics = read_outcomes_after_freeze(results, summary, tables["entry_context"])
            for name, frame in outputs.items():
                write_csv(results/(name+".csv.gz"), frame)
            summary.update(economics=economics, outcomes_read=True,
                status="fixed_episode_change_gate_not_independent_validation")
        else:
            summary.update(outcomes_read=False, status="insufficient_support_no_outcomes")
        summary.update(experiment_id=EXPERIMENT_ID, generated_at=pd.Timestamp.now(tz="UTC"),
            input_receipt=receipt, sources=sources, config_sha256=digest(config_path),
            output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
            holdout_consumed=False, new_intrabar_replays=0, independent_validation=False,
            training_eligible=False, production_eligible=False, overall_goal_achieved=False)
        write_json(results/"summary.json", summary)
    except Exception as error:
        write_json(results/"failure.json", dict(at=pd.Timestamp.now(tz="UTC"),
            status="failed_not_evidence", error_type=type(error).__name__, message=str(error)))
        raise
    print(json.dumps({k: summary[k] for k in ("status", "population", "support_values", "support_gates", "outcomes_read")}))


if __name__ == "__main__":
    run()
