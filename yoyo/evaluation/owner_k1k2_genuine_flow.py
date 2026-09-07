"""V36 same-venue owner morphology and one frozen genuine taker-flow gate.

Candidate features: completed one-hour OHLCV only, delegated to the pure owner
source module. Gate: quote amounts of completed five-minute bars in [K1 close,
K2 close), with no subsequent price. Matching is outcome-free and uses its own
completed-hour state. Future raw5 OHLC enter ONLY independent execution labels.
Sources: owner-causal-v2 Pine f_findBestK1, V4 native5 research policy,
https://github.com/binance/binance-public-data#futures and pandas2.3.3
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
No downloads, optimization, production mutations, or newer market reads.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features as native_features, resample_complete
from yoyo.data.k1k2_owner_source import add_features, detect_candidates
from yoyo.data.k1k2_genuine_flow_alignment import AMOUNTS, align_windows, clocks
from yoyo.evaluation.hourly_impulse_k2_matching import build_matching_frame, assign_controls
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events, single_position_ledger

ROOT = Path(__file__).resolve().parents[2]
REL = Path("experiments/active/exp-btcusdtp-owner-k1k2-genuine-flow-20260907-v36")
HERE = ROOT / REL
FOLDS = [(f"{y}H{h}", f"{y}-{'01' if h == 1 else '07'}-01T00:00:00Z",
          f"{y if h == 1 else y+1}-{'07' if h == 1 else '01'}-01T00:00:00Z")
         for y in [2023, 2024] for h in [1, 2]]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    """JSON-safe receipt conversion, preserving unknown as null, not zero."""
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)): return value.item()
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA: return None
    if isinstance(value, pd.Timestamp): return value.isoformat()
    return value


def write_json(path, value):
    with Path(path).open("x") as out:
        json.dump(clean(value), out, indent=2, ensure_ascii=False, allow_nan=False)
        out.write("\n")


def checked():
    config = json.loads((HERE / "config.json").read_text())
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = config["builder_paths"] + [str(REL / p) for p in ["config.json", "PROJECT_PLAN.md"]]
    for name in paths:
        if subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError("Commit exact builder before materializing inputs: " + name)
    path = ROOT / config["flow_summary"]
    if sha(path) != config["flow_summary_sha256"]:
        raise ValueError("V34 summary drift")
    manifest = json.loads(path.read_text())
    months = [str(p) for p in pd.period_range("2023-01", "2024-12", freq="M")]
    if [r["month"] for r in manifest["monthly"]] != months or manifest["status"] != "complete":
        raise ValueError("Only exact authorized24months permitted")
    for r in manifest["monthly"]:
        expected = "data/genuine_flow_coverage_v34/bars/BTCUSDT-5m-" + r["month"] + "-flow.csv"
        if r["output_path"] != expected or sha(ROOT / expected) != r["output_sha256"]:
            raise ValueError("Frozen same-venue monthly price/flow bytes drift")
    return commit, config, manifest


def load_month(path, receipt):
    """Timestamp-only preflight precedes numeric materialization; no mixed-era file."""
    times = pd.read_csv(path, usecols=["open_time"])
    t = clocks(times.open_time, "open_time")
    start = pd.Timestamp(receipt["month"] + "-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    if start < pd.Timestamp("2023-01-01", tz="UTC") or end > pd.Timestamp("2025-01-01", tz="UTC"):
        raise ValueError("Unauthorized month")
    expected = pd.date_range(start, end, freq="5min", inclusive="left")
    if not pd.DatetimeIndex(t).equals(expected):
        raise ValueError("Invalid monthly timestamp prefix: do not load prices")
    columns = BAR_COLUMNS + AMOUNTS + ["earliest_available_at", "source_exchange", "symbol"]
    frame = pd.read_csv(path, usecols=columns, float_precision="round_trip")
    if not frame.source_exchange.eq("binance_usdm").all() or not frame.symbol.eq("BTCUSDT").all():
        raise ValueError("Different venue/symbol")
    frame.open_time = t
    frame.earliest_available_at = clocks(frame.earliest_available_at, "earliest_available_at")
    if not frame.earliest_available_at.eq(t + pd.Timedelta(minutes=5)).all():
        raise ValueError("Invalid theoretical availability")
    return frame


def windows_for(events):
    """One post-K1 window per already chosen candidate, never choose K1 by flow."""
    e = events.copy()
    end = clocks(e.decision_time, "decision_time")
    gap = pd.to_numeric(e.gap_bars, errors="raise")
    if not gap.isin(range(2, 9)).all(): raise ValueError("Original source gap2..8 required")
    start = end - pd.to_timedelta(gap, unit="h")
    if "k1_decision_time" in e and not clocks(e.k1_decision_time, "k1_decision_time").eq(start).all():
        raise ValueError("Selected K1/window disagreement")
    return pd.DataFrame(dict(window_id=e.event_id + ":post_k1_through_k2", event_id=e.event_id,
        window_kind="post_k1_through_k2", window_start=start, window_end=end,
        decision_time=end, direction=e.direction))


def gate_events(flow, events):
    f = align_windows(flow, windows_for(events))
    selected = f[["event_id", "status", "flow_defined", "directional_imbalance",
        "quote_volume_sum", "delta_quote_volume_sum", "expected_bars", "available_bars",
        "zero_volume_bars", "max_source_available_at"]].rename(columns={"status": "flow_status"})
    out = events.merge(selected, on="event_id", how="left", validate="one_to_one", sort=False)
    out["flow_pass"] = out.flow_defined & out.directional_imbalance.gt(0)
    return out, f


def contribution(trades, gated=False, gross=False):
    """Independent-event original-notional return; gate-off0, unknown path NaN."""
    col = "gross_return" if gross else "net_return"
    x = pd.to_numeric(trades[col], errors="coerce").where(trades.closed)
    x = x.mask(trades.outcome.str.startswith("entry_"), 0.0)
    if gated: x = x.mask(~trades.flow_pass, 0.0)
    return x


def paired_contrasts(cases, controls, assignments):
    """Keep all three original controls. Any unknown baseline path stays unknown."""
    ids = assignments.loc[assignments.match_status.eq("matched"), "event_id"]
    c = cases.set_index("event_id")
    rows = []
    for event_id in ids:
        row = c.loc[[event_id]]
        ctrl = controls.loc[controls.parent_event_id.eq(event_id)]
        if len(ctrl) != 3 or ctrl.event_id.nunique() != 3:
            raise ValueError("Each original matched case must keep all3controls")
        out = dict(event_id=event_id, decision_time=row.iloc[0].decision_time,
                   fold=row.iloc[0].fold, controls=3)
        known = (row.flow_defined.all() and ctrl.flow_defined.all()
                 and contribution(row).notna().all() and contribution(ctrl).notna().all())
        out["complete_pair"] = bool(known)
        for label, gross in [("net", False), ("gross", True)]:
            base_c, gate_c = contribution(row, gross=gross).iloc[0], contribution(row, True, gross).iloc[0]
            base_r, gate_r = contribution(ctrl, gross=gross), contribution(ctrl, True, gross)
            base = base_c - base_r.mean() if known else np.nan
            gate = gate_c - gate_r.mean() if known else np.nan
            out.update({"baseline_excess_" + label: base, "gated_excess_" + label: gate,
                        "incremental_excess_" + label: gate - base})
        out["incremental_excess_saved_cost"] = out["incremental_excess_net"] - out["incremental_excess_gross"]
        rows.append(out)
    return pd.DataFrame(rows, columns=["event_id", "decision_time", "fold", "controls", "complete_pair",
        "baseline_excess_net", "gated_excess_net", "incremental_excess_net",
        "baseline_excess_gross", "gated_excess_gross", "incremental_excess_gross", "incremental_excess_saved_cost"])


def month_inference(frame, value, draws=9999, seed=20260906):
    """All24calendar months; intact same-month sets, six-month within-fold resampling.

    No IID trade test. Sign-flip requires symmetric independent cluster errors;
    bootstrap assumes exchangeable months within a halfyear. Both approximations
    are explicitly exploratory with reused data and cross-month outcome overlap.
    """
    f = frame.copy()
    f["month"] = pd.to_datetime(f.decision_time, utc=True).dt.strftime("%Y-%m")
    if f[value].isna().any(): return dict(status="unknown_pairs", n=len(f))
    months = [str(x) for x in pd.period_range("2023-01", "2024-12", freq="M")]
    group = f.groupby("month")[value].agg(["sum", "count"]).reindex(months, fill_value=0)
    sums, counts = group["sum"].to_numpy(float), group["count"].to_numpy(float)
    if counts.sum() == 0: return dict(status="empty", n=0)
    rng = np.random.default_rng(seed)
    sign = rng.choice([-1.0, 1.0], size=(draws, 24))
    # Elementwise reduction avoids platform BLAS warning behavior for tiny
    # vectors; it also makes the exact finite sign-flip statistic inspectable.
    flipped = (sign * sums[None, :]).sum(axis=1)
    if not np.isfinite(flipped).all(): raise ValueError("Nonfinite null statistic")
    p = (1 + (flipped >= sums.sum()).sum()) / (draws + 1)
    indexes = np.concatenate([rng.integers(a, a+6, size=(draws, 6)) for a in [0, 6, 12, 18]], axis=1)
    den = counts[indexes].sum(axis=1)
    boot = sums[indexes].sum(axis=1)[den > 0] / den[den > 0]
    values = f[value].to_numpy(float)
    return dict(status="exploratory", n=len(f), calendar_months=24, active_months=int((counts > 0).sum()),
        mean_bp=values.mean()*1e4, median_bp=np.median(values)*1e4,
        sd_bp=values.std(ddof=1)*1e4 if len(values)>1 else None,
        ci95_bp=(np.quantile(boot, [.025, .975])*1e4).tolist(), p_one_sided=float(p),
        monthly_lag1_autocorrelation=pd.Series(sums).autocorr(1),
        max_abs_month_share=float(np.abs(sums).max()/np.abs(sums).sum()) if np.abs(sums).sum() else 0,
        draws=draws, seed=seed)


def describe(trades):
    finite = trades.loc[trades.closed & trades.net_return.notna()]
    x = finite.net_return.astype(float)
    pos, neg = x.loc[x > 0].sum(), -x.loc[x < 0].sum()
    out = dict(requests=len(trades), closed=len(finite), rejected=int(trades.outcome.str.startswith("entry_").sum()),
        unresolved=int((~trades.closed & ~trades.outcome.str.startswith("entry_")).sum()),
        mean_net_bp=x.mean()*1e4, mean_gross_bp=finite.gross_return.mean()*1e4,
        median_net_bp=x.median()*1e4, sd_net_bp=x.std()*1e4, win_rate=x.gt(0).mean(),
        profit_factor=pos/neg if neg>0 else None, sum_net_bp=x.sum()*1e4,
        median_hold_minutes=finite.hold_minutes.median(),
        outcomes=trades.outcome.value_counts().to_dict(),
        fee_erased=int((finite.gross_return.gt(0) & finite.net_return.le(0)).sum()))
    return clean(out)


def rank_diagnostics(trades, score):
    f = trades.loc[trades.closed & trades[score].notna()].copy()
    if f.empty: return dict(n=0, auc=None, top_decile_n=0)
    y = f.net_return.gt(0); n1, n0 = int(y.sum()), int((~y).sum())
    auc = (f[score].rank().loc[y].sum()-n1*(n1+1)/2)/(n1*n0) if n1 and n0 else None
    top = f.sort_values([score, "event_id"], ascending=[False, True]).head(max(1, int(np.ceil(len(f)/10))))
    return clean(dict(n=len(f), auc=auc, top_decile_n=len(top),
        top_decile_gross_bp=top.gross_return.mean()*1e4, top_decile_net_bp=top.net_return.mean()*1e4,
        top_decile_win_rate=top.net_return.gt(0).mean(), interpretation="reused development descriptive, not validation"))


def run():
    commit, config, manifest = checked()
    output = ROOT / config["output_dir"]
    if output.exists() or (HERE / "summary.json").exists():
        raise ValueError("One-shot evidence exists; do not overwrite")
    flow = pd.concat([load_month(ROOT / r["output_path"], r) for r in manifest["monthly"]], ignore_index=True)
    raw = resample_complete(flow[BAR_COLUMNS], 5)
    hours = resample_complete(raw, 60)
    featured = add_features(hours)
    all_candidates = detect_candidates(featured, venue=config["venue"], symbol=config["symbol"])
    prefix_receipts = []
    for cutoff in pd.date_range("2023-04-01", "2025-01-01", freq="QS", tz="UTC"):
        prefix = detect_candidates(add_features(hours.loc[hours.open_time + pd.Timedelta(hours=1) <= cutoff]),
                                   venue=config["venue"], symbol=config["symbol"])
        wanted = all_candidates.loc[all_candidates.decision_time.le(cutoff)].reset_index(drop=True)
        pd.testing.assert_frame_equal(prefix, wanted)
        prefix_receipts.append(dict(cutoff=cutoff, candidates=len(prefix), exact=True))
    native5 = native_features(raw, "SMA", 40)
    native_hour = native_features(hours, "SMA", 40)
    np.testing.assert_allclose(featured.sma40_hl2, native_hour.ma, rtol=0, atol=0, equal_nan=True)
    np.testing.assert_allclose(featured.atr14, native_hour.atr, rtol=0, atol=0, equal_nan=True)
    all_candidates["signal_time"] = all_candidates.k2_time
    matchframe = build_matching_frame(raw, native_hour, native5, all_candidates)
    cases, controls, assignments, matchinfo, excluded = [], [], [], [], []
    for fold, start, end in FOLDS:
        within = all_candidates.decision_time.ge(pd.Timestamp(start)) & all_candidates.decision_time.lt(pd.Timestamp(end))
        current = all_candidates.loc[within].copy(); current["fold"] = fold
        cut = current.decision_time < pd.Timestamp(end)-pd.Timedelta(hours=72)
        excluded.append(current.loc[~cut].assign(exclusion_reason="clock_only_fold_last72h"))
        current = current.loc[cut].copy()
        ctrl, assignment, info = assign_controls(current, matchframe, count=3, seed=config["seed"], end_exclusive=end)
        ctrl["gap_bars"] = ctrl.parent_event_id.map(current.set_index("event_id").gap_bars).astype(int)
        cases.append(current); controls.append(ctrl); assignments.append(assignment); matchinfo.append(dict(fold=fold, **info))
    case = pd.concat(cases, ignore_index=True); control = pd.concat(controls, ignore_index=True)
    assignment = pd.concat(assignments, ignore_index=True)
    case, caseflow = gate_events(flow, case); control, controlflow = gate_events(flow, control)
    flow_prefix = []
    for cohort, events, expected in [("case",case,caseflow),("control",control,controlflow)]:
        for cutoff in pd.date_range("2023-04-01", "2025-01-01", freq="QS", tz="UTC"):
            selected = events.decision_time.le(cutoff)
            _, got = gate_events(flow.loc[flow.earliest_available_at.le(cutoff)], events.loc[selected])
            pd.testing.assert_frame_equal(got, expected.loc[selected].reset_index(drop=True))
            flow_prefix.append(dict(cohort=cohort, cutoff=cutoff, windows=int(selected.sum()), exact=True))
    output.mkdir(parents=True)
    files = {}
    def save(name, frame):
        path = output/(name+".csv")
        if path.exists(): raise ValueError("No evidence overwrite")
        frame.to_csv(path, index=False, float_format="%.17g")
        files[str(path.relative_to(ROOT))] = dict(sha256=sha(path), rows=len(frame), columns=list(frame))
    for name, frame in [("all_candidates",all_candidates),("clock_excluded",pd.concat(excluded)),
        ("case_requests",case),("control_requests",control),("assignments",assignment),
        ("case_flow",caseflow),("control_flow",controlflow)]: save(name,frame)
    write_json(HERE/"pre_outcome_receipt.json", dict(source_commit=commit,
        generated_at=pd.Timestamp.now(tz="UTC"), files=files.copy(), matching=matchinfo,
        candidate_prefix=prefix_receipts, flow_prefix=flow_prefix, outcomes_materialized=False))
    paths = {}
    for cohort, requests in [("case",case),("control",control)]:
        paths[cohort] = pd.concat([simulate_events(raw, native5, requests.loc[requests.fold.eq(fold)],
            config["policy"], end_exclusive=end) for fold,_,end in FOLDS], ignore_index=True)
        t = paths[cohort]
        t["failure_class"] = np.select([~t.closed, t.net_return.gt(0), t.gross_return.gt(0), t.gross_return.eq(0)],
            ["unclosed_or_rejected", "net_winner", "fee_erased_gross_gain", "flat_price_cost_loss"], default="price_loss")
        t["peak_gross_bp_lower_bound"] = t.max_favourable_r*t.risk_pct*1e4
        t["gave_back_fee_covering_peak"] = t.closed & t.net_return.le(0) & t.peak_gross_bp_lower_bound.gt(20)
        save(cohort+"_trades",t)
    case_t, control_t = paths["case"], paths["control"]
    pair = paired_contrasts(case_t, control_t, assignment); save("paired_contrasts",pair)
    foldrows, ledgers = [], {}
    for arm, kept in [("baseline",case_t),("flow_positive",case_t.loc[case_t.flow_pass])]:
        ledger = single_position_ledger(kept); ledgers[arm] = ledger; save(arm+"_single_position",ledger)
        for fold,_,_ in FOLDS:
            foldrows.append(dict(arm=arm,fold=fold, **describe(kept.loc[kept.fold.eq(fold)])))
    save("fold_metrics",pd.DataFrame(foldrows))
    baseline = contribution(case_t); gated = contribution(case_t,True)
    basegross = contribution(case_t,gross=True); gategross = contribution(case_t,True,True)
    inf = month_inference(pair,"incremental_excess_net",config["draws"],config["seed"])
    retained = case_t.loc[case_t.flow_pass]
    closedret = retained.loc[retained.closed]
    active = pd.to_datetime(closedret.decision_time,utc=True).dt.strftime("%Y-%m")
    coverage = len(pair)/len(case_t) if len(case_t) else 0
    goodfold = [r for r in foldrows if r["arm"]=="flow_positive"]
    gates = dict(min80=len(closedret)>=80, min12_per_fold=all(r["closed"]>=12 for r in goodfold),
        four_positive_folds=all(r["mean_net_bp"] is not None and r["mean_net_bp"]>0 for r in goodfold),
        positive_net=bool(closedret.net_return.mean()>0), pf1_1=(describe(retained)["profit_factor"] or 0)>=1.1,
        min12_active_months=active.nunique()>=12,
        min3_months_per_fold=all(pd.to_datetime(closedret.loc[closedret.fold.eq(f)].decision_time,utc=True).dt.strftime("%Y-%m").nunique()>=3 for f,_,_ in FOLDS),
        matched_coverage90=coverage>=.9, all_pairs_known=bool(pair.complete_pair.all()) and len(pair)>0,
        primary_p01=inf.get("p_one_sided",1)<.01, primary_ci_positive=inf.get("ci95_bp",[-np.inf])[0]>0,
        positive_absolute_excess=bool(pair.gated_excess_net.mean()>0))
    summary = dict(status="research_pass_not_deployable" if all(gates.values()) else "research_gate_failed",
        generated_at=pd.Timestamp.now(tz="UTC"), source_commit=commit, config_sha256=sha(HERE/"config.json"),
        source_bars=len(raw), source_hours=len(hours), all_candidates=len(all_candidates),
        unique_k1=int(all_candidates[["k1_time","direction"]].drop_duplicates().shape[0]),
        fold_eligible=len(case), controls=len(control), matched_cases=len(pair), matched_coverage=coverage,
        matching=matchinfo, flow_status=case.flow_status.value_counts().to_dict(),
        baseline=describe(case_t), flow_positive=describe(retained), flow_removed=describe(case_t.loc[~case_t.flow_pass]),
        matched_baseline_controls=describe(control_t), original_unit_baseline_mean_bp=baseline.mean()*1e4,
        original_unit_gated_mean_bp=gated.mean()*1e4, original_unit_increment_net_bp=(gated-baseline).mean()*1e4,
        original_unit_increment_gross_bp=(gategross-basegross).mean()*1e4,
        original_unit_saved_cost_bp=((gated-baseline)-(gategross-basegross)).mean()*1e4,
        primary_matched_increment=inf,
        matched_baseline_excess=month_inference(pair,"baseline_excess_net",config["draws"],config["seed"]),
        matched_gated_excess=month_inference(pair,"gated_excess_net",config["draws"],config["seed"]),
        single_position={arm:describe(led.loc[led.portfolio_selected]) for arm,led in ledgers.items()},
        fold_metrics=foldrows, gates=gates, files=files, candidate_prefix=prefix_receipts, flow_prefix=flow_prefix,
        descriptive_rank={s:rank_diagnostics(case_t,s) for s in ["directional_imbalance","body_ratio"]},
        holdout_evaluated=False, live_delivery_verified=False, funding_modelled=False,
        training_eligible=False, production_eligible=False)
    if checked() != (commit,config,manifest): raise ValueError("Frozen inputs changed during replay")
    write_json(HERE/"summary.json",summary)
    print(json.dumps(clean({k:v for k,v in summary.items() if k not in {"files","candidate_prefix","flow_prefix","matching"}}),indent=2))


if __name__ == "__main__":
    run()
