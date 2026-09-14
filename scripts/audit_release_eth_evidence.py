"""Reconcile saved ETH ledgers and matching features without new trade replay.

The original timestamp-first reader admits only the frozen pre-holdout prefix.
Reconstructed features use each signal bar and its past; no new candidates,
positions or outcomes are evaluated. Every financial result comes from saved
ledgers. This is a verification companion, not a new experiment.
"""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from yoyo.evaluation.release_eth_multitf import OUT, SOURCE, aggregate, assert_frozen_prefix, dump, features, policies, read_prefix
from yoyo.evaluation.release_eth_metrics import trade_stats
from yoyo.layers.l3_backtest.release_eth_multitf import Replay


def close(a, b, label):
    if not np.allclose(a, b, rtol=1e-9, atol=1e-7, equal_nan=True):
        raise AssertionError(label)


def main():
    phases = {p: json.loads((OUT / p / "all_summaries.json").read_text()) for p in ("development", "validation")}
    frozen = json.loads((OUT / "selection.json").read_text())
    expected = json.loads((OUT / "validation/source_receipt.json").read_text())
    raw, receipt = read_prefix(SOURCE, "2026-05-01T00:00Z")
    assert_frozen_prefix(receipt, expected)
    frames, engines, audits, diagnostics = {}, {}, [], {}
    pmap = policies()
    for phase, summaries in phases.items():
        for row in summaries:
            m, arm, key = row["minutes"], row["arm"], row["key"]
            feature_key = (m, arm if arm in {"C1", "C2", "C5"} else "C0")
            if feature_key not in frames:
                bars, _ = aggregate(raw, m)
                frames[feature_key] = features(bars, m, feature_key[1])
            f = frames[feature_key]
            if (m, arm) not in engines:
                engines[(m, arm)] = Replay(f, pmap[arm], m)
            engine = engines[(m, arm)]
            t = pd.read_csv(OUT / phase / f"{key}_trades.csv.gz")
            controls_path = OUT / phase / f"{key}_controls.csv.gz"
            c = pd.read_csv(controls_path) if row["controls"] else pd.DataFrame()
            s = row["stats"]
            close(500 + t.net_pnl.sum(), s["final_equity"], key + " account")
            close(t.fees.sum(), s["fees"], key + " fees total")
            close((t.net_return > 0).mean() * 100, s["win_rate_pct"], key + " wins")
            close(t.net_return.mean() * 1e4, s["net_bp"], key + " unit expectancy")
            for name, ledger in (("case", t), ("control", c)):
                if ledger.empty:
                    continue
                close(ledger.qty * (ledger.entry_price + ledger.exit_price) * row["policy"]["fee"], ledger.fees, key + name + " fee formula")
                close(ledger.direction * ledger.qty * (ledger.exit_price - ledger.entry_price), ledger.gross_pnl, key + name + " gross formula")
                close(ledger.gross_pnl - ledger.fees, ledger.net_pnl, key + name + " net formula")
                signal = pd.to_datetime(ledger.signal_time, utc=True)
                entry = pd.to_datetime(ledger.entry_time, utc=True)
                exit_time = pd.to_datetime(ledger.exit_time, utc=True)
                assert (entry == signal + pd.Timedelta(minutes=m)).all(), key + " entry clock"
                assert (signal >= pd.Timestamp(row["start"])).all() and (exit_time <= pd.Timestamp(row["end_exclusive"])).all(), key + " boundary"
                assert (exit_time >= entry).all(), key + " exit clock"
                assert not np.isinf(ledger[["mfe_r", "mae_r"]].to_numpy(float)).any(), key + " infinite R"
                fi = ledger.signal_i.to_numpy(int)
                assert (f.open_time.iloc[fi].to_numpy() == signal.to_numpy()).all(), key + " feature index"
            matched = t[t.matched.astype(bool)]
            if len(c):
                assert not c.duplicated(["signal_i", "direction"]).any(), key + " reused control+side"
                ids = c.case_id.to_numpy(int)
                cases = t.iloc[ids].reset_index(drop=True)
                ct = pd.to_datetime(c.signal_time, utc=True)
                case_times = pd.to_datetime(cases.signal_time, utc=True)
                assert (c.direction.to_numpy() == cases.direction.to_numpy()).all(), key + " match side"
                assert (ct.dt.strftime("%Y-%m").to_numpy() == case_times.dt.strftime("%Y-%m").to_numpy()).all(), key + " match month"
                assert ((ct.dt.tz_convert("Asia/Hong_Kong").dt.hour // 6).to_numpy() == (case_times.dt.tz_convert("Asia/Hong_Kong").dt.hour // 6).to_numpy()).all(), key + " match block"
                assert ((ct - case_times).abs() > pd.Timedelta(hours=48)).all(), key + " exclusion"
                ci, si = c.signal_i.to_numpy(int), cases.signal_i.to_numpy(int)
                assert (f.vol_bin.iloc[ci].to_numpy() == f.vol_bin.iloc[si].to_numpy()).all(), key + " causal volatility bin"
                assert (f.vol_bin.iloc[ci].to_numpy() == c.match_vol_bin.to_numpy()).all(), key + " recorded bin"
                assert engine.allowed[ci].all() and (engine.raw[ci] == 0).all(), key + " control admission"
                assert all(engine._entry_allowed(int(i), int(side), None) for i, side in zip(ci, c.direction)), key + " flat slope"
                means = c.groupby("case_id").net_return.mean()
                close(means.to_numpy(), t.loc[means.index, "control_mean"].to_numpy(), key + " control mean")
                close(t.loc[means.index, "net_return"].to_numpy() - means.to_numpy(), t.loc[means.index, "paired_excess"].to_numpy(), key + " excess")
                close(matched.control_mean.mean() * 1e4, s["control_bp"], key + " pooled control")
                close(matched.paired_excess.mean() * 1e4, s["excess_bp"], key + " pooled excess")
            audits.append({"key": key, "trades": len(t), "controls": len(c), "passed": True})
            chosen = frozen["selection"][str(m)]["selected"]
            if phase == "validation" and row["precision"] == "parent_ohlc" and arm == chosen:
                d = {"max_entry_leverage": float(t.leverage.max()), "max_path_leverage": float(t.max_leverage.max()),
                     "initial_stop_pct_median": float(t.initial_risk_fraction.median() * 100),
                     "gross_0p1pct_exit_count": int(np.isclose(t.gross_return, .001, atol=1e-8).sum()),
                     "mfe_1r_but_net_loss": int(((t.mfe_r >= 1) & (t.net_return <= 0)).sum()),
                     "mfe_3r_but_net_loss": int(((t.mfe_r >= 3) & (t.net_return <= 0)).sum()),
                     "stop_reason_breakdown": {str(reason): trade_stats(g) for reason, g in t.groupby("exit_reason")}}
                diagnostics[key] = d
    output = {"verified_at": pd.Timestamp.now(tz="UTC"), "new_trade_replays": 0,
              "source_prefix_rechecked": receipt, "rows": audits,
              "result_rows": len(audits), "case_trades": sum(a["trades"] for a in audits),
              "control_trades": sum(a["controls"] for a in audits), "all_passed": True,
              "counts_overlap_across_policies_periods": True, "diagnostics": diagnostics,
              "max_leverage_summary_semantics": "summary key max_leverage is entry leverage; ledger max_leverage is intraposition marked-equity leverage"}
    dump(OUT / "evidence_audit.json", output)
    print(json.dumps({k: output[k] for k in ("all_passed", "result_rows", "case_trades", "control_trades")}))


if __name__ == "__main__":
    main()
