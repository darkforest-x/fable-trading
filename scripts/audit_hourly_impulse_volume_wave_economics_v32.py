"""Independent saved-number V32 audit: stdlib joins/aggregation, no yoyo import.

NumPy is used ONLY to reproduce frozen PCG64 random indices/signs. Arithmetic,
quantiles, sample SD, group assembly and inference are independently evaluated.
No raw prices, alternate controls or horizons are acquired. This verifies saved
label arithmetic, not historical-price authenticity or executable trading PnL.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import csv
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
E = Path("experiments/active/exp-btcusdtp-1h-volume-wave-economics-preholdout-20260907-v32")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
V31 = Path("experiments/active/exp-btcusdtp-1h-volume-wave-support-preholdout-20260907-v31/results")
MONTHS = [f"{year}-{month:02}" for year in (2023, 2024) for month in range(1, 13)]
SERIES = ("accepted_cost", "accepted_excess", "policy_excess", "policy_delta")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path):
    with gzip.open(path, "rt", newline="") as stream:
        return list(csv.DictReader(stream))


def number(value):
    return float(value) if value not in (None, "") else math.nan


def equal(a, b, label="numeric audit"):
    a, b = number(a), number(b)
    if math.isnan(a) and math.isnan(b):
        return
    if not math.isfinite(a) or not math.isfinite(b) or not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12):
        raise AssertionError(f"{label}: {a} != {b}")


def payoff(state, value):
    if state == "abstain":
        return 0.
    return value if state == "accepted" else math.nan


def average(values):
    return math.fsum(values) / len(values) if values else math.nan


def quantile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    pos = (len(ordered)-1)*q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(pos-lo)


def check_description(values, saved):
    finite = [v for v in values if math.isfinite(v)]
    assert saved["n_total"] == len(values) and saved["n_known"] == len(finite)
    assert saved["n_unknown"] == len(values)-len(finite)
    equal(average(finite), saved["mean"])
    equal(math.fsum(finite) if finite else None, saved["sum"])
    equal(quantile(finite, .5), saved["median"])
    equal(min(finite) if finite else None, saved["minimum"])
    equal(max(finite) if finite else None, saved["maximum"])
    sd = math.sqrt(math.fsum((v-average(finite))**2 for v in finite)/(len(finite)-1)) if len(finite) > 1 else None
    equal(sd, saved["sd"])
    for field, q in (("q05", .05), ("q25", .25), ("q75", .75), ("q95", .95)):
        equal(quantile(finite, q), saved[field])
    for field, fn in (("n_positive", lambda v: v > 0), ("n_negative", lambda v: v < 0), ("n_zero", lambda v: v == 0)):
        assert saved[field] == sum(fn(v) for v in finite)


def inference(values, months, indices, signs):
    sums = [math.fsum(v for v, m in zip(values, months) if m == month and math.isfinite(v)) for month in MONTHS]
    counts = [sum(m == month and math.isfinite(v) for v, m in zip(values, months)) for month in MONTHS]
    draws = []
    for vector in indices:
        denominator = sum(counts[i] for i in vector)
        assert denominator > 0
        draws.append(sum(sums[i] for i in vector)/denominator)
    observed = sum(sums)
    extreme = sum(sum(s*v for s, v in zip(vector, sums)) >= observed for vector in signs)
    return [quantile(draws, .025), quantile(draws, .975)], (extreme+1)/(len(signs)+1), sums, counts


def run(root=ROOT, *, write_receipt=True):
    root = Path(root)
    config = json.loads((root/E/"config.json").read_text())
    result = json.loads((root/E/"results/summary.json").read_text())
    started = json.loads((root/E/"results/started.json").read_text())
    hashes = {**config["inputs"], **config["source_pins"],
              **{str(E/"results"/name): value for name, value in result["output_hashes"].items()}}
    hashes[str(E/"config.json")] = result["config_sha256"]
    hashes[str(E/"results/summary.json")] = sha(root/E/"results/summary.json")
    for path, value in hashes.items():
        assert sha(root/path) == value, path
    assert result["sources"] == started["sources"]
    for source in started["sources"]:
        blob = subprocess.check_output(["git", "show", started["builder_commit"]+":"+source["path"]], cwd=root)
        assert hashlib.sha256(blob).hexdigest() == source["sha256"]
    own_path = str(Path(__file__).resolve().relative_to(root))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert hashlib.sha256(subprocess.check_output(["git", "show", commit+":"+own_path], cwd=root)).hexdigest() == sha(root/own_path)
    contexts = {kind: {r["event_id"]: r for r in rows(root/V31/(kind+"_context.csv.gz"))} for kind in ("case", "control")}
    for kind, expected in (("case", (100, 148, 3)), ("control", (401, 343, 0))):
        observed = [sum(c["wave_gate_state"] == state for c in contexts[kind].values())
                    for state in ("accepted", "abstain", "unknown")]
        assert observed == list(expected)
        for c in contexts[kind].values():
            t, e = [datetime.fromisoformat(c[f]) for f in ("signal_time", "decision_time")]
            assert e == t+timedelta(hours=1)
            assert datetime.fromisoformat(c["wave_available_at"]) == e
            for lag in (1, 2):
                prefix = "wave_previous" if lag == 1 else "wave_previous2"
                assert datetime.fromisoformat(c[prefix+"_open_time"]) == t-timedelta(hours=lag)
                assert datetime.fromisoformat(c[prefix+"_available_at"]) == t-timedelta(hours=lag-1)
            delta = number(c["wave_previous"])-number(c["wave_previous2"])
            equal(delta, c["wave_prior_delta"])
            known = int(float(c["wave_previous2_count"])) >= 100
            state = ("accepted" if int(c["direction"])*delta > 0 else "abstain") if known else "unknown"
            assert state == c["wave_gate_state"]
            assert c["wave_known"].lower() == str(known).lower()
            reason = ("prior_improving" if int(c["direction"])*delta > 0 else
                      "flat" if delta == 0 else "opposite") if known else "warmup"
            assert reason == c["wave_reason"]
    populations, checks = {}, 0
    for kind, count in (("case", 251), ("control", 744)):
        raw = rows(root/V24/(kind+"_labels.csv.gz"))
        assert len(raw) == 4*count and len(contexts[kind]) == count
        out = {}
        for r in raw:
            key = r["event_id"], int(r["horizon_hours"])
            assert key not in out
            c = contexts[kind][key[0]]
            for field in ("fold", "direction", "mother_id", "mother_month", "request_kind"):
                assert r[field] == c[field]
            decision, endpoint = [datetime.fromisoformat(r[f]) for f in ("decision_time", "endpoint_time")]
            assert decision == datetime.fromisoformat(c["decision_time"])
            assert endpoint == decision+timedelta(hours=key[1])
            assert 2023 <= decision.year <= 2024 and 2023 <= endpoint.year <= 2024
            gross, net = number(r["gross_markout"]), number(r["cost_threshold_markout"])
            if r["status"] == "known":
                entry, exit_ = number(r["entry_open"]), number(r["endpoint_open"])
                equal(gross, int(r["direction"])*(exit_-entry)/entry)
                equal(net, gross-.002)
            else:
                assert math.isnan(gross) and math.isnan(net)
            state = c["wave_gate_state"]
            out[key] = dict(r, state=state, gross=gross, net=net, policy_net=payoff(state, net))
            checks += 1
        assert set(out) == {(event, h) for event in contexts[kind] for h in (1, 4, 12, 24)}
        populations[kind] = out
        emitted = rows(root/E/"results"/(kind+"_ledger.csv.gz"))
        assert len(emitted) == len(out)
        assert {(r["event_id"], int(r["horizon_hours"])) for r in emitted} == set(out)
        for r in emitted:
            own = out[(r["event_id"], int(r["horizon_hours"]))]
            for field, value in (("gross_markout", own["gross"]), ("cost_threshold_markout", own["net"]), ("policy_cost_threshold_markout", own["policy_net"])):
                equal(r[field], value)
    grouped = defaultdict(list)
    for (event, h), r in populations["control"].items():
        grouped[r["mother_id"], h].append(r)
    original_pairs = {(r["event_id"], int(r["horizon_hours"])): r for r in rows(root/V24/"paired_labels.csv.gz")}
    mother = {}
    for key, r in populations["case"].items():
        control = grouped[key]
        assert len(control) in (0, 3)
        if control:
            assert sorted(int(float(c["control_slot"])) for c in control) == [0, 1, 2]
        baseline = average([c["net"] for c in control])
        ctrl_policy = average([c["policy_net"] for c in control])
        excess = r["net"]-baseline
        equal(excess, original_pairs[key]["cost_threshold_excess_markout"])
        mother[key] = dict(r, original_excess=excess, case_policy=r["policy_net"],
            control_policy=ctrl_policy, policy_excess=r["policy_net"]-ctrl_policy,
            policy_delta=r["policy_net"]-r["net"], accepted_cost=r["net"] if r["state"] == "accepted" else math.nan,
            accepted_excess=excess if r["state"] == "accepted" else math.nan)
    emitted_mothers = rows(root/E/"results/mother_ledger.csv.gz")
    assert len(emitted_mothers) == len(mother) and {(r["event_id"], int(r["horizon_hours"])) for r in emitted_mothers} == set(mother)
    for r in emitted_mothers:
        own = mother[(r["event_id"], int(r["horizon_hours"]))]
        for field in SERIES:
            equal(r[field], own[field], field)
    populations["mother"] = mother
    for hour, entry in result["horizons"].items():
        for population, groups in entry["groups"].items():
            allrows = [r for (event, h), r in populations[population].items() if h == int(hour)]
            for state, metrics in groups.items():
                part = [r for r in allrows if state == "all" or r["state"] == state]
                for metric, saved in metrics.items():
                    check_description([r[metric] for r in part], saved)
    rng = np.random.Generator(np.random.PCG64(20260907))
    index_array = rng.integers(0, 24, size=(9999, 24), dtype=np.int64)
    sign_array = 2*rng.integers(0, 2, size=(9999, 24), dtype=np.int64)-1
    assert hashlib.sha256(index_array.astype("<i8").tobytes()).hexdigest() == result["contract"]["month_indices_sha256"]
    assert hashlib.sha256(sign_array.astype("<i8").tobytes()).hexdigest() == result["contract"]["month_signs_sha256"]
    indices, signs = index_array.tolist(), sign_array.tolist()
    pvalues = {}
    for name in SERIES:
        part = [r for (event, h), r in mother.items() if h == 4 and (not name.startswith("accepted_") or r["state"] == "accepted")]
        values, months = [r[name] for r in part], [r["mother_month"] for r in part]
        saved = result["primary"][name]
        check_description(values, saved)
        ci, p, sums, counts = inference(values, months, indices, signs)
        for a, b in zip(ci, saved["monthly_cluster"]["ci95"]):
            equal(a, b, name+" CI")
        equal(p, saved["monthly_cluster"]["one_sided_p"], name+" p")
        for i, month in enumerate(saved["monthly_cluster"]["months"]):
            assert month["month"] == MONTHS[i] and month["n_known"] == counts[i]
            equal(month["sum"], sums[i])
        pvalues[name] = p
    previous = 0.
    for rank, name in enumerate(sorted(pvalues, key=lambda name: (pvalues[name], name))):
        previous = max(previous, min(1., (4-rank)*pvalues[name]))
        equal(previous, result["primary"][name]["holm_p"])
    for path, value in hashes.items():
        assert sha(root/path) == value, path
    audit = dict(status="passed", auditor_commit=commit, auditor_sha256=sha(root/own_path),
        summary_sha256=hashes[str(E/"results/summary.json")], label_rows_recomputed=checks,
        mother_horizon_rows_recomputed=len(mother), horizon_group_descriptions_verified=True,
        sd_extremes_and_output_unique_grids_verified=True,
        wave_gate_clocks_and_states_independently_verified=True,
        primary_inferences_recomputed=4, month_clusters=24, draws=9999, holm_verified=True,
        raw_prices_read=False, holdout_consumed=False, independent_code_path=True,
        separate_human_or_agent_reviewer=False, executable_pnl_verified=False,
        limitation="Saved endpoints/identities/decisions and independent arithmetic only; no raw-window authenticity or execution audit.")
    if write_receipt:
        target = root/E/"audit.json"
        if target.exists():
            raise FileExistsError("Refusing existing audit receipt")
        target.write_text(json.dumps(audit, indent=2, allow_nan=False)+"\n")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Recompute without overwriting any saved receipt")
    args = parser.parse_args()
    print(json.dumps(run(write_receipt=not args.check_only), indent=2))
