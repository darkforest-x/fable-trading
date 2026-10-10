"""SPIKE V12.9 ETH 5m exit test: one tightening at a time, selected early, checked twice.

Source: owner 2026-10-10 chose option 3 after the 5m replay showed heavy open-profit
giveback (136 of 407 losers had touched +1R; 27 of 50 trades that touched 5R left
below it). Frozen config: ``experiments/active/exp-spike-v129-eth5m-exit-20261010-v1``.

Single variable: the exit rule. Entries, sides, entry prices, initial stops and the
recorded opposite-signal exits are frozen from two parent ledgers -- OKX ETH 5m
(exp-spike-v129-eth5m-20261010-v1) and Binance ETH 5m 2021-09..2024-01
(exp-spike-manual-system-20260925-v1 baseline). Candidates and the bar-by-bar
simulator are reused unchanged from ``spike_exit_giveback`` (tightening-only, so a
candidate acts before the recorded exit or leaves exactly as recorded). Only trades
whose incumbent replay reproduces the recorded exit are kept, per cohort.

Decision rule (config): select on OKX ordinary trades signalled before 2025-10-10;
pass only if the selected candidate beats the incumbent on both OKX last twelve
months and Binance 2021-09..2024-01 and the pooled validation paired sign-flip
p < 0.01. Matched random controls (rebuilt from the parents' control draws via the
same entry engine) show whether a change is signal-specific.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v129_eth5m as parent
from yoyo.evaluation.spike_exit_giveback import POLICIES, _max_drawdown, sign_flip_p, simulate
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v129-eth5m-exit-20261010-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_eth5m_exit.py")
KEY = ["sample", "arm", "cohort", "trade_key"]
CHECKS = ("okx_check", "binance_check")


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or float(cfg["side_cost"]) != .001:
        raise ValueError("invalid frozen exit-test config")
    return cfg


def sources() -> dict[str, str]:
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def _okx() -> tuple[dict, pd.DataFrame, pd.DataFrame, str]:
    cfg = parent.config()
    data = json.loads((Path(cfg["data_dir"]) / "receipt.json").read_text())
    base = low.load_base([Path(x) for x in data["inputs"]], pd.Timestamp(cfg["end"]))
    bars, _ = low.complete_bars(base, 5)
    facts = low.pine_facts(bars, bars, cfg["asset"], float(cfg["tick"]), 5)
    run = parent.EXP / "run_v1"
    trades = pd.read_csv(run / "trades.csv.gz")
    controls = pd.read_csv(run / "controls.csv.gz")
    controls["control_i"] = controls.control_sig
    return facts, trades, controls, cfg["tick"]


def _binance(cfg: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame, float]:
    if p.digest(Path(cfg["binance_data"])) != cfg["binance_data_sha256"]:
        raise ValueError("Binance series changed")
    raw = pd.read_csv(cfg["binance_data"])
    base = raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]
    bars, _ = p.complete_bars(base, 5)
    meta = p.source.symbol_meta()["ETHUSDT"]
    facts = low.pine_facts(bars, base, meta["asset"], float(meta["tick"]), 5)
    ledger = Path(cfg["binance_ledger"])
    end = pd.Timestamp(cfg["binance_end"])
    trades = pd.read_csv(ledger / "trades.csv.gz")
    trades = trades.loc[(trades.policy == "baseline") & (pd.to_datetime(trades.signal_close, utc=True) < end)]
    controls = pd.read_csv(ledger / "controls.csv.gz")
    controls = controls.loc[controls.policy == "baseline"].copy()
    index = facts["frame"].index
    opens = pd.to_datetime(controls.control_signal_close, utc=True) - pd.Timedelta(minutes=5)
    controls["control_i"] = index.get_indexer(pd.DatetimeIndex(opens))
    return facts, trades, controls, float(meta["tick"])


def replay(sample_of, facts: dict, trades: pd.DataFrame, controls: pd.DataFrame, tick: float) -> list[dict]:
    """Every policy on every closed signal trade and on its matched control."""
    frame = facts["frame"]
    o, h, l, c, atr = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr"))
    prepared = p.source.prepared_arm(frame, facts["gap"], facts["side"], "exit", {"symbol": "ETH"}, 5, tick)
    rows: list[dict] = []

    def run(sample, arm, cohort, key, side, entry, stop, e, x, exit_price, reason, signal_close):
        if facts["gap"][e:x + 1].any():
            rows.append({"sample": sample, "arm": arm, "cohort": cohort, "trade_key": key, "policy": "-", "parity": "gap"})
            return
        grid_stop = round(stop / tick) * tick
        for policy in POLICIES:
            out = simulate(policy, side=side, entry=entry, stop0=grid_stop, tick=tick, o=o, h=h, l=l, c=c, atr=atr,
                           e=e, x=x, exit_price=exit_price, exit_reason=str(reason))
            rows.append({"sample": sample, "arm": arm, "cohort": cohort, "trade_key": key, "side": side,
                         "signal_close": signal_close, "exit_time": frame.index[x], "policy": policy, **out})

    closed = trades.loc[~trades.censored.astype(bool)]
    for t in closed.itertuples():
        if pd.Timestamp(t.exit_time) != frame.index[int(t.exit_i)] or pd.Timestamp(t.entry_time) != frame.index[int(t.entry_i)]:
            raise ValueError("ledger bar indices do not match this frame")
        run(sample_of(t.signal_close), t.arm, "signal", t.trade_key, int(t.side), float(t.entry_price), float(t.initial_stop),
            int(t.entry_i), int(t.exit_i), float(t.exit_price), t.exit_reason, t.signal_close)
    target = closed.set_index("trade_key")
    for k in controls.loc[controls.matched.astype(bool) & controls.trade_key.isin(target.index)].itertuples():
        status, res = p.attempt(prepared, int(k.control_i), int(k.side))
        if res is None or res["censored"] or abs(float(res["net_r"]) - float(k.control_net_r)) > 1e-8:
            rows.append({"sample": sample_of(target.loc[k.trade_key, "signal_close"]), "arm": target.loc[k.trade_key, "arm"],
                         "cohort": "control", "trade_key": k.trade_key, "policy": "-", "parity": "control_mismatch"})
            continue
        run(sample_of(target.loc[k.trade_key, "signal_close"]), target.loc[k.trade_key, "arm"], "control", k.trade_key,
            int(k.side), float(res["entry_price"]), float(res["initial_stop"]), int(res["entry_i"]), int(res["exit_i"]),
            float(res["exit_price"]), res["exit_reason"], target.loc[k.trade_key, "signal_close"])
    return rows


def reproduced(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[frame.policy != "-"]
    ok = frame.loc[(frame.policy == "incumbent") & (frame.parity == "ok"), KEY].drop_duplicates()
    return frame.merge(ok, on=KEY, how="inner")


def summarize(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    frame = reproduced(frame)
    out = []
    for keys, g in frame.groupby(["sample", "arm", "cohort"]):
        piv = g.pivot_table(index="trade_key", columns="policy", values="net_r")
        order = g.loc[g.policy == "incumbent"].sort_values("exit_time").trade_key
        for policy in POLICIES:
            r = piv[policy].reindex(order)
            wins, losses = r[r > 0].sum(), -r[r < 0].sum()
            d = (piv[policy] - piv["incumbent"]).to_numpy(float)
            out.append(dict(zip(["sample", "arm", "cohort"], keys)) | {
                "policy": policy, "n": int(len(r)), "win_rate": float((r > 0).mean()), "mean_net_r": float(r.mean()),
                "median_net_r": float(r.median()), "sum_net_r": float(r.sum()),
                "profit_factor": float(wins / losses) if losses > 0 else np.inf, "max_drawdown_r": _max_drawdown(r),
                "n_ge_5r": int((r >= 5).sum()), "delta_mean_vs_incumbent": float(np.nanmean(d)),
                "sign_flip_p": np.nan if policy == "incumbent" else sign_flip_p(d, seed)})
    return pd.DataFrame(out)


def decide(summary: pd.DataFrame, frame: pd.DataFrame, seed: int) -> dict:
    """Apply the frozen selection and pass rule to ordinary signal trades."""
    sel = summary.loc[(summary["sample"] == "okx_select") & (summary.arm == "ordinary") & (summary.cohort == "signal")
                      & (summary.policy != "incumbent")].sort_values("mean_net_r", ascending=False)
    best = sel.iloc[0]
    if not best.delta_mean_vs_incumbent > 0:
        return {"selected": None, "passed": False, "reason": "no candidate beats the incumbent in selection"}
    policy = str(best.policy)
    checks = summary.set_index(["sample", "arm", "cohort", "policy"])
    deltas = {s: float(checks.loc[(s, "ordinary", "signal", policy), "delta_mean_vs_incumbent"]) for s in CHECKS}
    rep = reproduced(frame)
    pooled = rep.loc[rep["sample"].isin(CHECKS) & (rep.arm == "ordinary") & (rep.cohort == "signal")]
    piv = pooled.pivot_table(index=["sample", "trade_key"], columns="policy", values="net_r")
    d = (piv[policy] - piv["incumbent"]).to_numpy(float)
    pooled_p = sign_flip_p(d, seed)
    passed = all(v > 0 for v in deltas.values()) and pooled_p < .01
    return {"selected": policy, "selection_delta": float(best.delta_mean_vs_incumbent), "check_deltas": deltas,
            "pooled_check_delta": float(np.nanmean(d)), "pooled_check_p": pooled_p, "passed": passed}


def main() -> None:
    cfg = config()
    code = sources()
    split = pd.Timestamp(cfg["okx_split"])
    okx_facts, okx_trades, okx_controls, okx_tick = _okx()
    bn_facts, bn_trades, bn_controls, bn_tick = _binance(cfg)
    rows = replay(lambda t: "okx_select" if pd.Timestamp(t) < split else "okx_check", okx_facts, okx_trades,
                  okx_controls, float(okx_tick))
    rows += replay(lambda t: "binance_check", bn_facts, bn_trades, bn_controls, bn_tick)
    frame = pd.DataFrame(rows)
    out = EXP / "results_v1"
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary = summarize(frame, cfg["stat_seed"])
    summary.to_csv(out / "summary.csv", index=False)
    verdict = decide(summary, frame, cfg["stat_seed"])
    parity = frame.loc[frame.policy.isin(["incumbent", "-"])].groupby(["sample", "cohort", "parity"]).size()
    receipt = {"experiment_id": cfg["experiment_id"], "verdict": verdict,
               "parity": {":".join(map(str, k)): int(v) for k, v in parity.items()}, "code": code,
               "files": {f: p.digest(out / f) for f in ("trades.csv.gz", "summary.csv")},
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    p.dump(out / "receipt.json", parent._clean(receipt))
    print(json.dumps(parent._clean({"verdict": verdict, "parity": receipt["parity"]}), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
