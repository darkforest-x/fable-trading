"""Independent stdlib V21 saved-support verifier; never load archive prices.

Rebuild every saved native-hour HL2/rank50 from trace OHLC, resetting after
missing hours. Each request selects the hour ending at its own K1 OPEN and
requires all four fixed assets. This verifies saved-hour arithmetic, clocks,
population, support and byte lineage, NOT raw5->hour aggregation, exchange
latency, Pine runtime parity, cached-exit accounting, or profitability.
No strategy/dataframe/research modules are imported. Only support CSVs,
pre-entry request files, JSON receipts and committed source bytes are read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-external-breadth-preholdout-20260906-v21"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
PARENT = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
BASE = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
BASE_SHA = "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"
PINE = "experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/KkoxM97D.pine"
PINE_SHA = "58d49892627a886094b269c7b9d7ac15ae9ba1c0844696fc0cd85ab7856b3ae5"
INPUTS = {
    "original_mothers.csv.gz": "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
    "control_mothers.csv.gz": "01050c7a9602f469406df515edcc73ef2f4c9db2d46529e25030934012eebd5a",
    "assignments.csv": "671782877ee67824f7687243d5e7deae29d78a0bcba6245319ecf55629027b0f",
    "assignment_receipt.json": "1d77ca407712520e645463d30f97d26d452ccce45e87e68c2adcbc4120c43220",
}
SYMBOLS = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
ARCHIVE = "data/kline_preholdout_binance_um5m"
# Exact previously reviewed archive and audit identities; archive bytes are NOT
# opened by this verifier. The audit-byte pins attest the loader's source claim.
EXTERNAL = {
    "ETHUSDT": ("binance_um_ETHUSDT_5m_665856.csv", 665856,
        "8041770149cff3551f84966b1b5f3641f2f731dc3ae7c7d0bfeaf7b24dcd64e8",
        "7f58ccf84648c44a1b7f0b99f8823d663765f94969ead2f903d233114588b2c9"),
    "SOLUSDT": ("binance_um_SOLUSDT_5m_590316.csv", 590316,
        "87a76aa5c36208d862a29016c399d9124365dc93e7cdbd9799e14e2dba8e1165",
        "1af0e67261c9328a7ff75b204470eb6c43d1cafd4bd5142dba9732d51a6242f0"),
    "BNBUSDT": ("binance_um_BNBUSDT_5m_654240.csv", 654240,
        "1cb88e0dc3f82b1176e13ff8a9efeca74c282a55fb4e1adcd6a41141bedea2a8",
        "14b14689e0fd89e51e14c2da7354f17b38157c9d2e9dbcf81deec621e77b5af4"),
    "XRPUSDT": ("binance_um_XRPUSDT_5m_662876.csv", 662876,
        "7bebe067d4dc6e7169bdf30411472178a828aff16c1f527365582e98e69a1f94",
        "aea65c8e187678c09cff2a22d841522f6a12e84a0e1524622b67d64a12edbc7e"),
}
FOLDS = {"2023H1": ("2023-01-01", "2023-07-01"), "2023H2": ("2023-07-01", "2024-01-01"),
         "2024H1": ("2024-01-01", "2024-07-01"), "2024H2": ("2024-07-01", "2025-01-01")}
SUPPORT = {"minimum_events": 80, "minimum_per_fold": 12,
           "minimum_active_months": 12, "minimum_months_per_fold": 3}
STATES = ("accepted", "abstain", "unknown")
CSV_NAMES = {name + ".csv.gz" for name in ("entry_context", "external_hourly_trace", "counts", "matched_support")}
TRACE_COLUMNS = {"symbol", "open_time", "open", "high", "low", "close", "volume",
                 "hl2", "trscore", "count", "window_start", "available_at", "segment_id"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
HOUR = 3600 * 10**9
OUTCOME = re.compile(r"(^|_)(pnl|returns?|mfe|mae|outcome|closed)($|_)", re.I)
SOURCE_FILES = {
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_breadth.py",
    "yoyo/evaluation/hourly_impulse_research.py", "yoyo/evaluation/hourly_impulse_support_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_prior_breakout_research.py",
    "yoyo/evaluation/hourly_impulse_k2_research.py", "yoyo/evaluation/hourly_impulse_management_research.py",
    "yoyo/evaluation/hourly_impulse_failed_launch_research.py", "yoyo/evaluation/hourly_impulse_structure_research.py",
    "yoyo/evaluation/hourly_impulse_structure_accounting.py", "yoyo/evaluation/hourly_impulse_breadth_research.py",
    "yoyo/evaluation/hourly_impulse_breadth_accounting.py", "tests/test_hourly_impulse_breadth.py",
    "tests/test_hourly_impulse_breadth_accounting.py", "tests/test_hourly_impulse_breadth_research.py", PINE,
}


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def number(value, nullable=False):
    if value in (None, ""):
        require(nullable, "Missing number")
        return None
    require(not isinstance(value, bool), "Boolean is not a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise VerificationError("Invalid number") from error
    require(math.isfinite(result), "Nonfinite number")
    return result


def equal_number(actual, expected, message):
    actual = number(actual, nullable=True)
    require((actual is None and expected is None) or
            (actual is not None and expected is not None and
             math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)), message)


def boolean(value):
    if isinstance(value, bool):
        return value
    require(isinstance(value, str) and value in ("True", "False", "true", "false"), "Explicit boolean required")
    return value in ("True", "true")


def stamp(value, nullable=False):
    if value in (None, ""):
        require(nullable, "Missing clock")
        return None
    require(isinstance(value, str), "Explicit ISO timezone clock required")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value)
    require(match is not None, "Explicit ISO timezone clock required")
    dt = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
    delta = dt.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((match[2] or "").ljust(9, "0"))


def day(value):
    return stamp(value + "T00:00:00Z")


def month(value):
    return (EPOCH + timedelta(seconds=stamp(value) // 10**9)).strftime("%Y-%m")


def safe_path(root, identity):
    require(isinstance(identity, str) and identity and not Path(identity).is_absolute()
            and all(p not in ("", ".", "..") for p in identity.split("/")), "Unsafe identity")
    root = Path(root).resolve()
    path = root / identity
    require(root in path.resolve().parents and not any(p.is_symlink() for p in (path, *path.parents)), "Unsafe symlink/path")
    return path


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Missing evidence: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    require(path.is_file() and not path.is_symlink(), "Missing JSON: " + str(path))
    return json.loads(path.read_text(), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(VerificationError("Nonfinite JSON")))


def read_csv(path):
    require(path.is_file() and not path.is_symlink(), "Missing support CSV")
    with (gzip.open if path.suffix == ".gz" else open)(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames
        require(names and len(names) == len(set(names)), "Duplicate/missing CSV header")
        require(not any(OUTCOME.search(c) or c in {"exit_time", "exit_price", "net_r"} for c in names),
                "Outcome columns forbidden in support reader")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), "Ragged CSV")
    return rows


def indexed(rows):
    result = {}
    for row in rows:
        key = row.get("event_id")
        require(isinstance(key, str) and key.strip() and key not in result, "Duplicate/missing event_id")
        result[key] = row
    return result


def parity(before, after):
    """Exact old IDs/order/columns and nanosecond clocks; numeric CSV tolerance."""
    require([r["event_id"] for r in before] == [r["event_id"] for r in after], "Original request order/identity changed")
    right = indexed(after)
    for row in before:
        other = right[row["event_id"]]
        require(row.keys() <= other.keys(), "Original request column lost")
        for key, value in row.items():
            if key.endswith(("_time", "_available", "_at", "_deadline", "_until", "_bar_open")):
                require(stamp(value, True) == stamp(other[key], True), "Original request clock changed: " + key)
            elif value != other[key]:
                equal_number(other[key], number(value, True), "Original request value changed: " + key)


def rebuild_trace(trace, cutoff):
    """Recompute scores from saved hourly high/low, never trust saved HL2/ranks."""
    result = {symbol: {} for symbol in SYMBOLS}
    previous_key = None
    history = deque(maxlen=51)
    previous_symbol = previous_time = None
    segment = -1
    count = 0
    for row in trace:
        require(set(row) == TRACE_COLUMNS, "Trace schema changed")
        symbol = row["symbol"]
        require(symbol in SYMBOLS, "Non-fixed external asset/BTC in trace")
        time = stamp(row["open_time"])
        key = (SYMBOLS.index(symbol), time)
        require(previous_key is None or key > previous_key, "Trace duplicates or wrong fixed order")
        require(time % HOUR == 0 and day("2022-12-29") <= time and time + HOUR <= cutoff,
                "Trace contains incomplete/future/out-of-window hour")
        if symbol != previous_symbol:
            segment, count = -1, 0
        if symbol != previous_symbol or time != previous_time + HOUR:
            segment += 1
            count = 0
            history.clear()
        count += 1
        o, hi, lo, c, volume = (number(row[k]) for k in ("open", "high", "low", "close", "volume"))
        require(min(o, hi, lo, c) > 0 and lo <= min(o, c) <= max(o, c) <= hi and volume >= 0,
                "Invalid saved hourly OHLCV")
        hl2 = (hi + lo) / 2
        require(math.isfinite(hl2), "Invalid derived HL2")
        history.append(hl2)
        score = sum(1 if hl2 >= old else -1 for old in list(history)[:-1]) if count >= 51 else None
        window = time - 50 * HOUR if score is not None else None
        for field, expected in (("hl2", hl2), ("trscore", score), ("count", count), ("segment_id", segment)):
            equal_number(row[field], expected, "Trace arithmetic/reset drift: " + field)
        require(stamp(row["available_at"]) == time + HOUR and stamp(row["window_start"], True) == window,
                "Trace availability/51-hour window drift")
        result[symbol][time] = dict(score=score, count=count, available_at=time+HOUR, window_start=window)
        previous_key, previous_symbol, previous_time = key, symbol, time
    return result


def context_facts(context, trace):
    lookup = indexed(context)
    require(context, "All original requests missing")
    require(not any(key.startswith("structure_") for row in context for key in row), "V20 gate must not be stacked")
    cutoff = max(stamp(row["signal_time"]) for row in context)
    assets = rebuild_trace(trace, cutoff)
    for row in context:
        signal, decision = stamp(row["signal_time"]), stamp(row["decision_time"])
        direction = number(row["direction"])
        require(signal % HOUR == 0 and decision == signal + HOUR and direction in (-1, 1), "Own K1 clock/direction invalid")
        require(row["population"] in ("case", "control") and row["fold"] in FOLDS, "Invalid population/fold")
        start, end = FOLDS[row["fold"]]
        require(day(start) <= decision < day(end)-72*HOUR, "Request outside development fold/72h embargo")
        scores, missing = [], False
        for symbol in SYMBOLS:
            source = assets[symbol].get(signal-HOUR)
            missing |= source is None
            values = source or dict(score=None, count=0, available_at=None, window_start=None)
            for field, expected in values.items():
                value = row["breadth_"+symbol+"_"+field]
                if field in ("available_at", "window_start"):
                    require(stamp(value, True) == expected, "Own external clock/lag mismatch")
                else:
                    equal_number(value, expected, "Own external score/count mismatch")
            if values["score"] is not None:
                scores.append(values["score"])
        known = len(scores) == 4
        score = sum(scores)/200 if known else None
        state = "unknown" if not known else "accepted" if direction*score > 0 else "abstain"
        reason = ("neutral" if score == 0 else "known") if known else "missing_external_hour" if missing else "insufficient_history"
        require(boolean(row["breadth_known"]) == known and row["breadth_gate_state"] == state
                and row["breadth_reason"] == reason, "Own gate/zero/unknown semantics changed")
        equal_number(row["breadth_score"], score, "Four fixed assets/unrounded normalization changed")
        equal_number(row["breadth_source_count"], len(scores), "Unknown asset omitted from denominator")
        require(stamp(row["breadth_cutoff"]) == signal and
                stamp(row["breadth_available_at"], True) == (signal if known else None), "Gate cutoff/availability changed")
    return lookup


def state_counts(rows):
    counts = Counter(row["breadth_gate_state"] for row in rows)
    return dict(total=len(rows), **{state: counts[state] for state in STATES})


def verify_tables(context, trace, counts, matched, summary, *, expected_counts=(251, 462, 154)):
    """Pure saved-table API; small synthetic fixtures may override population sizes."""
    require(isinstance(summary, dict), "Complete summary required")
    lookup = context_facts(context, trace)
    n, m, k = expected_counts
    population = {p: [row for row in context if row["population"] == p] for p in ("case", "control")}
    require((len(population["case"]), len(population["control"])) == (n, m), "Original population denominator changed")
    groups, control_times = defaultdict(list), set()
    for row in population["control"]:
        parent = lookup.get(row["parent_event_id"])
        require(parent and parent["population"] == "case" and parent["fold"] == row["fold"]
                and number(parent["direction"]) == number(row["direction"]), "Control parent/fold/direction changed")
        time = stamp(row["decision_time"])
        require(time not in control_times, "Control time reused")
        control_times.add(time)
        groups[row["parent_event_id"]].append(row)
    require(len(groups) == k and all(len(part) == 3 for part in groups.values()), "Original three-or-zero control mapping changed")
    pairs = indexed(matched)
    require(pairs.keys() == groups.keys(), "Matched support omitted/rematched original triples")
    for parent, part in groups.items():
        pair, case = pairs[parent], lookup[parent]
        require(pair["fold"] == case["fold"] and pair["case_state"] == case["breadth_gate_state"]
                and pair["control_ids"] == "|".join(sorted(row["event_id"] for row in part)), "Matched identity/state changed")
        for field, value in state_counts(part).items():
            equal_number(pair["control_"+field], value, "Matched support count changed")
        require(boolean(pair["all_known"]) == (case["breadth_gate_state"] != "unknown" and
                all(row["breadth_gate_state"] != "unknown" for row in part)), "Matched unknown coerced to known")
    dimensions = {"all": ["all"], "fold": list(FOLDS), "direction": ["1", "-1"],
                  "month": [f"{year}-{mo:02d}" for year in (2023, 2024) for mo in range(1, 13)]}
    actual = {(row["population"], row["dimension"], row["key"]): row for row in counts}
    expected = {}
    for pop, rows in population.items():
        for dimension, keys in dimensions.items():
            for key in keys:
                part = [r for r in rows if dimension == "all" or
                        (str(int(number(r["direction"]))) if dimension == "direction" else
                         month(r["decision_time"]) if dimension == "month" else r["fold"]) == key]
                values = state_counts(part)
                values["accepted_rate"] = values["accepted"]/len(part) if part else None
                expected[(pop, dimension, key)] = values
    require(len(actual) == len(counts) and actual.keys() == expected.keys(), "Count table omitted/duplicated fixed empty folds/months")
    for key, values in expected.items():
        for field, value in values.items():
            equal_number(actual[key][field], value, "Support count/rate denominator changed")
    totals = {p: state_counts(rows) for p, rows in population.items()}
    require(summary["population"] == totals, "Summary population counts changed")
    admitted = [r for r in population["case"] if r["breadth_gate_state"] == "accepted"]
    folds = {f: [r for r in admitted if r["fold"] == f] for f in FOLDS}
    values = dict(events=len(admitted), minimum_fold_events=min(map(len, folds.values())),
                  active_months=len({month(r["decision_time"]) for r in admitted}),
                  minimum_fold_months=min(len({month(r["decision_time"]) for r in rows}) for rows in folds.values()))
    gates = dict(minimum_events=values["events"] >= 80, minimum_per_fold=values["minimum_fold_events"] >= 12,
                 minimum_active_months=values["active_months"] >= 12, minimum_months_per_fold=values["minimum_fold_months"] >= 3)
    require(summary["support_values"] == values and summary["support_gates"] == gates and
            summary["support_pass"] is all(gates.values()), "Support threshold/decision changed")
    require(summary["matching"] == dict(assigned=k, unassigned=n-k, coverage=k/n, required=.9, **{"pass": k/n >= .9}),
            "Fixed matching ceiling changed")
    read = all(gates.values())
    require(summary["outcomes_read"] is read and summary["status"] ==
            ("fixed_episode_gate_comparison_not_independent_validation" if read else "insufficient_support_no_outcomes"),
            "Outcome access inconsistent with frozen support")
    for flag in ("holdout_consumed", "independent_validation", "training_eligible", "production_eligible", "overall_goal_achieved"):
        require(summary[flag] is False, "Support audit falsely promoted: " + flag)
    equal_number(summary["new_intrabar_replays"], 0, "Unexpected new replay")
    return dict(status="passed", population=totals, support_values=values, support_gates=gates,
                support_pass=read, matched_groups=k, unmatched=n-k, count_rows=len(counts),
                saved_hourly_rows=len(trace), saved_hourly_scores_recomputed=True,
                raw5_aggregation_recomputed=False, archive_price_bytes_read=False,
                economic_accounting_verified=False, live_availability_verified=False,
                limitation="Saved hourly arithmetic and support only; raw aggregation, cached exits, live latency and profitability are not independently verified.")


def verify_config(config):
    require(config["experiment_id"] == EXPERIMENT_ID and config["parent_requests"] == PARENT
            and config["request_inputs"] == INPUTS and config["base_config"] == BASE
            and config["base_config_sha256"] == BASE_SHA, "Frozen request/base identities changed")
    require(config["phase_end_exclusive"] == "2025-01-01" and config["warmup_start"] == "2022-12-29"
            and config["development_folds"] == [[f, a, b] for f, (a, b) in FOLDS.items()]
            and config["support"] == SUPPORT and config["archive"] == ARCHIVE, "Frozen source window/support changed")
    sources = {s: dict(zip(("file", "rows", "sha256", "audit_sha256"), values)) for s, values in EXTERNAL.items()}
    require(config["external_sources"] == sources, "Fixed external source pins changed")
    gate = dict(rank_length=50, source="native_complete_1h_HL2",
        comparison="current_hl2_greater_or_equal_lag_adds_one_else_minus_one", history="51_consecutive_complete_hours_per_asset",
        universe=list(SYMBOLS), weights="equal_mean_scores_divided_by50", cutoff="own_K1_open", lag_hours_before_entry=1,
        join="exact_last_hour_available_at_equals_own_signal_time", accept="own_direction_times_breadth_score_strictly_positive",
        zero="known_abstain", missing_any_asset="unknown_NaN", forward_fill=False, nz_price_fill=False, length_search=False,
        extra_structure_ma_volume_gate=False, own_controls=True, pine_runtime_parity_verified=False, live_feed_latency_verified=False)
    require(config["gate"] == gate, "Frozen source formula/clock changed")
    execution = dict(policy="15m_native40_failed_confirm2", cost_fraction=.002, max_hours=72, stop="K1_extreme",
        execution_source="original_OKX_BTC_cached_V18_episodes", new_intrabar_replays=0, serial_recomputed_per_arm=True)
    require(config["fixed_execution"] == execution and config["matching_coverage_required"] == .9, "Frozen execution/coverage changed")
    require(config["expected"] == dict(mothers=251, controls=462, matched=154,
            status_counts={"matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3}),
            "Frozen251/462/154/97 config changed")
    require(config["outcome_read_rule"] == "only_after713context_freeze_and_all_support_gates_pass", "Outcome access rule changed")
    for flag in ("holdout_consumed", "training_eligible", "production_eligible"):
        require(config[flag] is False, "Config eligibility drift")


def verify_sources(root, started, summary, config):
    require(started["sources"] == summary["sources"] and started["sources"], "Source receipts missing/mismatched")
    commit = started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit), "Invalid committed builder")
    pins = {item["path"]: item["sha256"] for item in started["sources"]}
    required = SOURCE_FILES | {EXPERIMENT_PATH+"/config.json", EXPERIMENT_PATH+"/PROJECT_PLAN.md", BASE}
    require(len(pins) == len(started["sources"]) and required <= pins.keys(), "Missing/duplicate committed source")
    for identity, expected in pins.items():
        safe_path(root, identity)
        require(not identity.startswith("data/") and re.fullmatch(r"[a-f0-9]{64}", expected), "Invalid source identity/hash")
        try:
            content = subprocess.run(["git", "show", commit+":"+identity], cwd=root, check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as error:
            raise VerificationError("Committed source unavailable; cannot skip") from error
        require(hashlib.sha256(content).hexdigest() == expected, "Committed source byte mismatch")
    seconds = subprocess.run(["git", "show", "-s", "--format=%ct", commit], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    require(seconds.isdigit() and int(seconds)*10**9 <= stamp(started["at"]), "Run predates builder commit")
    require(pins[EXPERIMENT_PATH+"/config.json"] == summary["config_sha256"]
            and pins[BASE] == config["base_config_sha256"] and pins[PINE] == PINE_SHA, "Config/Pine/source pin disagreement")
    return len(pins)


def verify_receipts(root, receipts, config, cutoff):
    require(set(receipts) == set(SYMBOLS), "Four source receipts required")
    for symbol, values in EXTERNAL.items():
        filename, rows, archive_sha, audit_sha = values
        receipt = receipts[symbol]
        audit_path = safe_path(root, ARCHIVE+"/audits/"+symbol+".json")
        require(sha(audit_path) == audit_sha, "External audit bytes changed")
        audit = read_json(audit_path)
        require(audit["symbol"] == symbol and audit["status"] == "complete" and audit["rows"] == rows
                and audit["output_sha256"] == archive_sha and audit["holdout_ohlcv_rows_materialized"] == 0
                and stamp(audit["last_time"]) < day("2026-05-01"), "External audit metadata changed")
        require(receipt["symbol"] == symbol and receipt["path"] == ARCHIVE+"/series/"+filename
                and receipt["sha256"] == archive_sha and receipt["audit_sha256"] == audit_sha
                and receipt["physical_rows"] == rows, "External source receipt identity changed")
        first, last = stamp(receipt["first_price_time"]), stamp(receipt["last_price_time"])
        require(first % (HOUR//12) == last % (HOUR//12) == 0 and day("2022-12-29") <= first <= last < cutoff
                and stamp(receipt["price_end_exclusive"]) == cutoff <= day("2025-01-01"), "External price materialization boundary changed")
        for field in ("price_rows_2025_plus_materialized", "holdout_ohlcv_rows_materialized"):
            equal_number(receipt[field], 0, "Forbidden external prices materialized")
        require(receipt["execution_source_unchanged"] is True and receipt["timestamp_and_hash_preflight"] is True,
                "External feed substituted execution or skipped preflight")
        count, skip = number(receipt["price_rows_materialized"]), number(receipt["skipped_before_warmup_rows"])
        require(count.is_integer() and skip.is_integer() and 0 < count <= (last-first)//(HOUR//12)+1
                and 0 <= skip <= rows-count, "External source receipt row arithmetic invalid")


def verify(directory=None, *, root=ROOT):
    root = Path(root).resolve()
    results = Path(directory) if directory is not None else root/EXPERIMENT_PATH/"results"
    if not results.is_absolute():
        results = root/results
    require(results.resolve() == root/EXPERIMENT_PATH/"results", "Unexpected results identity")
    require(not (results/"failure.json").exists(), "Failed run is not evidence")
    experiment = results.parent
    config, summary, started, frozen = (read_json(p) for p in
        (experiment/"config.json", results/"summary.json", results/"started.json", results/"context_frozen.json"))
    verify_config(config)
    require(summary["experiment_id"] == EXPERIMENT_ID and sha(experiment/"config.json") == summary["config_sha256"], "Wrong summary/config bytes")
    require(sha(safe_path(root, BASE)) == BASE_SHA, "Frozen base bytes changed")
    source_count = verify_sources(root, started, summary, config)
    hashes = summary["output_hashes"]
    actual_csv = {p.name for p in results.glob("*.csv.gz")}
    require(set(hashes) == actual_csv and CSV_NAMES <= hashes.keys() and set(frozen["output_hashes"]) == CSV_NAMES,
            "Output hash coverage incomplete/changed")
    for name, expected in hashes.items():
        require(sha(safe_path(results, name)) == expected, "Saved output byte mismatch: "+name)
    require(all(hashes[name] == value for name, value in frozen["output_hashes"].items())
            and frozen["requests"] == 713 and frozen["outcomes_read"] is False, "All713 context was not frozen before outcomes")
    require(stamp(started["at"]) <= stamp(frozen["at"]) <= stamp(summary["generated_at"]), "Freeze clock invalid")
    require(frozen["external_receipts"] == summary["external_receipts"], "Frozen source receipts changed")
    # Support inputs only; no V18 outcome file is ever opened or hashed here.
    for name, expected in INPUTS.items():
        require(sha(safe_path(root, PARENT+"/"+name)) == expected, "Frozen pre-entry input changed")
    context = read_csv(results/"entry_context.csv.gz")
    for population, filename in (("case", "original_mothers.csv.gz"), ("control", "control_mothers.csv.gz")):
        parity(read_csv(safe_path(root, PARENT+"/"+filename)), [r for r in context if r["population"] == population])
    assignments = indexed(read_csv(safe_path(root, PARENT+"/assignments.csv")))
    require(assignments.keys() == {r["event_id"] for r in context if r["population"] == "case"}
            and Counter(r["match_status"] for r in assignments.values()) ==
            {"matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3}, "Frozen154/97 mapping changed")
    matched = read_csv(results/"matched_support.csv.gz")
    require({r["event_id"] for r in matched} == {key for key, row in assignments.items() if row["match_status"] == "matched"}, "Matched set changed")
    output = verify_tables(context, read_csv(results/"external_hourly_trace.csv.gz"), read_csv(results/"counts.csv.gz"), matched, summary)
    cutoff = max(stamp(r["signal_time"]) for r in context)
    verify_receipts(root, summary["external_receipts"], config, cutoff)
    marker = results/"outcomes_started.json"
    if output["support_pass"]:
        access = read_json(marker)
        require(stamp(frozen["at"]) <= stamp(access["at"]) <= stamp(summary["generated_at"])
                and access["frozen_context_sha256"] == sha(results/"context_frozen.json")
                and access["new_intrabar_replays"] == 0, "Outcomes preceded immutable support freeze")
    else:
        require(not marker.exists() and set(hashes) == CSV_NAMES and "economics" not in summary, "Failed support nevertheless accessed outcomes")
    output.update(experiment_id=EXPERIMENT_ID, builder_commit=started["builder_commit"],
        committed_sources_verified=source_count, output_hashes_verified=len(hashes),
        fixed_request_hashes_verified=len(INPUTS), external_audit_hashes_verified=4,
        summary_sha256=sha(results/"summary.json"), context_frozen_sha256=sha(results/"context_frozen.json"),
        auditor_sha256=sha(Path(__file__)), independent_source_authenticity_verified=False)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path(EXPERIMENT_PATH)/"results")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.results, root=args.root)
    except (VerificationError, ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps(dict(status="failed", error=str(error)), ensure_ascii=False))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.out is not None:
        # Explicit independent receipt only; never overwrite research evidence.
        target = args.out if args.out.is_absolute() else Path(args.root)/args.out
        require(not target.exists(), "Preserve existing independent verification")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered+"\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
