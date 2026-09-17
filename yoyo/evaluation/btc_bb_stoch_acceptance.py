"""One-shot BTC 5m acceptance on the holdout. Reading this window spends it.

Two arms only, both frozen before the read: the original BB200/2.0/3% rule and
the owner-approved break-even-with-cost variant. The grid candidates are absent
on purpose -- they were rejected at recheck, and spending a holdout on a
rejected candidate buys nothing.

The pass rule is evaluated by code, not by prose: net R positive, matched
control excess positive, and month-block one-sided p below 0.01, all three.
The module refuses to run twice into the same directory, so there is no quiet
second attempt.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.authorized_holdout_window import read_authorized_window
from yoyo.evaluation.bb_stoch_longrun_study import fee_curve, run_arm
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec, compute_features, prepare, replay_entry
from yoyo.evaluation.bb_stoch_rsi_study import audit
from yoyo.evaluation.eth_bb_stoch_study import (ROOT, compare, draw_controls, enrich,
                                                match_context, save, sha, stats)

EXP = ROOT / "experiments/active/exp-btc-bb-stoch-holdout-acceptance-20260917-v1"
BUILDERS = [
    "yoyo/evaluation/btc_bb_stoch_acceptance.py",
    "yoyo/evaluation/btc_bb_stoch_acceptance_report.py",
    "yoyo/data/authorized_holdout_window.py",
    "yoyo/evaluation/bb_stoch_longrun_study.py",
    "yoyo/evaluation/bb_stoch_parameter_replay.py",
    "yoyo/evaluation/bb_stoch_rsi_study.py",
    "yoyo/evaluation/eth_bb_stoch_study.py",
    "yoyo/evaluation/spike_fanshen_exit.py",
    "yoyo/data/release_eth_prefix.py",
    "yoyo/contracts/holdout.py",
    "docs/HOLDOUT_LEDGER.md",
    "tests/evaluation/test_btc_bb_stoch_acceptance.py",
]


def freeze_receipt() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    names = BUILDERS + [str((EXP / n).relative_to(ROOT)) for n in ("config.json", "PROJECT_PLAN.md")]
    files = {}
    for name in names:
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        if committed != (ROOT / name).read_bytes():
            raise ValueError(f"Uncommitted builder/config: {name}")
        files[name] = hashlib.sha256(committed).hexdigest()
    return dict(source_commit=head, files=files, code_frozen_before_holdout_read=True,
                generated_at=pd.Timestamp.now(tz="UTC"))


def verdict(group: dict, cfg: dict) -> dict:
    """Apply the three frozen criteria; every one of them must hold."""
    s, c = group["stats"], group["control"]
    rules = cfg["pass_criteria"]
    checks = {
        "net_r_positive": bool(s["net_r"] > 0),
        "excess_mean_net_r_positive": bool((c["excess_mean_net_r"] or 0) > 0),
        "signflip_p_below_threshold": bool(c["p"] is not None and c["p"] < rules["signflip_p_below"]),
    }
    return dict(checks=checks, passed=all(checks.values()),
                net_r=s["net_r"], excess_mean_net_r=c["excess_mean_net_r"], p=c["p"],
                threshold=rules["signflip_p_below"])


def main() -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    if (EXP / "results.json").exists():
        raise ValueError("this acceptance has already been evaluated; a second run is forbidden")
    receipt = freeze_receipt()
    save(EXP / "code_receipt.json", receipt)
    auth = cfg["holdout_authorization"]
    raw, source = read_authorized_window(ROOT / cfg["source"], cfg["minutes"], auth)
    if sha(ROOT / cfg["source"]) != cfg["source_sha256"]:
        raise ValueError("source identity changed")
    save(EXP / "source_receipt.json", source)
    raw["_data_gap"] = raw.index.to_series().diff().ne(pd.Timedelta(minutes=cfg["minutes"]))
    raw.iloc[0, raw.columns.get_loc("_data_gap")] = False
    frame = compute_features(raw)
    context = match_context(frame, cfg["minutes"])
    # Warmup bars exist only so the bands can form; nothing before the holdout
    # boundary may open a scored trade.
    evaluation_start = pd.Timestamp(auth["evaluation_start"])
    context["valid"] &= (context["confirmed"] >= evaluation_start)
    signals = frame.signal.to_numpy(int)
    admissible = (signals != 0) & context["valid"]
    counts = dict(bars=int(len(frame)), holdout_rows_read=source["holdout_rows_read"],
                  raw_signals=int((signals != 0).sum()), scored_signals=int(admissible.sum()),
                  scored_long=int((admissible & (signals == 1)).sum()),
                  scored_short=int((admissible & (signals == -1)).sum()))
    results = dict(config=cfg, data=source, counts=counts, source_commit=receipt["source_commit"],
                   arms={}, fee_curves={}, verdicts={})
    accounting = []
    for arm, plan in cfg["arms"].items():
        spec = ParamSpec(bb_length=cfg["bb_length"], bb_mult=cfg["bb_population_std_multiple"],
                         stop_fraction=cfg["stop_fraction"], partial_fraction=plan["partial"],
                         be_cost_fraction=plan["be_cost"])
        prepared = prepare(frame, spec)
        results["arms"][arm] = {}
        for mode in [cfg["primary_path"], *cfg["sensitivity_paths"]]:
            rows = run_arm(frame, prepared, context, signals, mode, cfg["minutes"])
            audit(rows)
            draws = {int(x["signal_i"]): draw_controls(context, int(x["signal_i"]), int(x["side"]), cfg)
                     for x in rows}
            save(EXP / f"{arm}_{mode}_control_draws.json", draws)
            control_rows = []
            for x in rows:
                for number, j in enumerate(draws[int(x["signal_i"])], 1):
                    control = replay_entry(prepared, int(j), side_override=int(x["side"]), path_mode=mode)
                    if control is None:
                        continue
                    control = enrich(control, frame, cfg["minutes"])
                    control.update(actual_signal_i=int(x["signal_i"]), draw_number=number,
                                   actual_month=x["month"], vol_bucket=int(context["bucket"][int(j)]))
                    control_rows.append(control)
            audit(control_rows)
            accounting.append(dict(arm=arm, path=mode, actual=len(rows), controls=len(control_rows), passed=True))
            save(EXP / f"{arm}_{mode}_trades.json", rows)
            pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows]).to_csv(
                EXP / f"{arm}_{mode}_trades.csv", index=False)
            groups = {"all": rows, "long": [r for r in rows if r["side"] == 1],
                      "short": [r for r in rows if r["side"] == -1]}
            groups.update({m: [r for r in rows if r["month"] == m]
                           for m in sorted({r["month"] for r in rows})})
            results["arms"][arm][mode] = {name: dict(stats=stats(g), control=compare(g, control_rows, cfg))
                                          for name, g in groups.items()}
            if mode == cfg["primary_path"]:
                results["fee_curves"][arm] = fee_curve([r for r in rows if not r["censored"]],
                                                       cfg["fee_sensitivity_rates"])
                results["verdicts"][arm] = verdict(results["arms"][arm][mode]["all"], cfg)
            s = results["arms"][arm][mode]["all"]["stats"]
            print(f"{arm:14s} {mode:14s} n={s['natural']:4d} netR={s['net_r']:+8.3f} "
                  f"perTrade={None if s['mean_net_r'] is None else round(s['mean_net_r'], 4)} "
                  f"grossR={s['gross_r']:+8.3f} PF={None if s['profit_factor'] is None else round(s['profit_factor'], 3)}",
                  flush=True)
    results["accepted"] = any(v["passed"] for v in results["verdicts"].values())
    save(EXP / "accounting_validation.json", accounting)
    save(EXP / "results.json", results)
    print("VERDICT", json.dumps(results["verdicts"], ensure_ascii=False), flush=True)
    print("ACCEPTED" if results["accepted"] else "NOT ACCEPTED", flush=True)


if __name__ == "__main__":
    main()
