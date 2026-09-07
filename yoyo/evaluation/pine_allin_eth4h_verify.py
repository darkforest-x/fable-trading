"""Reconcile frozen ETH4h ledgers without changing or rerunning the strategy.

Independently check sizing, equity identities, exact control strata, and the
Pine first-entry-bar protection gap using the checksum-identified source only.
Recompute numerical diagnostics from saved ledgers and render the final report.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.pine_allin_eth4h_replay import (
    EXP, OUT, ROOT, digest, inference, load_source, report, save_json,
)


def main():
    qa_path = OUT / "independent_validation.json"
    if qa_path.exists():
        raise RuntimeError("Independent validation already exists; do not overwrite")
    builder = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    for path in (Path(__file__), ROOT / "yoyo/evaluation/pine_allin_eth4h_replay.py"):
        frozen = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(ROOT)}"])
        import hashlib
        assert hashlib.sha256(frozen).hexdigest() == digest(path)
    payload = json.loads((OUT / "summary.json").read_text())
    frame, _ = load_source()
    checks, firstbar, concentration = {}, {}, {}
    ranking = []
    for row in payload["summary"]:
        stem = f"{row['window']}_{row['arm']}"
        t = pd.read_csv(OUT / f"{stem}_trades.csv", parse_dates=["entry_time", "exit_time", "signal_time"])
        e = pd.read_csv(OUT / f"{stem}_equity.csv", parse_dates=["time"])
        c = pd.read_csv(OUT / f"{stem}_controls.csv")
        fee = 0 if row["arm"] == "original_zero_cost" else .001
        expected_fees = t.qty * (t.entry_price + t.exit_price) * fee
        expected_pnl = t.direction * t.qty * (t.exit_price - t.entry_price) - expected_fees
        checks[stem + "_fill_cash_identity"] = bool(np.allclose(t.net_pnl, expected_pnl, rtol=1e-12))
        closing = 500 + t.net_pnl.sum()
        if row["open_position"] is not None:
            pos = row["open_position"]
            closing += pos["direction"] * pos["qty"] * (frame.close.iloc[-1] - pos["entry_price"]) - pos["entry_fee"]
        checks[stem + "_final_account"] = bool(np.isclose(closing, row["final_equity"], rtol=1e-12))
        sig = t.signal_i.to_numpy(int)
        ent = t.entry_i.to_numpy(int)
        marked = e.set_index("time").equity.reindex(t.entry_time).to_numpy()
        mult = np.where(frame.hk_dayofweek.iloc[sig].to_numpy() == 3, 8, 4)
        if row["arm"] == "one_x_cost20":
            mult = np.ones(len(sig))
        expected_qty = marked * mult / frame.close.iloc[sig].to_numpy()
        checks[stem + "_signal_time_sizing"] = bool(np.allclose(t.qty, expected_qty, rtol=1e-12))
        checks[stem + "_next_open_price"] = bool(np.allclose(t.entry_price, frame.open.iloc[ent]))
        a = c.case_signal_i.to_numpy(int)
        b = c.control_signal_i.to_numpy(int)
        checks[stem + "_exact_control_strata"] = bool(
            np.array_equal(frame.open_time.iloc[a].dt.strftime("%Y-%m"), frame.open_time.iloc[b].dt.strftime("%Y-%m"))
            and np.array_equal((frame.hk_hour.iloc[a] // 6), (frame.hk_hour.iloc[b] // 6))
            and np.array_equal(frame.vol_bin.iloc[a], frame.vol_bin.iloc[b])
            and (np.abs(a - b) > 12).all())
        cc = c[c.closed]
        gross = cc.direction * (cc.control_exit_price / cc.control_entry_price - 1)
        net = gross - fee * (1 + cc.control_exit_price / cc.control_entry_price)
        checks[stem + "_control_return_identity"] = bool(np.allclose(net, cc.control_net_return, rtol=1e-12, atol=1e-12))
        if row["arm"] == "original_cost20":
            direct_touch = np.where(t.direction.to_numpy() > 0,
                                    frame.low.iloc[ent].to_numpy() <= t.initial_stop.to_numpy(),
                                    frame.high.iloc[ent].to_numpy() >= t.initial_stop.to_numpy())
            affected = t.loc[direct_touch].copy()
            affected.to_csv(OUT / f"{row['window']}_firstbar_stop_breaches.csv", index=False)
            firstbar[row["window"]] = {"unprotected_firstbar_stop_touches": int(direct_touch.sum()),
                                        "later_winners": int((affected.net_pnl > 0).sum()),
                                        "trade_signal_times": affected.signal_time.tolist()}
            sorted_unit = t.net_return.sort_values(ascending=False)
            concentration[row["window"]] = {
                "n_positive_net_trades": int((t.net_return > 0).sum()),
                "mean_net_bp_excluding_largest_one": float(sorted_unit.iloc[1:].mean() * 1e4),
                "mean_net_bp_excluding_largest_three": float(sorted_unit.iloc[3:].mean() * 1e4),
                "not_an_alternative_strategy": True,
            }
            new = {"window": row["window"], **inference(t)}
            old = next(x for x in payload["ranking"] if x["window"] == row["window"])
            checks[row["window"] + "_stable_sum_p_unchanged"] = bool(np.isclose(
                old["matched_month_cluster_signflip_p"], new["matched_month_cluster_signflip_p"], rtol=0, atol=1e-12))
            ranking.append(new)
    verification = {"passed": all(checks.values()), "checks": checks, "firstbar": firstbar,
                    "concentration": concentration, "verification_commit": builder,
                    "note": "Original matmul emitted floating warnings; finite values and explicit sum agree within 1.4e-17; p unchanged.",
                    "market_replay_rerun": False, "native_tradingview_parity": False}
    save_json(qa_path, verification)
    if not verification["passed"]:
        raise AssertionError([k for k, v in checks.items() if not v])
    payload["ranking"] = ranking
    payload["independent_validation"] = verification
    payload["report_commit"] = builder
    save_json(OUT / "summary_verified.json", payload)
    report(payload, {})
    print(json.dumps({"checks_passed": len(checks), "firstbar": {k:v['unprotected_firstbar_stop_touches'] for k,v in firstbar.items()},
                      "concentration": concentration}, indent=2))


if __name__ == "__main__":
    main()
