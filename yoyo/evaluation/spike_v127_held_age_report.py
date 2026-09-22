"""Receipt-bound report for the V12.7 held-break age cap replay.

Reads only a completed ``spike_v127_held_age`` runner directory plus the
frozen V12.6 ``run_v2`` reference; never opens price archives or reruns
decisions. Aggregate metrics, month-block bootstrap, the four-test later
family and tail retention reuse ``spike_v126_htf_report`` with this
experiment's two arms. Added here: (1) baseline parity against V12.6 run_v2
trades/controls, (2) descriptive latency from the causal line-crossing close
to the joint close and entry chase in R/ATR-free units, (3) the V12.7 default
rule fixed in the experiment plan before the run.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v126_htf_report as base


ARMS = ("baseline", "held_age_8")
PARITY_FIELDS = ("entry_time", "exit_time", "exit_reason", "net_r", "net_return", "initial_stop", "censored")


def _with_arms() -> None:
    # The shared aggregation helpers read their module-level ARMS at call
    # time; bind this experiment's arms before any of them run.
    base.ARMS = ARMS


def parity(frames: dict[str, pd.DataFrame], reference: Path) -> dict[str, Any]:
    """Baseline trades and controls must equal the V12.6 run_v2 baseline."""
    _, ref = base.load_run(reference)
    ours = frames["trades"].loc[frames["trades"].arm.eq("baseline")].set_index("trade_key").sort_index()
    theirs = ref["trades"].loc[ref["trades"].arm.eq("baseline")].set_index("trade_key").sort_index()
    same_keys = ours.index.equals(theirs.index)
    mismatched = {}
    if same_keys:
        for field in PARITY_FIELDS:
            a, b = ours[field], theirs[field]
            if field in ("net_r", "net_return", "initial_stop"):
                bad = ~np.isclose(a.astype(float), b.astype(float), rtol=0, atol=1e-12, equal_nan=True)
            else:
                bad = a.astype(str).to_numpy() != b.astype(str).to_numpy()
            mismatched[field] = int(bad.sum())
    oc = frames["controls"].loc[frames["controls"].arm.eq("baseline")].set_index("trade_key").sort_index()
    rc = ref["controls"].loc[ref["controls"].arm.eq("baseline")].set_index("trade_key").sort_index()
    controls_same = oc.index.equals(rc.index) and bool(np.allclose(oc.control_net_r.astype(float), rc.control_net_r.astype(float), equal_nan=True))
    return {"reference": str(reference), "baseline_trades": len(ours), "reference_trades": len(theirs), "same_trade_keys": bool(same_keys),
            "field_mismatches": mismatched, "controls_same": controls_same,
            "pass": bool(same_keys and not any(mismatched.values()) and controls_same)}


def latency(frames: dict[str, pd.DataFrame], split: pd.Timestamp) -> pd.DataFrame:
    """Median/quantile bars from the first above-line close to the joint close.

    ``chase_r`` = (entry - crossing close) / (entry - initial stop); positive
    means the entry paid above the close that first crossed the line.
    """
    d = frames["decisions"][["trade_key", "arm", "order", "pair_source", "signal_close", "cross_close_time", "cross_close"]]
    t = frames["trades"]
    t = t.loc[~t.censored.astype(bool)].merge(d, on=["trade_key", "arm"], how="left", validate="one_to_one", suffixes=("", "_d"))
    t["lag_bars"] = (pd.to_datetime(t.signal_close, utc=True) - pd.to_datetime(t.cross_close_time, utc=True)) / pd.Timedelta(minutes=15)
    t["chase_r"] = (t.entry_price - t.cross_close) / (t.entry_price - t.initial_stop)
    t["cohort"] = np.where(pd.to_datetime(t.signal_close, utc=True) >= split, "later", "earlier_or_cross")
    rows = []
    for arm in ARMS:
        unit = t.loc[t.arm.eq(arm)]
        for group, sub in [("all", unit), *[(f"order={k}", g) for k, g in unit.groupby("order")]]:
            rows.append({"arm": arm, "group": group, "n_closed": len(sub),
                         "lag_bars_p25": sub.lag_bars.quantile(.25), "lag_bars_median": sub.lag_bars.median(),
                         "lag_bars_p75": sub.lag_bars.quantile(.75), "lag_bars_p90": sub.lag_bars.quantile(.90),
                         "chase_r_median": sub.chase_r.median(), "chase_r_gt1_share": float((sub.chase_r > 1).mean()) if len(sub) else math.nan,
                         "missing_cross": int(sub.cross_close.isna().sum())})
    return pd.DataFrame(rows)


def default_rule(metrics: pd.DataFrame) -> dict[str, Any]:
    """Plan rule: mean net R not lower in full and later; full gt5 rate not lower."""
    m = metrics.set_index(["arm", "period"])
    def get(arm, period, col): return float(m.loc[(arm, period), col])
    checks = {}
    for period in ("full", "later"):
        checks[f"mean_net_r_{period}"] = get(ARMS[1], period, "mean_net_r") >= get(ARMS[0], period, "mean_net_r")
    rate = {arm: get(arm, "full", "gt5_final_net_r") / get(arm, "full", "n") for arm in ARMS}
    checks["gt5_rate_full"] = rate[ARMS[1]] >= rate[ARMS[0]]
    return {"checks": checks, "gt5_rate_full": rate, "default_on": all(checks.values()),
            "rule": "treatment mean_net_r >= baseline in full AND later, AND full gt5 rate >= baseline"}


def build(input_dir: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    _with_arms()
    identity, frames = base.load_run(input_dir)
    config = identity["config"]
    if identity.get("subset") is not False or len(identity["symbols"]) != config["expected_symbols"]:
        raise ValueError("full frozen universe required")
    if config["arms"] != list(ARMS) or config["split"] != base.SPLIT.isoformat():
        raise ValueError("frozen specification mismatch")
    split = pd.Timestamp(config["split"])
    closed = base._closed(frames["trades"], split)
    metrics, monthly = base.summary_tables(closed, frames["controls"], config)
    inference = base._bootstrap(closed, config) + base._weekly_inference(closed, frames["controls"], config)
    output.mkdir(parents=True)
    tables = {"metrics.csv": metrics, "monthly.csv": monthly, "tail_retention.csv": base.tail_retention(closed),
              "paired_attribution.csv": base.paired_attribution(closed), "inference.csv": pd.DataFrame(inference),
              "latency.csv": latency(frames, split)}
    for name, frame in tables.items():
        frame.to_csv(output / name, index=False)
    check = parity(frames, Path(config["parity_reference_run"]))
    rule = default_rule(metrics)
    summary = {"input": str(input_dir), "input_manifest_sha256": base.sha(input_dir / "manifest.json"),
               "input_identity_sha256": base.sha(input_dir / "identity.json"), "report_source_sha256": base.sha(Path(__file__)),
               "closed_rows": len(closed), "baseline_parity": check, "v127_default_rule": rule,
               "files": {name: base.sha(output / name) for name in tables}, "native_pine_parity": False,
               "auc": "not_applicable_no_model_score", "top_decile": "not_applicable_no_ranker",
               "drawdown_basis": "exit_ordered_event_r_nonaccount", "training_eligible": False, "production_eligible": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=bool) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.output), indent=2, default=bool))


if __name__ == "__main__":
    main()
