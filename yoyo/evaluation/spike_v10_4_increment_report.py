"""Tables, statistics and check figures for the V10.4 1h increment audit.

Source: per-symbol outputs of `spike_v10_4_increment` (committed runner) and, for
the reproduction check, the original run's per-stream ledgers and a rerun of the
original commit's code. Nothing here re-simulates a trade except where a figure
redraws bars from the same archive. All cuts by later pairing outcome are
descriptive attribution on history that was already read, not filters that were
knowable at the V9 bar.

C-A uncertainty: UTC event months are resampled with replacement (seed 91509,
2,000 draws); each draw carries every trade of both arms in the drawn months and
recomputes each arm's own mean, so different trade counts per arm are respected.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v9_full_report import block_statistics
from yoyo.evaluation.spike_account_growth import simulate_shared_account

ARMS = ("v9_only", "break_only", "joint")
ARM_LABEL = {"v9_only": "A · V9_ONLY", "break_only": "B · BREAK_ONLY", "joint": "C · JOINT"}
ORIGINAL = Path("experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1")
ACCOUNT_CONFIG = Path("experiments/active/exp-spike-account-growth-20260913-v1/config.json")
PARITY = ["signal_i", "entry_i", "entry_time", "entry_price", "initial_stop", "initial_risk", "exit_i", "exit_time",
          "exit_price", "exit_reason", "net_r", "gross_r", "censored", "mfe_r"]
SEED, REPS = 91509, 2000


def cat(pattern: str) -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted(glob.glob(pattern)) if os.path.getsize(p)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load(run: Path) -> dict[str, pd.DataFrame]:
    out = {name: cat(str(run / "streams" / "*" / f"{name}.csv.gz"))
           for name in ("trades", "statuses", "controls", "v9_events", "joints", "lines", "breaks", "shadows",
                        "gap_audit", "pair_attempts", "spike_events", "line_events")}
    t = out["trades"]
    for column in ("signal_bar_open", "entry_time", "exit_time"):
        t[column] = pd.to_datetime(t[column], utc=True, format="mixed")
    t["signal_close"] = t.signal_bar_open + pd.Timedelta(hours=1)
    t["period"] = np.where(t.signal_close < study.SPLIT, "earlier", "later")
    t["month"] = t.signal_close.dt.strftime("%Y-%m")
    t["hold_hours"] = (t.exit_time - t.entry_time).dt.total_seconds() / 3600
    t = t.merge(out["controls"][["trade_key", "matched", "control_net_r", "control_net_return"]],
                on="trade_key", how="left", validate="one_to_one")
    out["trades"] = t
    s = out["statuses"]
    s["signal_bar_open"] = pd.to_datetime(s.signal_bar_open, utc=True, format="mixed")
    s["period"] = np.where(s.signal_bar_open + pd.Timedelta(hours=1) < study.SPLIT, "earlier", "later")
    return out


# ---------- reproduction ----------

def reproduction(run: Path, repro: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    original = cat(str(ORIGINAL / "results/run_v1/streams/*/trades.csv.gz"))
    original = original.loc[original.timeframe == "1h"]
    rerun = cat(str(repro / "*" / "trades.csv.gz"))
    new = cat(str(run / "streams" / "*" / "trades.csv.gz"))
    new = new.loc[new.arm.isin(["v9_only", "joint"])]
    rows, diffs = [], []
    for name, other in (("original_code_rerun", rerun), ("new_runner_head", new)):
        merged = original[["trade_key", *PARITY]].merge(other[["trade_key", *PARITY]], on="trade_key", how="outer",
                                                        suffixes=("_orig", "_other"), indicator=True)
        row = {"comparison": f"original run_v1 vs {name}", "original_trades": len(original), "other_trades": len(other),
               "only_original": int((merged._merge == "left_only").sum()),
               "only_other": int((merged._merge == "right_only").sum())}
        both = merged.loc[merged._merge == "both"]
        for column in PARITY:
            a, b = both[f"{column}_orig"], both[f"{column}_other"]
            if a.dtype.kind in "fc":
                ok = np.isclose(a.astype(float), b.astype(float), rtol=1e-10, atol=1e-10, equal_nan=True)
            else:
                ok = a.astype(str).to_numpy() == b.astype(str).to_numpy()
            row[f"diff_{column}"] = int((~ok).sum())
            if (~ok).any():
                diffs.append(both.loc[~ok, ["trade_key", f"{column}_orig", f"{column}_other"]].assign(field=column))
        rows.append(row)
    ojoint = cat(str(ORIGINAL / "results/run_v1/streams/*/joints.csv.gz"))
    ojoint = ojoint.loc[ojoint.timeframe == "1h", ["stream_key", "i", "spike_i", "break_i", "order", "uid", "score"]]
    njoint = cat(str(run / "streams" / "*" / "joints.csv.gz"))
    njoint["stream_key"] = "binance_um:" + njoint.symbol + ":1h"
    merged = ojoint.merge(njoint[["stream_key", "i", "spike_i", "break_i", "order", "uid", "score"]],
                          on=["stream_key", "i"], how="outer", suffixes=("_o", "_n"), indicator=True)
    both = merged.loc[merged._merge == "both"]
    rows.append({"comparison": "original joints vs new runner joints (all bars, incl. warm-up)",
                 "original_trades": len(ojoint), "other_trades": len(njoint),
                 "only_original": int((merged._merge == "left_only").sum()),
                 "only_other": int((merged._merge == "right_only").sum()),
                 **{f"diff_{c}": int((both[f"{c}_o"] != both[f"{c}_n"]).sum()) for c in ("spike_i", "break_i", "order", "uid")},
                 "diff_score": int((~np.isclose(both.score_o, both.score_n, rtol=1e-12, atol=0)).sum())})
    ocontrol = cat(str(ORIGINAL / "results/run_v1/streams/*/controls.csv.gz"))
    ncontrol = cat(str(run / "streams" / "*" / "controls.csv.gz"))
    m = ocontrol.merge(ncontrol, on="trade_key", how="inner", suffixes=("_o", "_n"))
    rows.append({"comparison": "original controls vs new controls (same trade_key)", "original_trades": int(ocontrol.trade_key.str.contains(":1h:").sum()),
                 "other_trades": int(ncontrol.trade_key.str.contains(":v9_long:|:joint:").sum()),
                 "only_original": 0, "only_other": 0,
                 "diff_control_signal_i": int((m.control_signal_i_o.fillna(-1) != m.control_signal_i_n.fillna(-1)).sum()),
                 "diff_control_net_r": int((~np.isclose(m.control_net_r_o, m.control_net_r_n, rtol=1e-10, atol=1e-10, equal_nan=True)).sum())})
    return pd.DataFrame(rows), (pd.concat(diffs) if diffs else pd.DataFrame(columns=["trade_key", "field"]))


# ---------- per-arm tables ----------

def drawdown(closed: pd.DataFrame) -> float:
    path = closed.sort_values("exit_time").net_r.cumsum().to_numpy()
    if not len(path):
        return 0.0
    return float((np.maximum.accumulate(np.r_[0.0, path])[1:] - path).max())


def concurrency(part: pd.DataFrame) -> tuple[float, int]:
    if not len(part):
        return np.nan, 0
    events = pd.concat([pd.Series(1, index=part.entry_time), pd.Series(-1, index=part.exit_time)])
    level = events.groupby(level=0).sum().sort_index().cumsum()
    return float(level.median()), int(level.max())


def arm_metrics(trades: pd.DataFrame, statuses: pd.DataFrame) -> dict:
    closed = trades.loc[trades.status == "closed"]
    r = closed.net_r
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    pair = closed.loc[closed.matched.fillna(False).astype(bool)]
    delta = pair.net_r - pair.control_net_r
    p = block_statistics(delta, pair.month)["p_month_signflip"] if len(pair) > 1 else np.nan
    med, mx = concurrency(trades)
    return {"signals": len(statuses), "trades_taken": len(trades), "closed": len(closed),
            "censored_boundary": int((trades.status == "censored_boundary").sum()),
            "censored_gap": int((trades.status == "censored_gap").sum()),
            "skipped_in_position": int((statuses.status == "skipped_in_position").sum()),
            "risk_invalid": int((statuses.status == "risk_invalid").sum()),
            "no_next_bar": int(statuses.status.isin(["no_next_bar", "next_bar_is_gap"]).sum()),
            "win_rate": r.gt(0).mean() if len(r) else np.nan,
            "mean_gross_r": closed.gross_r.mean() if len(r) else np.nan, "mean_net_r": r.mean() if len(r) else np.nan,
            "mean_net_bp": closed.net_return.mean() * 1e4 if len(r) else np.nan,
            "pf": wins / losses if losses > 0 else np.nan, "total_net_r": r.sum(),
            "event_drawdown_r": drawdown(closed), "median_hold_h": closed.hold_hours.median() if len(r) else np.nan,
            "concurrent_median": med, "concurrent_max": mx,
            "random_mean_r": pair.control_net_r.mean() if len(pair) else np.nan,
            "excess_vs_random_r": delta.mean() if len(pair) else np.nan, "p_vs_random": p}


def main_table(d: dict) -> pd.DataFrame:
    rows = []
    for arm in ARMS:
        t, s = d["trades"].loc[d["trades"].arm == arm], d["statuses"].loc[d["statuses"].arm == arm]
        for period in ("full", "earlier", "later"):
            tp = t if period == "full" else t.loc[t.period == period]
            sp = s if period == "full" else s.loc[s.period == period]
            rows.append({"arm": ARM_LABEL[arm], "period": period, **arm_metrics(tp, sp)})
    return pd.DataFrame(rows)


def conservation(d: dict) -> pd.DataFrame:
    rows = []
    for arm in ARMS:
        s = d["statuses"].loc[d["statuses"].arm == arm]
        t = d["trades"].loc[d["trades"].arm == arm]
        counts = s.status.value_counts().to_dict()
        taken = counts.get("closed", 0) + counts.get("censored_boundary", 0) + counts.get("censored_gap", 0)
        rows.append({"arm": ARM_LABEL[arm], "candidates": len(s), **{k: counts.get(k, 0) for k in
                     ("closed", "censored_boundary", "censored_gap", "skipped_in_position", "risk_invalid",
                      "no_next_bar", "next_bar_is_gap")},
                     "status_sum_equals_candidates": sum(counts.values()) == len(s),
                     "taken_equals_trade_rows": taken == len(t)})
    return pd.DataFrame(rows)


# ---------- C - A ----------

def c_minus_a(d: dict, period: str) -> dict:
    t = d["trades"].loc[d["trades"].status == "closed"]
    if period != "full":
        t = t.loc[t.period == period]
    a, c = t.loc[t.arm == "v9_only"], t.loc[t.arm == "joint"]
    months = sorted(set(t.month))
    agg = {arm: part.groupby("month").agg(n=("net_r", "size"), r=("net_r", "sum"), bp=("net_return", "sum"))
           .reindex(months, fill_value=0) for arm, part in (("A", a), ("C", c))}
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(months), size=(REPS, len(months)))
    diffs, bp_diffs, valid = [], [], 0
    nA, rA, bA = (agg["A"][k].to_numpy(float) for k in ("n", "r", "bp"))
    nC, rC, bC = (agg["C"][k].to_numpy(float) for k in ("n", "r", "bp"))
    for row in draws:
        na, nc = nA[row].sum(), nC[row].sum()
        if na == 0 or nc == 0:
            continue
        valid += 1
        diffs.append(rC[row].sum() / nc - rA[row].sum() / na)
        bp_diffs.append((bC[row].sum() / nc - bA[row].sum() / na) * 1e4)
    lo, hi = np.quantile(diffs, [.025, .975]) if diffs else (np.nan, np.nan)
    blo, bhi = np.quantile(bp_diffs, [.025, .975]) if bp_diffs else (np.nan, np.nan)
    return {"period": period, "months": len(months), "months_without_A": int((nA == 0).sum()),
            "months_without_C": int((nC == 0).sum()), "reps": REPS, "valid_reps": valid,
            "A_closed": len(a), "C_closed": len(c), "C_per_A_trades": len(c) / len(a) if len(a) else np.nan,
            "A_mean_r": a.net_r.mean(), "C_mean_r": c.net_r.mean(), "diff_mean_r": c.net_r.mean() - a.net_r.mean(),
            "ci95_low_r": lo, "ci95_high_r": hi,
            "A_mean_bp": a.net_return.mean() * 1e4, "C_mean_bp": c.net_return.mean() * 1e4,
            "diff_mean_bp": (c.net_return.mean() - a.net_return.mean()) * 1e4, "ci95_low_bp": blo, "ci95_high_bp": bhi,
            "A_total_r": a.net_r.sum(), "C_total_r": c.net_r.sum(), "diff_total_r": c.net_r.sum() - a.net_r.sum()}


# ---------- V9 event attribution ----------

def v9_ledger(d: dict) -> pd.DataFrame:
    v9 = d["v9_events"].copy()
    sh = d["shadows"].loc[d["shadows"].path == "v9_event"].drop(columns=["path"])
    v9 = v9.merge(sh.add_prefix("v9path_").rename(columns={"v9path_symbol": "symbol", "v9path_signal_i": "signal_i"}),
                  on=["symbol", "signal_i"], how="left", validate="one_to_one")
    a = d["statuses"].loc[d["statuses"].arm == "v9_only", ["symbol", "signal_i", "status", "blocking_trade"]]
    v9 = v9.merge(a.rename(columns={"status": "A_status", "blocking_trade": "A_blocking_trade"}),
                  on=["symbol", "signal_i"], how="left", validate="one_to_one")
    v9["signal_bar_open"] = pd.to_datetime(v9.signal_bar_open, utc=True, format="mixed")
    v9["period"] = np.where(v9.signal_bar_open + pd.Timedelta(hours=1) < study.SPLIT, "earlier", "later")
    v9["paired"] = v9.pair_status.eq("joint")
    v9["reason_group"] = v9.pair_status.str.split(":").str[0]
    return v9


def filtering(v9: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ("full", "earlier", "later"):
        part = v9 if period == "full" else v9.loc[v9.period == period]
        for label, g in (("paired_into_joint", part.loc[part.paired]),
                         ("not_paired_decided", part.loc[~part.paired & part.pair_status.ne("pending_at_data_end")]),
                         ("pending_at_data_end", part.loc[part.pair_status.eq("pending_at_data_end")])):
            closed = g.loc[g.v9path_status == "closed"]
            rows.append({"period": period, "group": label, "v9_events": len(g), "shadow_closed": len(closed),
                         "shadow_censored": int(g.v9path_status.isin(["censored_boundary", "censored_gap"]).sum()),
                         "shadow_no_trade": int(g.v9path_status.isin(["risk_invalid", "no_next_bar", "next_bar_is_gap"]).sum()),
                         "mean_net_r": closed.v9path_net_r.mean(), "total_net_r": closed.v9path_net_r.sum(),
                         "win_rate": closed.v9path_net_r.gt(0).mean(),
                         "loss_r": closed.v9path_net_r[closed.v9path_net_r < 0].sum(),
                         "profit_r": closed.v9path_net_r[closed.v9path_net_r > 0].sum(),
                         "ge3r": int(closed.v9path_net_r.ge(3).sum()), "ge10r": int(closed.v9path_net_r.ge(10).sum()),
                         "mean_net_bp": closed.v9path_net_return.mean() * 1e4})
    return pd.DataFrame(rows)


def reasons(v9: pd.DataFrame) -> pd.DataFrame:
    closed = v9.v9path_status.eq("closed")
    g = v9.assign(r=v9.v9path_net_r.where(closed)).groupby("pair_status")
    out = g.agg(v9_events=("signal_i", "size"), shadow_closed=("r", "count"), shadow_mean_r=("r", "mean"),
                shadow_total_r=("r", "sum"), ge3r=("r", lambda x: int(x.ge(3).sum())),
                ge10r=("r", lambda x: int(x.ge(10).sum()))).reset_index()
    out["share"] = out.v9_events / len(v9)
    return out.sort_values("v9_events", ascending=False)


def realtime_split(v9: pd.DataFrame, attempts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """What was knowable at the V9 bar itself, and what only waiting revealed.

    joint_now: the joint fired on the V9 bar (break came first or same bar);
    refused_now: a pair attempt on the V9 bar failed a second-bar gate;
    no_line_now: no confirmed line was available; pending: evidence saved on a
    line that had not broken yet -- only these depend on the next 6 bars.
    """
    refused_now = set()
    if len(attempts):
        at = attempts.loc[~attempts.eligible.astype(bool) & (attempts.i == attempts.spike_i)]
        refused_now = set(zip(at.symbol, at.spike_i))
    now = np.where(v9.paired & v9.joint_order.isin(["same_bar", "break_first"]), "joint_now",
                   np.where(v9.pair_status.eq("no_available_line"), "no_line_now",
                            np.where([(s, int(i)) in refused_now for s, i in zip(v9.symbol, v9.signal_i)],
                                     "refused_now", "pending")))
    v9 = v9.assign(at_v9_bar=now)
    closed = v9.v9path_status.eq("closed")
    rows = []
    for (period, group), g in [((p, k), g) for p in ("full", "earlier", "later")
                               for k, g in (v9 if p == "full" else v9.loc[v9.period == p]).groupby("at_v9_bar")]:
        c = g.loc[closed.loc[g.index]]
        rows.append({"period": period, "at_v9_bar": group, "v9_events": len(g), "shadow_closed": len(c),
                     "mean_net_r": c.v9path_net_r.mean(), "total_net_r": c.v9path_net_r.sum(),
                     "ge3r": int(c.v9path_net_r.ge(3).sum()), "ge10r": int(c.v9path_net_r.ge(10).sum())})
    pending = v9.loc[v9.at_v9_bar == "pending"]
    later = pending.groupby("pair_status").agg(v9_events=("signal_i", "size"), v9path_total_r=("v9path_net_r", "sum"),
                                               v9path_mean_r=("v9path_net_r", "mean")).reset_index()
    return pd.DataFrame(rows), later.sort_values("v9_events", ascending=False)


def wait_policy(v9: pd.DataFrame, wait_rows: pd.DataFrame) -> pd.DataFrame:
    """Pending events only: enter all at the V9 bar, or wait and enter only the ones that pair."""
    pending = v9.loc[(v9.pair_status != "no_available_line") & ~(v9.paired & v9.joint_order.isin(["same_bar", "break_first"]))]
    sf = wait_rows.loc[wait_rows.order == "spike_first"]
    rows = []
    for period in ("full", "earlier", "later"):
        p = pending if period == "full" else pending.loc[pending.period == period]
        w = sf if period == "full" else sf.loc[np.where(pd.to_datetime(sf.signal_bar_open, utc=True) + pd.Timedelta(hours=1)
                                                        < study.SPLIT, "earlier", "later") == period]
        pc = p.loc[p.v9path_status == "closed"]
        wc = w.loc[w.jt_status == "closed"]
        rows.append({"period": period, "pending_v9_events": len(p), "enter_all_at_v9_trades": len(pc),
                     "enter_all_at_v9_total_r": pc.v9path_net_r.sum(), "enter_all_at_v9_total_bp": pc.v9path_net_return.sum() * 1e4,
                     "wait_for_joint_trades": len(wc), "wait_for_joint_total_r": wc.jt_net_r.sum(),
                     "wait_for_joint_total_bp": wc.jt_net_return.sum() * 1e4,
                     "same_events_entered_at_v9_total_r": w.loc[w.v9_status == "closed", "v9_net_r"].sum()})
    return pd.DataFrame(rows)


# ---------- waiting ----------

def waiting(d: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    j = d["joints"].loc[d["joints"].in_window.astype(bool)].copy()
    sh = d["shadows"]
    v9p = sh.loc[sh.path == "v9_event"].add_prefix("v9_").rename(columns={"v9_symbol": "symbol", "v9_signal_i": "spike_i"})
    jp = sh.loc[sh.path == "joint_event"].add_prefix("jt_").rename(columns={"jt_symbol": "symbol", "jt_signal_i": "i"})
    w = j.merge(v9p, on=["symbol", "spike_i"], how="left").merge(jp, on=["symbol", "i"], how="left")
    w["wait_bars"] = w.i - w.spike_i
    both = w.v9_status.eq("closed") & w.jt_status.eq("closed")
    w["both_closed"] = both
    w["entry_diff_bp"] = (w.jt_entry_price / w.v9_entry_price - 1) * 1e4
    w["risk_pct_v9"] = w.v9_initial_risk_frac * 100
    w["risk_pct_joint"] = w.jt_initial_risk_frac * 100
    w["net_bp_v9"] = w.v9_net_return * 1e4
    w["net_bp_joint"] = w.jt_net_return * 1e4
    # Common-risk view: the joint path's net return divided by the V9 path's
    # initial risk fraction, i.e. the joint outcome in V9-R units.
    w["joint_in_v9_r"] = w.jt_net_return / w.v9_initial_risk_frac
    w["same_exit"] = (w.jt_exit_i == w.v9_exit_i) & (w.jt_exit_reason == w.v9_exit_reason)
    rows = []
    for order, g in [("all", w), *w.groupby("order")]:
        b = g.loc[g.both_closed]
        rows.append({"order": order, "joints": len(g), "both_closed": len(b),
                     "wait_bars_mean": g.wait_bars.mean(), "wait_bars_max": int(g.wait_bars.max()) if len(g) else 0,
                     "entry_diff_bp_mean": b.entry_diff_bp.mean(), "entry_diff_bp_median": b.entry_diff_bp.median(),
                     "risk_pct_v9_median": b.risk_pct_v9.median(), "risk_pct_joint_median": b.risk_pct_joint.median(),
                     "net_r_v9_mean": b.v9_net_r.mean(), "net_r_joint_mean": b.jt_net_r.mean(),
                     "joint_in_v9_r_mean": b.joint_in_v9_r.mean(),
                     "net_bp_v9_mean": b.net_bp_v9.mean(), "net_bp_joint_mean": b.net_bp_joint.mean(),
                     "same_exit_share": b.same_exit.mean()})
    return pd.DataFrame(rows), w


# ---------- occupancy ----------

def holding_mask(trades: pd.DataFrame) -> dict:
    spans: dict = {}
    for row in trades.itertuples(index=False):
        end = row.exit_i if row.status != "censored_boundary" else 10**9
        spans.setdefault(row.symbol, []).append((int(row.signal_i), int(end)))
    return spans


def in_position(spans: dict, symbol: str, t: int) -> bool:
    return any(s < t < e for s, e in spans.get(symbol, []))


def occupancy(d: dict, v9: pd.DataFrame) -> pd.DataFrame:
    t = d["trades"]
    a_spans = holding_mask(t.loc[t.arm == "v9_only"])
    c_spans = holding_mask(t.loc[t.arm == "joint"])
    c = t.loc[(t.arm == "joint")].merge(d["joints"][["symbol", "i", "spike_i", "order"]],
                                        left_on=["symbol", "signal_i"], right_on=["symbol", "i"], how="left")
    c["A_in_position_at_joint_bar"] = [in_position(a_spans, s, int(i)) for s, i in zip(c.symbol, c.signal_i)]
    a_status = v9.set_index(["symbol", "signal_i"]).A_status
    c["A_status_of_its_v9_event"] = [a_status.get((s, int(k))) for s, k in zip(c.symbol, c.spike_i)]
    rows = []
    for label, g in [("C trades, all", c), *[(f"C trades, A {st}", g) for st, g in c.groupby("A_status_of_its_v9_event")],
                     ("C trades while A held a position at the joint bar", c.loc[c.A_in_position_at_joint_bar])]:
        closed = g.loc[g.status == "closed"]
        rows.append({"group": label, "trades": len(g), "closed": len(closed), "mean_net_r": closed.net_r.mean(),
                     "total_net_r": closed.net_r.sum()})
    paired_v9 = v9.loc[v9.paired]
    c_took = set(zip(c.symbol, c.spike_i))
    skipped_by_c = paired_v9.loc[[(s, int(k)) not in c_took for s, k in zip(paired_v9.symbol, paired_v9.signal_i)]]
    rows.append({"group": "paired V9 events whose joint C skipped (C in position / invalid)", "trades": len(skipped_by_c),
                 "closed": int(skipped_by_c.v9path_status.eq("closed").sum()),
                 "mean_net_r": skipped_by_c.v9path_net_r.mean(), "total_net_r": skipped_by_c.v9path_net_r.sum()})
    a_skipped = v9.loc[v9.A_status == "skipped_in_position"]
    for label, g in (("V9 events A skipped in position", a_skipped),
                     ("  of which later paired into a joint", a_skipped.loc[a_skipped.paired])):
        closed = g.loc[g.v9path_status == "closed"]
        rows.append({"group": label, "trades": len(g), "closed": len(closed), "mean_net_r": closed.v9path_net_r.mean(),
                     "total_net_r": closed.v9path_net_r.sum()})
    return pd.DataFrame(rows)


# ---------- errata ----------

def errata(original_stats: Path, out: Path) -> pd.DataFrame:
    t = pd.read_csv(original_stats / "trades.csv.gz")
    c = pd.read_csv(original_stats / "controls.csv.gz")
    j = t.loc[(t.timeframe == "1h") & (t.arm == "joint") & ~t.censored.astype(bool)]
    k_detail = max(1, len(j) // 100)
    top = j.net_r.nlargest(k_detail)
    removed_detail = j.loc[j.net_r.isin(top)]
    removed_detail[["trade_key", "symbol", "signal_bar_open", "net_r"]].sort_values("net_r", ascending=False).to_csv(
        out / "errata_top1pct_removed_ids.csv", index=False)
    pair = j.merge(c.loc[c.matched.astype(bool), ["trade_key", "control_net_r"]], on="trade_key")
    return pd.DataFrame([
        {"item": "1h 拆解报告第 7 节「去掉最好的 1%（15 笔）」", "original_text": "15 笔，剩 1,462",
         "actual": f"len//100 = {len(j)}//100 = {k_detail} 笔；isin 去掉 {len(removed_detail)} 行，剩 {len(j) - len(removed_detail)}",
         "consequence": "标签「15 笔」写错，删的是 14 笔；表中每笔 R/合计 R 数值本身按 14 笔计算，无需改数"},
        {"item": "配对超额 vs 两组均值相减", "original_text": "+0.0878 − (−0.1550) 读作 +0.2436",
         "actual": f"配对超额只在 {len(pair)} 对匹配成功的交易上算（共 {len(j)} 笔），"
                   f"配对内信号均值 {pair.net_r.mean():+.4f}、随机均值 {pair.control_net_r.mean():+.4f}、差 {(pair.net_r - pair.control_net_r).mean():+.4f}；"
                   f"表中「每笔净R」用全部 {len(j)} 笔",
         "consequence": "0.2436 ≠ 0.0878+0.1550=0.2428 来自 3 笔未匹配交易的分母差；不是计算错误，需在表注写明"},
    ])


# ---------- account cashbook (realized balance only) ----------

def cashbook(d: dict) -> pd.DataFrame:
    config = json.loads(ACCOUNT_CONFIG.read_text())["account"]
    rows = []
    for arm in ("v9_only", "joint"):
        t = d["trades"].loc[d["trades"].arm == arm].copy()
        t["base_asset"] = t.asset
        t["side"] = 1
        t["trade_id"] = t.trade_key
        t["censored"] = t.status != "closed"
        for sizing in config["sizing"]:
            for risk in config["risk_fraction"]:
                res = simulate_shared_account(t[["entry_time", "exit_time", "base_asset", "entry_price", "initial_risk",
                                                 "side", "trade_id", "censored", "net_return"]].assign(
                                                     net_return=t.net_return.fillna(0.0)),
                                              sizing=sizing, risk_fraction=risk,
                                              initial_balance=config["initial_balance_usdt"],
                                              portfolio_risk_cap=config["portfolio_initial_risk_cap"],
                                              gross_leverage_cap=config["gross_entry_notional_leverage_cap"],
                                              entry_floor_fraction=config["entry_floor_fraction_of_initial"],
                                              seed=config["same_timestamp_seed"])
                s = res["summary"]
                rows.append({"arm": ARM_LABEL[arm], "sizing": sizing, "risk_fraction": risk,
                             **{k: s.get(k) for k in ("candidates", "selected", "rejected", "closed",
                                                      "censored_boundary", "final_balance", "net_return",
                                                      "floor_triggered", "bankrupt", "max_gross_leverage",
                                                      "max_portfolio_initial_risk")},
                             "realized_max_drawdown": _realized_drawdown(res["equity_curve"]),
                             "rejection_reasons": json.dumps(s.get("rejection_reasons", {}))})
    return pd.DataFrame(rows)


def _realized_drawdown(curve: pd.DataFrame) -> float:
    """Peak-to-trough of the exit-time (realized) balance; no mark-to-market exists."""
    column = next((c for c in ("balance", "balance_after", "equity") if c in curve), None)
    if column is None or not len(curve):
        return float("nan")
    values = curve[column].to_numpy(float)
    peak = np.maximum.accumulate(values)
    return float(((peak - values) / peak).max())


# ---------- figures ----------

def overview_figure(d: dict, out: Path) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    colors = {"v9_only": "#4679C9", "break_only": "#8a8f98", "joint": "#AD7B29"}
    closed = d["trades"].loc[d["trades"].status == "closed"]
    for arm in ARMS:
        part = closed.loc[closed.arm == arm].sort_values("exit_time")
        axes[0].plot(part.exit_time, part.net_r.cumsum(), color=colors[arm], label=f"{ARM_LABEL[arm]}（{len(part)} 笔）")
    axes[0].axvline(study.SPLIT, color="#222", linewidth=0.8, linestyle=":")
    axes[0].axhline(0, color="#bbb", linewidth=0.6)
    axes[0].set_title("事件累计净 R（按出场时间排序，各币叠加，不是账户净值）", fontsize=10)
    axes[0].legend(fontsize=8)
    monthly = closed.groupby(["month", "arm"]).net_r.mean().unstack()
    x = np.arange(len(monthly))
    axes[1].bar(x - 0.2, monthly.get("v9_only"), width=0.4, color=colors["v9_only"], label="A 每笔净 R")
    axes[1].bar(x + 0.2, monthly.get("joint"), width=0.4, color=colors["joint"], label="C 每笔净 R")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([m[2:] for m in monthly.index], rotation=90, fontsize=7)
    axes[1].axhline(0, color="#bbb", linewidth=0.6)
    axes[1].set_title("按信号月份的每笔净 R：A vs C", fontsize=10)
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def sample_ids(d: dict, v9: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    j = d["joints"].loc[d["joints"].in_window.astype(bool)].sort_values(["symbol", "i"]).reset_index(drop=True)
    picks = []
    for order in ("same_bar", "spike_first", "break_first"):
        pool = j.loc[j.order == order]
        take = rng.choice(len(pool), size=min(2, len(pool)), replace=False)
        for k in sorted(take):
            row = pool.iloc[int(k)]
            picks.append({"kind": f"joint:{order}", "symbol": row.symbol, "decision_i": int(row.i),
                          "event_id": row.joint_id, "v9_i": int(row.spike_i)})
    pool = v9.loc[~v9.paired].sort_values(["symbol", "signal_i"]).reset_index(drop=True)
    for k in sorted(rng.choice(len(pool), size=min(6, len(pool)), replace=False)):
        row = pool.iloc[int(k)]
        picks.append({"kind": f"v9_not_joint:{row.pair_status}", "symbol": row.symbol, "decision_i": int(row.signal_i),
                      "event_id": row.v9_event_id, "v9_i": int(row.signal_i)})
    return pd.DataFrame(picks).assign(seed=SEED)


def check_figures(d: dict, v9: pd.DataFrame, run: Path, out: Path) -> pd.DataFrame:
    picks = sample_ids(d, v9)
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    files = study.series_files()
    meta = study.symbol_meta()
    for n, pick in enumerate(picks.itertuples(index=False), 1):
        from yoyo.evaluation.spike_v10_4_increment import bars_1h
        bars = bars_1h(files[pick.symbol])
        facts = study.v9_facts(bars, 60, meta[pick.symbol]["asset"], meta[pick.symbol]["tick"])
        frame = facts["frame"]
        t = pick.decision_i
        fig, (left, right) = plt.subplots(1, 2, figsize=(14, 4.4), gridspec_kw={"width_ratios": [1.25, 1]})
        anchor = t - 140
        if pick.kind.startswith("joint"):
            ax_i = int(d["joints"].loc[(d["joints"].symbol == pick.symbol) & (d["joints"].i == t)].iloc[0].ax)
            anchor = min(anchor, max(ax_i - 10, t - 400))
        lo = max(0, anchor)
        past = frame.iloc[lo:t + 1]
        x = np.arange(lo, t + 1)
        _candles(left, x, past)
        for col, color in (("s20", "#008F82"), ("e20", "#6fbfb4"), ("s60", "#4679C9"), ("e60", "#90aee0"),
                           ("s120", "#555"), ("e120", "#999")):
            left.plot(x, past[col], color=color, linewidth=0.7)
        left.axvline(pick.v9_i, color="#4679C9", linestyle=":", linewidth=1)
        title = f"#{n} {pick.symbol} 1h · {pick.kind} · 决策 K 收盘 {frame.index[t] + pd.Timedelta(hours=1):%Y-%m-%d %H:%M} UTC"
        trade = None
        if pick.kind.startswith("joint"):
            joint = d["joints"].loc[(d["joints"].symbol == pick.symbol) & (d["joints"].i == t)].iloc[0]
            slope = (joint.bp - joint.ap) / (joint.bx - joint.ax)
            xs = np.array([max(int(joint.ax), lo), t])
            left.plot(xs, joint.ap + slope * (xs - joint.ax), color="#222", linewidth=1.4)
            for label, bx, by in (("A", joint.ax, joint.ap), ("B", joint.bx, joint.bp), ("C", joint.cx, joint.cp)):
                if bx >= lo:
                    left.annotate(label, (bx, by), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=8)
            if joint.born_i >= lo:
                left.axvline(joint.born_i, color="#999", linestyle="-.", linewidth=0.8)
            left.axvline(joint.break_i, color="#AD7B29", linestyle="--", linewidth=1)
            left.axvline(t, color="#D34B66", linewidth=1)
            trade = d["shadows"].loc[(d["shadows"].symbol == pick.symbol) & (d["shadows"].path == "joint_event")
                                     & (d["shadows"].signal_i == t)]
            title += f"\n线 {joint.line_id.split('|L|')[1][:40]}… 三点确认 {frame.index[int(joint.born_i)]:%m-%d %H:%M}"
        else:
            snaps = json.loads((run / "streams" / pick.symbol / "v9_snapshots.json").read_text())
            snap = next((s for s in snaps if s["i"] == t), None)
            shown = 0
            for line in (snap or {}).get("available", []):
                slope = (line["bp"] - line["ap"]) / (line["bx"] - line["ax"])
                xs = np.array([max(line["ax"], lo), t])
                left.plot(xs, line["ap"] + slope * (xs - line["ax"]), color="#222", linewidth=1.0, alpha=0.8)
                shown += 1
            title += f"\n当时可用的已确认结构 {shown} 条（没有就不画线）"
            trade = d["shadows"].loc[(d["shadows"].symbol == pick.symbol) & (d["shadows"].path == "v9_event")
                                     & (d["shadows"].signal_i == t)]
        left.set_title(title, fontsize=8.5)
        left.set_xticks([])
        future_hi = min(len(frame) - 1, t + 120)
        if trade is not None and len(trade) and pd.notna(trade.iloc[0].get("exit_i")):
            future_hi = min(len(frame) - 1, int(trade.iloc[0].exit_i) + 10)
        fut = frame.iloc[t:future_hi + 1]
        fx = np.arange(t, future_hi + 1)
        _candles(right, fx, fut)
        if trade is not None and len(trade) and pd.notna(trade.iloc[0].get("entry_i")):
            tr = trade.iloc[0]
            right.plot([tr.entry_i], [tr.entry_price], marker="^", color="#008F82", markersize=8)
            right.hlines(tr.initial_stop, tr.entry_i, tr.exit_i, color="#D34B66", linestyle="--", linewidth=0.9)
            right.plot([tr.exit_i], [tr.exit_price], marker="x", color="#222", markersize=8)
            right.set_title(f"后续路径（{'联合' if pick.kind.startswith('joint') else 'V9 影子'}）：{tr.status} · "
                            f"{tr.exit_reason} · 净 {tr.net_r:+.2f}R", fontsize=8.5)
        else:
            right.set_title("后续路径：无有效交易", fontsize=8.5)
        right.set_xticks([])
        fig.tight_layout()
        fig.savefig(out / f"check_{n:02d}.png", dpi=100)
        plt.close(fig)
    return picks


def _candles(ax, x, part):
    up = (part.close >= part.open).to_numpy()
    ax.vlines(x, part.low, part.high, color="#8a8f98", linewidth=0.5)
    ax.bar(x, (part.close - part.open).abs().clip(lower=1e-12), bottom=np.minimum(part.open, part.close), width=0.7,
           color=np.where(up, "#008F82", "#D34B66"), linewidth=0)


def main(run_root: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    strict = load(run_root / "strict")
    ties = load(run_root / "right_inclusive")
    repro, diffs = reproduction(run_root / "strict", run_root / "repro_original")
    v9 = v9_ledger(strict)
    wait_table, wait_rows = waiting(strict)
    realtime, pending_outcomes = realtime_split(v9, strict["pair_attempts"])
    tables = {
        "reproduction": repro, "reproduction_diffs": diffs, "conservation": conservation(strict),
        "main_table": main_table(strict), "c_minus_a": pd.DataFrame([c_minus_a(strict, p) for p in ("full", "earlier", "later")]),
        "c_minus_a_right_inclusive": pd.DataFrame([c_minus_a(ties, p) for p in ("full", "earlier", "later")]),
        "main_table_right_inclusive": main_table(ties),
        "v9_pair_reasons": reasons(v9), "filtering": filtering(v9), "realtime_split": realtime,
        "pending_outcomes": pending_outcomes, "wait_policy": wait_policy(v9, wait_rows), "waiting": wait_table,
        "occupancy": occupancy(strict, v9), "errata": errata(ORIGINAL / "statistics/run_v1", out),
        "gap_audit": strict["gap_audit"], "cashbook": cashbook(strict),
        "joint_display": pd.DataFrame([{
            "joints_in_window": int(strict["joints"].in_window.astype(bool).sum()),
            "line_was_displayed_main_bar_before": int(strict["joints"].loc[strict["joints"].in_window.astype(bool)].line_displayed_before_joint.astype(bool).sum()),
            "median_bars_line_born_to_joint": float((strict["joints"].i - strict["joints"].born_i).median()),
            "min_bars_line_born_to_first_event": int((strict["joints"][["spike_i", "break_i"]].min(axis=1) - strict["joints"].born_i).min())}]),
    }
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    v9.to_csv(out / "v9_events_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    wait_rows.to_csv(out / "waiting_pairs.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    strict["trades"].to_csv(out / "trades_all_arms.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    strict["statuses"].to_csv(out / "candidate_statuses.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    joints = strict["joints"].copy()
    bar_open = pd.to_datetime(joints.signal_bar_open, utc=True)
    # Joints never straddle a gap (a gap clears every line), so bar arithmetic is exact.
    for name, column in (("v9_bar_open", "spike_i"), ("break_bar_open", "break_i"), ("line_confirmed_bar_open", "born_i"),
                         ("anchor_a_bar_open", "ax"), ("anchor_b_bar_open", "bx"), ("anchor_c_bar_open", "cx")):
        joints[name] = bar_open - pd.to_timedelta(joints.i - joints[column], unit="h")
    joints.to_csv(out / "joints.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    strict["breaks"].to_csv(out / "breaks.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    strict["lines"].to_csv(out / "structures.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    strict["pair_attempts"].to_csv(out / "pair_attempts.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    monthly = (strict["trades"].loc[strict["trades"].status == "closed"].groupby(["arm", "month"])
               .agg(closed=("net_r", "size"), mean_net_r=("net_r", "mean"), total_net_r=("net_r", "sum"),
                    mean_net_bp=("net_return", lambda x: x.mean() * 1e4)).reset_index())
    monthly.to_csv(out / "monthly.csv", index=False)
    overview_figure(strict, out / "overview.png")
    check_figures(strict, v9, run_root / "strict", out).to_csv(out / "check_samples.csv", index=False)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_colwidth", 80)
    for name, frame in tables.items():
        print(f"\n== {name} ==")
        print(frame.to_string(index=False, float_format=lambda v: f"{v:.4f}") if len(frame) else "(empty)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    main(args.run_root, args.out)
