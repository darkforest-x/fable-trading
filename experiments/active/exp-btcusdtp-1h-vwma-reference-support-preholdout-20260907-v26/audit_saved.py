"""Independent, standard-library-only V26 saved-hourly support audit.

No strategy/feature/runner imports, raw5 archive, labels, trades or returns.
Recompute causal fixed40 references, RMA14 and shape from saved V20 OHLCV;
independently enumerate the fixed complete opportunity grid and entry rules.
This does NOT verify raw5 aggregation, exchange/source authenticity, executable
fills, profitability, or live Pine parity. Hash/chronology checks validate saved
receipts and committed bytes, not an independent observation of past execution.

Numeric checks use relative 2e-12 / absolute 2e-10 tolerance for independently
summed floats; discrete sides, entry identities and clocks are exact. Equal
positive weights reduce mathematically to SMA, including the equality tie.
References: https://docs.python.org/3.9/library/math.html#math.fsum
https://docs.python.org/3.9/library/datetime.html#datetime.datetime.fromisoformat
https://www.tradingview.com/support/solutions/43000592293-volume-weighted-moving-average-vwma/
HL2, not the published example's close, is the frozen experiment price source.
Run --self-test-only without reading any study files. Normal CLI requires this
auditor's bytes already committed, and creates audit.json without overwriting.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
E = Path("experiments/active/exp-btcusdtp-1h-vwma-reference-support-preholdout-20260907-v26")
BUILDER = "374f003f3fb9b0812edb25636042654312b06f1e"
CONFIG_SHA = "c1046cb32d4c3567d2a436a33015cd9c67ec0ef6a492c052b3ac69d1862f9218"
FREEZE_SHA = "3df6f7d89b5b9978e2fcc78023bdf8ab8b75d96da80c9dc5954e61c94d1df973"
TRACE = Path("experiments/active/exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20/results/hourly_trace.csv.gz")
MOTHERS = Path("experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz")
FOLDS = (("2023H1", "2023-01-01", "2023-07-01"),
         ("2023H2", "2023-07-01", "2024-01-01"),
         ("2024H1", "2024-01-01", "2024-07-01"),
         ("2024H2", "2024-07-01", "2025-01-01"))
HOUR = timedelta(hours=1)
UTC = timezone.utc
OHLC = ("open", "high", "low", "close")
FEATURES = ("hl2", "ma", "atr", "ma_side", "ma_slope_atr", "body_ratio", "range_atr",
            "long_close_location", "short_close_location", "volume_ratio", "bullish_engulf",
            "bearish_engulf", "cross_count24", "efficiency24", "prior_high20", "prior_low20",
            "prior_range_median20")
ENTRY = ("event_id", "signal_time", "decision_time", "direction", "signal_open", "signal_high",
         "signal_low", "signal_close", "initial_stop", "signal_atr", "ma", "ma_side", "body_ratio",
         "range_atr", "close_location", "volume_ratio", "ma_slope_atr", "cross_count24", "efficiency24",
         "is_engulf", "breakout20", "extension_atr", "fold")
OUTPUTS = {n+".csv.gz" for n in ("opportunities", "shape_opportunities", "counts", "sma_entries",
                                "vwma_entries", "hourly_sma", "hourly_vwma")}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(value, hour=False):
    require(isinstance(value, str), "Timestamp is not an explicit string")
    # datetime only retains microseconds: reject hidden nanoseconds, don't truncate.
    require(not re.search(r"\.\d{7,}", value), "Submicrosecond timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid timestamp") from error
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0), "Explicit UTC required")
    if hour:
        require((result.minute, result.second, result.microsecond) == (0, 0, 0), "Native UTC hour required")
    return result


def number(value, nullable=False):
    if nullable and (value is None or str(value).lower() in ("", "nan", "none")):
        return None
    require(not isinstance(value, bool) and str(value).lower() not in ("true", "false"), "Boolean numeric value")
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid numeric value") from error
    require(math.isfinite(result), "Nonfinite numeric value")
    return result


def check(actual, expected, label):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label+": keys")
        for key, value in expected.items():
            check(actual[key], value, label+"/"+str(key))
    elif expected is None:
        require(number(actual, nullable=True) is None, label+": expected unknown")
    elif isinstance(expected, bool):
        require(actual is expected or actual == str(expected), label+": boolean")
    elif isinstance(expected, datetime):
        require(stamp(actual) == expected, label+": clock")
    elif isinstance(expected, int):
        require(number(actual) == expected, label+": integer")
    elif isinstance(expected, float):
        require(math.isclose(number(actual), expected, rel_tol=2e-12, abs_tol=2e-10), label+": float")
    else:
        require(actual == expected, label+": value")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and len(reader.fieldnames) == len(set(reader.fieldnames)), "CSV schema")
        data = list(reader)
        require(all(None not in row for row in data), "CSV row overflow")
        return data


def indexed(data, key):
    result = {}
    for row in data:
        value = row[key]
        require(value is not None and str(value).strip() and value not in result, "Null/duplicate "+key)
        result[value] = row
    return result


def quotient(a, b):
    return None if a is None or b is None or b == 0 else a/b


def recompute(source, reference):
    """Independent list/window implementation; no saved derived column used."""
    require(reference in ("SMA", "VWMA"), "Unknown fixed reference")
    output, history, previous_time, segment = [], [], None, -1
    for saved in source:
        t = stamp(saved["open_time"], hour=True)
        require(previous_time is None or t > previous_time, "Source timestamp order/duplicate")
        if previous_time is None or t-previous_time != HOUR:
            history, segment = [], segment+1
        previous_time = t
        q = {key:number(saved[key]) for key in OHLC}
        o, h, low, c = (q[key] for key in OHLC)
        require(low > 0 and low <= min(o, c) <= max(o, c) <= h, "Source OHLC bounds")
        v = number(saved.get("volume"), nullable=True)
        require(v is None or v >= 0, "Negative volume in fixed source")
        if "segment_id" in saved:
            check(saved["segment_id"], segment, "Source hourly segment")
        q.update(open_time=t, volume=v, segment_id=segment, hl2=(h+low)/2)
        n = len(history)
        prev = history[-1] if n else None
        span = h-low
        q["tr"] = max(span, abs(h-prev["close"]), abs(low-prev["close"])) if prev else span
        window = history[-39:]+[q]
        volumes = [r["volume"] for r in window]
        valid_count = sum(x is not None for x in volumes)
        volume_sum = math.fsum(volumes) if len(window) == valid_count == 40 else None
        average = math.fsum(r["hl2"] for r in window)/40 if len(window) == 40 else None
        ma = average
        if reference == "VWMA":
            ma = None
            if volume_sum is not None and volume_sum > 0:
                ma = (average if min(volumes) == max(volumes) else
                      math.fsum(r["hl2"]*r["volume"] for r in window)/volume_sum)
        q["ma"] = ma
        q["atr"] = (None if n < 13 else math.fsum(r["tr"] for r in history+[q])/14 if n == 13
                    else (history[-1]["atr"]*13+q["tr"])/14)
        q["ma_side"] = 0 if ma is None else 1 if q["hl2"] >= ma else -1
        q["ma_slope_atr"] = quotient(ma-history[-3]["ma"], 3*q["atr"]) if (
            ma is not None and n >= 3 and history[-3]["ma"] is not None and q["atr"] is not None) else None
        q["body_ratio"], q["range_atr"] = quotient(abs(c-o), span), quotient(span, q["atr"])
        q["long_close_location"], q["short_close_location"] = quotient(c-low, span), quotient(h-c, span)
        prior_v = [r["volume"] for r in history[-20:]]
        q["volume_ratio"] = quotient(v, math.fsum(prior_v)/20) if n >= 20 and all(x is not None for x in prior_v) else None
        q["bullish_engulf"] = bool(prev and c > o and prev["close"] < prev["open"] and
                                   o <= prev["close"] and c >= prev["open"] and (o < prev["close"] or c > prev["open"]))
        q["bearish_engulf"] = bool(prev and c < o and prev["close"] > prev["open"] and
                                   o >= prev["close"] and c <= prev["open"] and (o > prev["close"] or c < prev["open"]))
        q["flip"] = bool(prev and q["ma_side"]*prev["ma_side"] < 0)
        q["cross_count24"] = sum(r["flip"] for r in history[-24:]) if n >= 24 else None
        last = history[-24:]+[q]
        denominator = math.fsum(abs(b["close"]-a["close"]) for a,b in zip(last,last[1:]))
        q["efficiency24"] = (abs(c-last[0]["close"])/denominator if denominator else 0.) if n >= 24 else None
        q["prior_high20"] = max(r["high"] for r in history[-20:]) if n >= 20 else None
        q["prior_low20"] = min(r["low"] for r in history[-20:]) if n >= 20 else None
        q["prior_range_median20"] = statistics.median(r["high"]-r["low"] for r in history[-20:]) if n >= 20 else None
        reason = ("warmup" if n < 39 else "invalid_volume" if reference == "VWMA" and valid_count < 40 else
                  "zero_volume_sum" if reference == "VWMA" and volume_sum == 0 else "known")
        q.update(reference_known=ma is not None, reference_reason=reason, reference_available_at=t+HOUR,
                 reference_window_start=t-39*HOUR, reference_count=len(window), reference_volume_valid=v is not None,
                 reference_volume_valid_count=valid_count, reference_volume_invalid_count=len(window)-valid_count,
                 reference_volume_sum=volume_sum)
        history.append(q)
        output.append(q)
    return output


def shape(q, direction):
    if q is None:
        return "unknown", "missing_signal_hour"
    location = q["long_close_location" if direction == 1 else "short_close_location"]
    if any(q[k] is None for k in ("atr", "body_ratio", "range_atr")) or location is None or q["atr"] <= 0:
        return "unknown", "shape_normalization_unavailable"
    engulf = q["bullish_engulf" if direction == 1 else "bearish_engulf"]
    ok = ((q["body_ratio"] >= .65 and q["range_atr"] >= 1) or (engulf and q["range_atr"] >= .65)) and location >= .7
    return ("qualified", "shape_qualified") if ok else ("not_qualified", "shape_rejected")


def entry_gates(q, direction):
    """Only fixed active entry conditions; no discretionary explanation."""
    extension = direction*(q["close"]-q["ma"])/q["atr"]
    return {"body_strict_cross": direction*(q["open"]-q["ma"]) < 0 < direction*(q["close"]-q["ma"]),
            "hl2_side": q["ma_side"] == direction, "extension_0_to_99": 0 <= extension <= 99,
            "cross_count_le_999": q["cross_count24"] <= 999}


def state(q, direction):
    s, why = shape(q, direction)
    if s == "unknown":
        return "unknown", why
    if s == "not_qualified":
        return "abstain", "shape_rejected"
    if not q["reference_known"]:
        return "unknown", q["reference_reason"]
    if q["cross_count24"] is None:
        return "unknown", "active_entry_feature_unavailable"
    return ("accepted", "entry_accepted") if all(entry_gates(q,direction).values()) else ("abstain", "reference_or_active_gate_rejected")


def entry_row(request, q):
    d = request["direction"]
    answer = {k:q[k] for k in ("ma", "ma_side", "body_ratio", "range_atr", "volume_ratio", "cross_count24", "efficiency24")}
    answer.update({k:request[k] for k in ("event_id", "signal_time", "decision_time", "direction", "fold")})
    answer.update({"signal_"+k:q[k] for k in OHLC})
    answer.update(initial_stop=q["low" if d == 1 else "high"], signal_atr=q["atr"],
                  close_location=q["long_close_location" if d == 1 else "short_close_location"],
                  ma_slope_atr=None if q["ma_slope_atr"] is None else d*q["ma_slope_atr"],
                  is_engulf=q["bullish_engulf" if d == 1 else "bearish_engulf"],
                  breakout20=q["close"] > q["prior_high20"] if d == 1 else q["close"] < q["prior_low20"],
                  extension_atr=d*(q["close"]-q["ma"])/q["atr"])
    return answer


def grid():
    for fold, start, end in FOLDS:
        e = stamp(start+"T00:00:00Z")
        stop = stamp(end+"T00:00:00Z")-72*HOUR
        while e < stop:
            signal = e-HOUR
            for direction in (1,-1):
                yield dict(event_id=signal.isoformat()+ ("_L" if direction == 1 else "_S"),
                           signal_time=signal, decision_time=e, direction=direction, fold=fold, month=e.strftime("%Y-%m"))
            e += HOUR


def verify_tables(source, tables, original, summary):
    """Pure data API; no I/O and no imports from the implementation under audit."""
    recomputed = {arm:recompute(source, reference) for arm,reference in (("sma","SMA"),("vwma","VWMA"))}
    maps = {arm:{r["open_time"]:r for r in data} for arm,data in recomputed.items()}
    for arm,data in recomputed.items():
        saved = tables["hourly_"+arm]
        require(len(saved) == len(data), "Hourly count "+arm)
        for got, want in zip(saved,data):
            for key, value in want.items():
                if key not in ("tr", "flip"):
                    check(got[key],value,arm+"/"+str(want["open_time"])+"/"+key)
    expected, entries = [], {"sma":{},"vwma":{}}
    reason_counts = {"sma_only":Counter(),"vwma_only":Counter()}
    for request in grid():
        event, d, t = request["event_id"],request["direction"],request["signal_time"]
        a,b = maps["sma"].get(t),maps["vwma"].get(t)
        shape_status,shape_reason = shape(a,d)
        q = dict(request,shape_status=shape_status,shape_reason=shape_reason)
        for arm, row in (("sma",a),("vwma",b)):
            q[arm+"_state"],q[arm+"_reason"] = state(row,d)
            for key in ("ma","ma_side","ma_slope_atr","cross_count24","reference_known","reference_reason"):
                q[arm+"_"+key] = row[key] if row is not None else None
            if q[arm+"_state"] == "accepted":
                entries[arm][event] = entry_row(request,row)
        x,y = q["sma_state"],q["vwma_state"]
        q["overlap"] = ("any_unknown" if "unknown" in (x,y) else "both_accepted" if x == y == "accepted" else
                        "sma_only" if x == "accepted" else "vwma_only" if y == "accepted" else "both_abstain")
        q["reference_delta_atr"] = quotient(b["ma"]-a["ma"],a["atr"]) if (
            a and b and a["ma"] is not None and b["ma"] is not None) else None
        if q["overlap"] in reason_counts:
            rejected = b if q["overlap"] == "sma_only" else a
            failed = "+".join(k for k,v in entry_gates(rejected,d).items() if not v)
            require(failed, "Exclusive entry has no rejecting gate")
            reason_counts[q["overlap"]][failed] += 1
        expected.append(q)
    got_grid = indexed(tables["opportunities"],"event_id")
    require(set(got_grid) == {r["event_id"] for r in expected}, "Whole grid identity mismatch")
    for row in expected:
        check(got_grid[row["event_id"]],row,"Opportunity/"+row["event_id"])
    shapes = indexed(tables["shape_opportunities"],"event_id")
    require(set(shapes) == {r["event_id"] for r in expected if r["shape_status"] == "qualified"}, "Shape subset")
    for event,row in shapes.items():
        require(row == got_grid[event], "Shape subset copied fields drift")
    for arm in entries:
        actual = indexed(tables[arm+"_entries"],"event_id")
        require(set(actual) == set(entries[arm]), "Entry set "+arm)
        for event,want in entries[arm].items():
            check(actual[event],want,arm+" entry/"+event)
    old = indexed(original,"event_id")
    require(set(old) == set(entries["sma"]), "Original SMA mother population")
    for event,want in entries["sma"].items():
        check({k:old[event][k] for k in ENTRY},want,"Original mother/"+event)
    count_rows = []
    months = sorted({r["month"] for r in expected})
    for population in ("all_clock_directions","shape_qualified"):
        base = expected if population == "all_clock_directions" else [r for r in expected if r["shape_status"] == "qualified"]
        for dimension,keys in (("all",["all"]),("fold",[f[0] for f in FOLDS]),("month",months),("direction",[1,-1])):
            for key in keys:
                part = base if dimension == "all" else [r for r in base if r[dimension] == key]
                for arm in entries:
                    states = Counter(r[arm+"_state"] for r in part)
                    count_rows.append(dict(population=population,dimension=dimension,key=str(key),arm=arm,total=len(part),
                                           **{s:states[s] for s in ("accepted","abstain","unknown")}))
    count_key = lambda r:(r["population"],r["dimension"],r["key"],r["arm"])
    counts = {count_key(r):r for r in tables["counts"]}
    require(len(counts) == len(tables["counts"]) == len(count_rows), "Counts identities")
    for row in count_rows:
        require(count_key(row) in counts,"Missing count group")
        check(counts[count_key(row)],row,"Counts/"+str(count_key(row)))
    support = {}
    for arm, data in entries.items():
        own = [r for r in expected if r["event_id"] in data]
        values = dict(events=len(own),minimum_per_fold=min(sum(r["fold"] == f[0] for r in own) for f in FOLDS),
                      active_months=len({r["month"] for r in own}),
                      minimum_months_per_fold=min(len({r["month"] for r in own if r["fold"] == f[0]}) for f in FOLDS))
        gates = {k:values[k] >= threshold for k,threshold in {"events":80,"minimum_per_fold":12,"active_months":12,"minimum_months_per_fold":3}.items()}
        support[arm] = dict(values=values,gates=gates,**{"pass":all(gates.values())})
    differs = set(entries["sma"]) != set(entries["vwma"])
    overlap = Counter(r["overlap"] for r in expected)
    want_summary = dict(population=len(expected),shape_qualified=len(shapes),shape_unknown=sum(r["shape_status"] == "unknown" for r in expected),
                        support=support,overlap={s:overlap[s] for s in ("both_accepted","sma_only","vwma_only","both_abstain","any_unknown")},
                        accepted_identity_sets_differ=differs,
                        status="no_entry_change" if not differs else "support_pass_requires_new_background" if support["vwma"]["pass"] else "insufficient_support_no_outcomes",
                        outcomes_read_or_computed=False,economic_acceptance=False,random_controls_assigned=False)
    check({k:summary[k] for k in want_summary},want_summary,"Summary")
    def split(key):
        keys = [f[0] for f in FOLDS] if key == "fold" else [1,-1]
        return {str(value):dict(Counter(r["overlap"] for r in expected if r[key] == value)) for value in keys}
    unknown = [r for r in expected if r["overlap"] == "any_unknown"]
    return dict(population=len(expected),hourly_rows_per_arm=len(source),count_groups=len(count_rows),
                entries={arm:len(data) for arm,data in entries.items()},shape_qualified=len(shapes),overlap=dict(overlap),
                exclusive_entry_rejection_reasons={k:dict(v) for k,v in reason_counts.items()},
                overlap_by_fold=split("fold"),overlap_by_direction=split("direction"),
                unknown_rows=len(unknown),unknown_reason_combinations=dict(Counter(
                    r["sma_reason"]+" | "+r["vwma_reason"] for r in unknown)),
                unknown_by_fold=dict(Counter(r["fold"] for r in unknown)),
                unknown_by_direction=dict(Counter(str(r["direction"]) for r in unknown)))


def git(root, *args):
    return subprocess.check_output(["git",*args],cwd=root)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_receipt(root, metadata):
    commit = metadata["builder_commit"]
    # Git commit offsets may be local: only convert this git-owned timestamp.
    when = datetime.fromisoformat(git(root,"show","-s","--format=%cI",commit).decode().strip())
    require(when.tzinfo is not None,"Git commit requires timezone")
    when = when.astimezone(UTC)
    require(when <= stamp(metadata["at"]),"Source commit follows materialization")
    seen = set()
    for item in metadata["sources"]:
        path = item["path"]
        require(path not in seen,"Duplicate source receipt")
        seen.add(path)
        require(hashlib.sha256(git(root,"show",commit+":"+path)).hexdigest() == item["sha256"],"Committed source SHA "+path)
    return len(seen)


def verify(root=ROOT):
    root = Path(root)
    audit_path = E/"audit_saved.py"
    audit_commit = git(root,"rev-parse","HEAD").decode().strip()
    auditor_sha = sha(root/audit_path)
    require(hashlib.sha256(git(root,"show",audit_commit+":"+str(audit_path))).hexdigest() == auditor_sha,"Auditor must be committed before real audit")
    directory = root/E/"results"
    require(not (directory/"failure.json").exists(),"Failed run cannot be evidence")
    metadata_hashes = {name:sha(directory/name) for name in (
        "summary.json","support_frozen.json","started.json","baseline_reproduced.json")}
    require(sha(root/E/"config.json") == CONFIG_SHA,"Frozen config")
    require(sha(directory/"support_frozen.json") == FREEZE_SHA,"Pinned first-run support freeze")
    config,summary,freeze,started,baseline = [load_json(p) for p in (
        root/E/"config.json",directory/"summary.json",directory/"support_frozen.json",directory/"started.json",directory/"baseline_reproduced.json")]
    require({summary["builder_commit"],freeze["builder_commit"],started["builder_commit"]} == {BUILDER},"Builder identity")
    require(summary["experiment_id"] == E.name,"Experiment identity")
    require(summary["support_frozen_sha256"] == FREEZE_SHA,"Summary/freeze linkage")
    require(summary["output_hashes"] == freeze["output_hashes"] and set(summary["output_hashes"]) == OUTPUTS,"Output manifest")
    for name,expected in summary["output_hashes"].items():
        require(sha(directory/name) == expected,"Output SHA "+name)
    require(sha(directory/"baseline_reproduced.json") == freeze["baseline_checkpoint_sha256"],"Baseline checkpoint SHA")
    require(started["sources"] == freeze["sources"],"Source receipt changed")
    require(started["config_sha256"] == CONFIG_SHA,"Started config SHA")
    require(started["inputs"] == config["inputs"] == freeze["input_receipt"]["inputs"] == baseline["inputs"],"Input manifests")
    require(len(config["inputs"]) == 10 and len(started["sources"]) == 12,"Receipt cardinality")
    for path,expected in config["inputs"].items():
        require(sha(root/path) == expected,"Input SHA "+path)
    require(stamp(started["at"]) < stamp(baseline["at"]) < stamp(freeze["at"]) <= stamp(summary["generated_at"]),"Baseline/support chronology")
    for key in ("all_entry_columns_parity","all_feature_columns_parity","before_vwma_computation"):
        require(baseline[key] is True,"Baseline receipt "+key)
    require(baseline["events"] == 251 and freeze["timestamp_preflight_before_prices"] is True and freeze["outcomes_read_or_computed"] is False,"Stage markers")
    current_sources = source_receipt(root,started)
    for item in started["sources"]:
        require(sha(root/item["path"]) == item["sha256"],"Current source drift "+item["path"])
    v20 = TRACE.parent
    old,oldfreeze,first,failure,resumed = [load_json(root/v20/n) for n in (
        "started.json","context_frozen.json","outcomes_started.json","failure.json","outcomes_resumed_1.json")]
    chain = [stamp(m["at"]) for m in (old,oldfreeze,first,failure,resumed)]
    require(all(a < b for a,b in zip(chain,chain[1:])),"Parent feature/recovery chronology")
    require(oldfreeze["outcomes_read"] is False and oldfreeze["output_hashes"][TRACE.name] == config["inputs"][str(TRACE)],"Parent feature freeze")
    for marker in (first,resumed):
        require(marker["context_frozen_sha256"] == config["inputs"][str(v20/"context_frozen.json")],"Parent recovery identity")
    require(oldfreeze["source_receipt"]["holdout_price_rows"] == 0,"Parent holdout source receipt")
    require(stamp(oldfreeze["source_receipt"]["phase_price_last_open"]) < stamp("2025-01-01T00:00:00Z"),"Parent phase receipt")
    old4 = load_json(root/MOTHERS.parent/"started.json")
    parents = [source_receipt(root,m) for m in (old,old4)]
    require(freeze["input_receipt"]["parent_sources_verified"] == parents,"Parent source count receipts")
    source = rows(root/TRACE)
    require(len(source) == config["trace_rows"] == 18222,"Trace cardinality")
    check(source[0]["open_time"],stamp(config["trace_first_open"]),"Trace first")
    check(source[-1]["open_time"],stamp(config["trace_last_open"]),"Trace last")
    require(all(stamp(r["open_time"],hour=True) < stamp("2025-01-01T00:00:00Z") for r in source),"Forbidden phase row")
    tables = {name[:-7]:rows(directory/name) for name in OUTPUTS}
    data = verify_tables(source,tables,rows(root/MOTHERS),summary)
    # Recheck the same immutable receipts around all reads/computation; never
    # endorse a concurrent file replacement using only an earlier digest.
    for path,expected in config["inputs"].items():
        require(sha(root/path) == expected,"Input changed during audit: "+path)
    for name,expected in summary["output_hashes"].items():
        require(sha(directory/name) == expected,"Output changed during audit: "+name)
    for name,expected in metadata_hashes.items():
        require(sha(directory/name) == expected,"Metadata changed during audit: "+name)
    for item in started["sources"]:
        require(sha(root/item["path"]) == item["sha256"],"Source changed during audit: "+item["path"])
    require(sha(root/audit_path) == auditor_sha,"Auditor changed during audit")
    require(not (directory/"failure.json").exists(),"Failure appeared during audit")
    return dict(status="passed",at=datetime.now(UTC).isoformat(),**data,
                output_hashes_verified=len(OUTPUTS),input_hashes_verified=len(config["inputs"]),source_receipts_verified=current_sources,
                parent_source_receipts_verified=parents,builder_commit=BUILDER,audit_commit=audit_commit,
                auditor_sha256=auditor_sha,summary_sha256=metadata_hashes["summary.json"],support_frozen_sha256=FREEZE_SHA,
                input_output_source_hashes_rechecked_after_computation=True,
                saved_chronology={"started":started["at"],"baseline_reproduced":baseline["at"],"support_frozen":freeze["at"],"summary":summary["generated_at"]},
                original_entry_fields_recomputed=True,independent_saved_hourly_formula_check=True,
                raw5_aggregation_verified=False,source_authenticity_verified=False,profitability_evaluated=False,
                tradingview_live_parity_verified=False,independent_execution_order_observation=False)


def self_test():
    """Synthetic only, no filesystem/study reads; negative controls retained."""
    t = stamp("2024-01-01T00:00:00Z")
    source = [dict(open_time=(t+i*HOUR).isoformat(),open="100",high="101",low="99",close="100",volume=".1") for i in range(100)]
    a,b = recompute(source,"SMA"),recompute(source,"VWMA")
    require(a == b,"Uniform weights exact identity")
    require(a[38]["ma"] is None and a[39]["ma"] == 100 and a[39]["ma_side"] == 1,"40 clock/tie")
    require(a[13]["atr"] == 2 and a[12]["atr"] is None,"RMA seed")
    changed = [dict(r) for r in source]
    changed[10].update(open="120",close="120",high="121",low="119",volume="1000")
    c,d = recompute(changed,"SMA"),recompute(changed,"VWMA")
    require(d[39]["ma"] > c[39]["ma"] and d[50]["ma"] == c[50]["ma"],"Weight window")
    changed[60]["volume"] = ""
    require(recompute(changed,"VWMA")[60]["ma"] is None and recompute(changed,"SMA")[60]["ma"] is not None,"Volume missingness")
    gapped = source[:50]+source[51:]
    require(recompute(gapped,"SMA")[50]["ma"] is None,"Gap reset")
    require(recompute(source[:75],"VWMA") == b[:75],"Prefix invariance")
    require(len(list(grid())) == 34512 and len({r["event_id"] for r in grid()}) == 34512,"Complete grid")
    q = dict(a[50]);q.update(open=99,close=101,hl2=100.5,ma_side=1,body_ratio=.8,range_atr=1.,long_close_location=.9)
    require(state(q,1)[0] == "accepted","Strict long cross")
    q["open"] = 100
    require(state(q,1)[0] == "abstain","Equality cannot cross")
    q.update(open=101,close=99,hl2=99.5,ma_side=-1,short_close_location=.9)
    require(state(q,-1)[0] == "accepted","Mirrored strict cross")
    negatives = (("nanosecond",lambda:stamp("2024-01-01T00:00:00.000000001Z",True)),
                 ("bool-coercion",lambda:check("1",True,"test")),
                 ("unknown-zero",lambda:check("0",None,"test")),
                 ("duplicate-id",lambda:indexed([{"id":"a"},{"id":"a"}],"id")),
                 ("wrong-side",lambda:check("-1",1,"test")))
    for name,call in negatives:
        try:
            call()
        except ValueError:
            continue
        raise ValueError("Negative self-test failed: "+name)
    return {"status":"passed","synthetic_checks":16,"real_study_files_read":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test-only",action="store_true")
    parser.add_argument("--out",type=Path,default=ROOT/E/"audit.json")
    args = parser.parse_args()
    if args.self_test_only:
        print(json.dumps(self_test(),indent=2))
        return
    require(not args.out.exists(),"Refusing to overwrite audit output")
    self_test()
    answer = verify()
    with args.out.open("x",encoding="utf-8") as handle:
        json.dump(answer,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write("\n")
    print(json.dumps(answer,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
