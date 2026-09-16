"""The frozen ETH BB × Stoch rule, unchanged, on BTC 5m/1m and XAU 1m.

The only variable is which market and which bar duration.  Nothing about the
rule is retuned for the new venue: BB(200), Stoch(5,3,3), the 3% stop and the
0.1% per-side fee are the ETH values, which on a one-minute chart means a very
wide stop over a 200-minute band.  That mismatch is the measurement, not a
reason to quietly adjust a parameter.

Each dataset runs as its own process (``--dataset``) so the three can be
replayed at the same time.  Price access goes through the frozen pre-holdout
reader, whose ``end > 2026-05-01`` refusal is not relaxed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.bb_stoch_longrun_study import fee_curve, run_arm
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec, compute_features, prepare, replay_entry
from yoyo.evaluation.bb_stoch_rsi_study import audit, rsi_series
from yoyo.evaluation.eth_bb_stoch_study import (ROOT, compare, draw_controls, enrich,
                                                match_context, save, sha, stats)
from yoyo.evaluation.ma_stoch_rsi_filter import zone_admission

EXP = ROOT / "experiments/active/exp-btc-xau-bb-stoch-timeframes-20260916-v1"
BUILDERS = [
    "yoyo/evaluation/btc_xau_bb_stoch_study.py",
    "yoyo/evaluation/btc_xau_bb_stoch_report.py",
    "yoyo/evaluation/bb_stoch_longrun_study.py",
    "yoyo/evaluation/bb_stoch_parameter_replay.py",
    "yoyo/evaluation/bb_stoch_rsi_study.py",
    "yoyo/evaluation/eth_bb_stoch_study.py",
    "yoyo/evaluation/ma_stoch_rsi_filter.py",
    "yoyo/evaluation/spike_v6_bb_squeeze.py",
    "yoyo/evaluation/spike_fanshen_exit.py",
    "yoyo/data/spike_fanshen_prefix.py",
    "yoyo/data/okx_archive_bars.py",
    "yoyo/contracts/holdout.py",
    "tests/evaluation/test_btc_xau_bb_stoch_study.py",
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
    return dict(source_commit=head, files=files, code_frozen_before_price_read=True,
                generated_at=pd.Timestamp.now(tz="UTC"))


def declared_gaps(index: pd.DatetimeIndex, minutes: int) -> pd.Series:
    """Mark discontinuities for THIS bar duration, not a hardcoded five minutes.

    The replay engine defaults to a five-minute step when no gap column is
    supplied, which would call every bar of a one-minute series a gap and stop
    any band from ever warming up.
    """
    result = index.to_series().diff().ne(pd.Timedelta(minutes=minutes))
    result.iloc[0] = False
    return result.astype(bool)


def run(dataset: str) -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    if cfg["parameter_search"] or cfg["holdout_consumed"]:
        raise ValueError("this study runs frozen arms on pre-holdout data only")
    spec_cfg = cfg["datasets"][dataset]
    minutes = spec_cfg["minutes"]
    out = EXP / dataset
    if out.exists():
        raise ValueError(f"frozen output already exists: {out}")
    receipt = freeze_receipt()
    raw, data_receipt = read_prefix(ROOT / spec_cfg["source"], minutes, cfg["end"])
    if sha(ROOT / spec_cfg["source"]) != spec_cfg["sha256"]:
        raise ValueError("source identity changed")
    raw["_data_gap"] = declared_gaps(raw.index, minutes)
    frame = compute_features(raw)
    frame["rsi"] = rsi_series(frame, cfg["rsi_length"])
    signals = frame.signal.to_numpy(int)
    gate = zone_admission(signals, frame.rsi.to_numpy(float), cfg["rsi_lower"], cfg["rsi_upper"])
    context = match_context(frame, minutes)
    admissible = (signals != 0) & context["valid"]
    out.mkdir(parents=True)
    save(out / "code_receipt.json", receipt)
    save(out / "source_receipt.json", data_receipt)
    counts = dict(bars=int(len(frame)), minutes=minutes, raw_signals=int((signals != 0).sum()),
                  admissible=int(admissible.sum()),
                  admissible_long=int((admissible & (signals == 1)).sum()),
                  admissible_short=int((admissible & (signals == -1)).sum()),
                  gap_bars=int(frame["_data_gap"].sum()),
                  rsi_line_passed=int((gate[admissible] != 0).sum()))
    results = dict(dataset=dataset, config=spec_cfg, counts=counts, data=data_receipt,
                   source_commit=receipt["source_commit"], arms={}, fee_curves={})
    accounting = []
    for arm, plan in cfg["arms"].items():
        spec = ParamSpec(stop_fraction=cfg["stop_fraction"], partial_fraction=plan["partial"],
                         be_cost_fraction=plan["be_cost"])
        prepared = prepare(frame, spec)
        entry_signal = signals if plan["gate"] is None else gate
        rows = run_arm(frame, prepared, context, entry_signal, cfg["primary_path"], minutes)
        audit(rows)
        draws = {int(x["signal_i"]): draw_controls(context, int(x["signal_i"]), int(x["side"]), cfg)
                 for x in rows}
        control_rows = []
        for x in rows:
            for number, j in enumerate(draws[int(x["signal_i"])], 1):
                control = replay_entry(prepared, int(j), side_override=int(x["side"]),
                                       path_mode=cfg["primary_path"])
                if control is None:
                    continue
                control = enrich(control, frame, minutes)
                control.update(actual_signal_i=int(x["signal_i"]), draw_number=number,
                               actual_month=x["month"], vol_bucket=int(context["bucket"][int(j)]))
                control_rows.append(control)
        audit(control_rows)
        accounting.append(dict(arm=arm, actual=len(rows), controls=len(control_rows), passed=True))
        save(out / f"{arm}_trades.json", rows)
        pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows]).to_csv(
            out / f"{arm}_trades.csv", index=False)
        results["fee_curves"][arm] = fee_curve([r for r in rows if not r["censored"]],
                                               cfg["fee_sensitivity_rates"])
        groups = {"all": rows, "long": [r for r in rows if r["side"] == 1],
                  "short": [r for r in rows if r["side"] == -1]}
        groups.update({year: [r for r in rows if r["month"][:4] == year]
                       for year in sorted({r["month"][:4] for r in rows})})
        results["arms"][arm] = {name: dict(stats=stats(group), control=compare(group, control_rows, cfg))
                                for name, group in groups.items()}
        s = results["arms"][arm]["all"]["stats"]
        print(f"{dataset:8s} {arm:20s} n={s['natural']:5d} netR={s['net_r']:+10.2f} "
              f"perTrade={None if s['mean_net_r'] is None else round(s['mean_net_r'], 4)} "
              f"PF={None if s['profit_factor'] is None else round(s['profit_factor'], 3)} "
              f"grossR={s['gross_r']:+9.2f}", flush=True)
    save(out / "accounting_validation.json", accounting)
    save(out / "results.json", results)
    print("Saved", out, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    run(parser.parse_args().dataset)
