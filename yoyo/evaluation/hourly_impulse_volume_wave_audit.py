"""Independent stdlib scalar reconstruction of V31 saved-source support.

No strategy feature imports. Read pinned V20 hourly OHLCV and V24 identity-only
requests, then compare saved V31 wave, own clocks, states, tables and gates.
Not raw5 aggregation, market-data authenticity, TV/Pine runtime or economics.
EMA20/5 seed recurrence follows frozen ChartPrime lfaZVLub numeric formula.
Only current/past rows in each contiguous segment contribute to each wave.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EID = "exp-btcusdtp-1h-volume-wave-support-preholdout-20260907-v31"
DIRECTORY = Path("experiments/active") / EID
V20 = Path("experiments/active/exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20/results")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
HOUR = timedelta(hours=1)
STATES = ("accepted", "abstain", "unknown")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    with gzip.open(path, "rt") as f:
        return list(csv.DictReader(f))


def time(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert t.utcoffset() == timedelta(0) and t.minute == t.second == t.microsecond == 0
    return t


def same(a, b):
    if a is None:
        assert b == "" or b is None, (a, b)
    else:
        assert math.isclose(a, float(b), rel_tol=1e-12, abs_tol=1e-12), (a, b)


def scalar(rows):
    """Recurrence deliberately uses Python floats, not pandas ewm."""
    out = {}
    previous = None
    count = 0
    segment = 0
    for row in rows:
        t = time(row["open_time"])
        assert t not in out and (previous is None or t > previous)
        o, h, l, c, v = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
        assert all(math.isfinite(x) for x in (o, h, l, c, v))
        assert min(o, h, l, c) > 0 and v >= 0 and h >= max(o, c, l) and l <= min(o, c, h)
        bv, sv, tv = (v if c > o else 0.), (v if c < o else 0.), (v if v > 0 else 1.)
        if previous is None or t - previous != HOUR:
            segment += 1
            count = 1
            buy, sell, total = bv, sv, tv
            raw = 100. * (buy - sell) / total
            wave = raw
        else:
            count += 1
            alpha = 2. / 21.
            buy, sell, total = [alpha*x+(1.-alpha)*p for x, p in ((bv, buy), (sv, sell), (tv, total))]
            raw = 100. * (buy - sell) / total
            wave = (1./3.)*raw+(2./3.)*wave
        out[t] = dict(count=count, segment=segment, buy=buy, sell=sell, total=total, raw=raw, wave=wave,
                      prices=(o, h, l, c, v))
        previous = t
    return out


def states(requests, trace):
    result = {}
    for row in requests:
        t, e = time(row["signal_time"]), time(row["decision_time"])
        assert e == t+HOUR
        direction = int(row["direction"])
        assert direction in (-1, 1)
        own, older, newer = trace.get(t), trace.get(t-2*HOUR), trace.get(t-HOUR)
        delta = None
        known = (own is not None and older is not None and newer is not None
                 and own["segment"] == older["segment"] == newer["segment"] and older["count"] >= 100)
        if known:
            delta = newer["wave"] - older["wave"]
        if own and row.get("signal_close"):
            same(own["prices"][3], row["signal_close"])
        result[row["event_id"]] = dict(known=bool(known), delta=delta,
            state="accepted" if known and direction*delta > 0 else "abstain" if known else "unknown",
            reverse_accepted=bool(known and -direction*delta > 0))
    assert len(result) == len(requests)
    return result


def totals(rows, lookup):
    return dict(total=len(rows), **{s:sum(lookup[r["event_id"]]["state"] == s for r in rows) for s in STATES},
                known=sum(lookup[r["event_id"]]["known"] for r in rows))


def audit(root=ROOT):
    root = Path(root)
    directory = root/DIRECTORY/"results"
    config = json.loads((root/DIRECTORY/"config.json").read_text())
    frozen = json.loads((directory/"support_frozen.json").read_text())
    summary = json.loads((directory/"summary.json").read_text())
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    own_path = "yoyo/evaluation/hourly_impulse_volume_wave_audit.py"
    assert hashlib.sha256(subprocess.check_output(["git", "show", current+":"+own_path], cwd=root)).hexdigest() == sha(root/own_path)
    expected = {**config["inputs"], **config["feature_sources"], **config["source_pins"],
                **{s["path"]:s["sha256"] for s in frozen["sources"]},
                **{str(DIRECTORY/"results"/p):h for p,h in frozen["output_hashes"].items()}}
    start_hashes = {p:sha(root/p) for p in expected}
    assert start_hashes == expected
    assert frozen["outcomes_read_or_computed"] is False and frozen["holdout_consumed"] is False
    assert frozen["timestamp_preflight_before_prices"] is True
    assert summary["builder_commit"] == frozen["builder_commit"]
    assert summary["output_hashes"] == frozen["output_hashes"]
    assert summary["support_frozen_sha256"] == sha(directory/"support_frozen.json")
    for src in frozen["sources"]:
        assert hashlib.sha256(subprocess.check_output(
            ["git", "show", frozen["builder_commit"]+":"+src["path"]], cwd=root)).hexdigest() == src["sha256"]
    source = read(root/V20/"hourly_trace.csv.gz")
    assert len(source) == config["trace_rows"]
    assert time(source[0]["open_time"]) == time(config["trace_start"])
    assert time(source[-1]["open_time"]) == time(config["trace_end"]) < time(config["phase_end_exclusive"])
    trace = scalar(source)
    saved = read(directory/"hourly_trace.csv.gz")
    assert len(saved) == len(trace)
    for raw, actual in zip(source, saved):
        t = time(raw["open_time"])
        assert time(actual["open_time"]) == t
        for key in ("open", "high", "low", "close", "volume"):
            same(float(raw[key]), actual[key])
        expected_wave = trace[t]
        # Column mapping is locked with the core API before any actual run.
        for key, col in TRACE_NUMERIC.items():
            same(expected_wave[key], actual[col])
        assert int(actual["wave_count"]) == expected_wave["count"]
        assert int(actual["wave_segment"]) == expected_wave["segment"]
        assert time(actual["wave_available_at"]) == t+HOUR
        for lag, stem in ((1, "wave_previous"), (2, "wave_previous2")):
            prior = trace.get(t-lag*HOUR)
            if prior and prior["segment"] == expected_wave["segment"]:
                same(prior["wave"], actual[stem])
                assert int(actual[stem+"_count"]) == prior["count"]
                assert time(actual[stem+"_open_time"]) == t-lag*HOUR
                assert time(actual[stem+"_available_at"]) == t-(lag-1)*HOUR
            else:
                assert all(actual[c] == "" for c in (stem, stem+"_count", stem+"_open_time", stem+"_available_at"))
        assert (actual["wave_known"] == "True") == (expected_wave["count"] >= 102)
    populations, lookups = {}, {}
    for pop in ("case", "control"):
        requests = read(root/V24/(pop+"_requests.csv.gz"))
        lookup = states(requests, trace)
        contexts = read(directory/(pop+"_context.csv.gz"))
        assert len(contexts) == len(requests)
        for r, got in zip(requests, contexts):
            assert all(got[k] == v for k,v in r.items())
            exp = lookup[r["event_id"]]
            assert got["wave_gate_state"] == exp["state"]
            assert (got["wave_known"] == "True") == exp["known"]
            if exp["known"]:
                same(exp["delta"], got["wave_prior_delta"])
                assert time(got["wave_previous_available_at"]) == time(r["signal_time"])
                assert time(got["wave_previous2_available_at"]) == time(r["signal_time"])-HOUR
                signed = int(r["direction"])*exp["delta"]
                assert got["wave_reason"] == ("prior_improving" if signed>0 else "flat" if signed==0 else "opposite")
        assert summary["population"][pop] == totals(requests, lookup)
        populations[pop], lookups[pop] = requests, lookup
    counts = read(directory/"counts.csv.gz")
    expected_grid = {(p,d,k) for p in ("case","control") for d,ks in
        (("all",["all"]),("fold",[x[0] for x in config["folds"]]),("direction",["1","-1"]),
         ("month",[f"{y}-{m:02d}" for y in (2023,2024) for m in range(1,13)])) for k in ks}
    assert {(r["population"],r["dimension"],r["key"]) for r in counts} == expected_grid and len(counts)==62
    for r in counts:
        pop, d, k = r["population"], r["dimension"], r["key"]
        def included(x):
            return d == "all" or (x["fold"] == k if d=="fold" else str(int(x["direction"]))==k
                                 if d=="direction" else time(x["decision_time"]).strftime("%Y-%m")==k)
        rows = [x for x in populations[pop] if included(x)]
        n = totals(rows, lookups[pop])
        assert all(int(r[key])==value for key,value in n.items())
        same(n["accepted"]/n["total"] if n["total"] else None, r["accepted_rate"])
    cases, controls = populations["case"], populations["control"]
    selected = [r for r in cases if lookups["case"][r["event_id"]]["state"]=="accepted"]
    values = dict(events=len(selected), minimum_fold_events=min(sum(r["fold"]==f[0] for r in selected) for f in config["folds"]),
        active_months=len({time(r["decision_time"]).strftime("%Y-%m") for r in selected}),
        minimum_fold_months=min(len({time(r["decision_time"]).strftime("%Y-%m") for r in selected if r["fold"]==f[0]}) for f in config["folds"]))
    assert values == summary["support_values"]
    gates = dict(zip(config["support"], [values["events"]>=80,values["minimum_fold_events"]>=12,
                     values["active_months"]>=12,values["minimum_fold_months"]>=3]))
    assert summary["support_gates"] == gates and summary["support_pass"] == all(gates.values())
    matched = read(directory/"matched_support.csv.gz")
    assert len(matched)==len(cases) and len({r["event_id"] for r in matched})==len(cases)
    matched_lookup = {r["event_id"]:r for r in matched}
    complete_n = accepted_complete = 0
    for m in cases:
        group = sorted([c for c in controls if c["mother_id"]==m["event_id"]], key=lambda c:int(c["control_slot"]))
        got = matched_lookup[m["event_id"]]
        expected_state = lookups["case"][m["event_id"]]
        n = totals(group,lookups["control"])
        complete = expected_state["known"] and len(group)==3 and n["unknown"]==0
        assert got["case_state"]==expected_state["state"]
        assert got["control_ids"]=="|".join(x["event_id"] for x in group)
        assert int(got["control_total"])==len(group)
        assert (got["complete_known"]=="True")==complete
        assert (got["accepted_case_complete_known"]=="True") == (complete and expected_state["state"]=="accepted")
        assert all(int(got["control_"+s])==n[s] for s in STATES)
        complete_n += complete
        accepted_complete += complete and expected_state["state"]=="accepted"
    coverage_expected = dict(case_known=(summary["population"]["case"]["known"],len(cases)),
        control_known=(summary["population"]["control"]["known"],len(controls)),
        complete_known_triples_all_cases=(complete_n,len(cases)),
        complete_known_triples_accepted_cases=(accepted_complete,len(selected)))
    for key,(n,d) in coverage_expected.items():
        assert summary["coverage"][key] == dict(numerator=n,denominator=d,rate=n/d if d else None)
    assert summary["accepted_case_control_states"]==totals(
        [r for r in controls if r["mother_id"] in {s["event_id"] for s in selected}],lookups["control"])
    null_control={}
    for pop, lookup in lookups.items():
        positive=sum(r["state"]=="accepted" for r in lookup.values())
        negative=sum(r["reverse_accepted"] for r in lookup.values())
        flat=sum(r["known"] and r["delta"]==0 for r in lookup.values())
        known=sum(r["known"] for r in lookup.values())
        assert positive+negative+flat==known
        null_control[pop]=dict(accepted=positive,direction_reversed_accepted=negative,flat=flat,known=known)
    assert expected=={p:sha(root/p) for p in expected}
    receipt=dict(status="passed",auditor_commit=current,at=datetime.now(timezone.utc).isoformat(),
        hourly_rows=len(source),contexts=sum(len(x) for x in populations.values()),count_rows=len(counts),matched_rows=len(matched),
        input_and_output_hashes=expected,scalar_replay=True,direction_symmetry_control=null_control,
        economic_outcomes_read=False,raw5_read=False,holdout_consumed=False,pine_runtime_parity=False)
    path = root/DIRECTORY/"audit.json"
    with path.open("x") as f:
        json.dump(receipt,f,indent=2,allow_nan=False)
        f.write("\n")
    return receipt


TRACE_NUMERIC = dict(buy="wave_ema_buy", sell="wave_ema_sell", total="wave_ema_total",
                     raw="wave_raw", wave="wave_value")


if __name__ == "__main__":
    print(json.dumps(audit(),indent=2))
