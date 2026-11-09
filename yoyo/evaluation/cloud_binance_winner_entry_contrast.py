"""Describe signals at the 12H decision and test frozen single-condition screens on the native funded longs."""
import json
import operator
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import hypergeom

ROOT = Path(__file__).resolve().parents[2] / "experiments/active"
EXP = ROOT / "exp-cloud-close-binance-winner-entry-contrast-20261109-v1"
WALLET = ROOT / "exp-cloud-close-binance-matched-target-clock-20261109-v1"
FUNDING = ROOT / "exp-cloud-close-funding-pressure-20261109-v1/signal_pressure.csv.gz"
CUTOFF = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp()*1000)
OP = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


def funded(boundary):
    data = pd.read_csv(WALLET / f"hold_doubling_{boundary}_boundary/parents.csv.gz",
                       dtype={"symbol": str}, float_precision="round_trip").set_index("event_id")
    if not data.index.is_unique or not np.allclose(data.cash_pnl, data.order_cash_pnl_usdt + data.funding_cashflow_usdt):
        raise ValueError("Repeated parent or inconsistent fee/funding cash")
    if not data.nlargest(10,"cash_pnl").side.eq(1).all():
        raise ValueError("Quoted ten overall winners differ from long-only winners")
    return data[data.side.eq(1)].copy()


def stored_or_reconstructed(inputs):
    """Reuse original causal signal rows, and reconstruct only IDs absent from the direct archive."""
    from yoyo.evaluation.cloud_binance_winner_entry_features import build_features, CANONICAL_FEATURES
    folder = ROOT/"exp-signal-center-score-20261106-v1/results/symbols"
    parts = []
    for symbol, group in inputs.groupby("symbol"):
        path = folder/f"{symbol}USDT"/"signals.csv.gz"
        if not path.is_file():
            continue
        frame = pd.read_csv(path,float_precision="round_trip",dtype={"symbol":str}).set_index("signal_id")
        direct = frame.loc[frame.index.intersection(group.index)]
        if direct.empty:
            continue
        expected_ns = group.loc[direct.index,"entry_ms"]*1_000_000
        if (not direct.index.is_unique or not direct.tf.eq("12H").all() or not direct.side.eq(1).all()
                or not direct.symbol.eq(f"{symbol}USDT").all() or not np.array_equal(direct.close_ns,expected_ns)):
            raise ValueError("Original signal row differs in direction, time or contract")
        fields = direct.reindex(columns=CANONICAL_FEATURES).copy()
        fields["signal_time_ms"] = expected_ns//1_000_000
        fields["signal_feature_source_status"] = "frozen_signal_center_table"
        parts.append(fields)
    direct = pd.concat(parts)
    missing = inputs.index.difference(direct.index)
    if len(missing):
        extra = build_features(inputs.loc[missing])
        if set(extra.index) != set(missing) or not extra.index.is_unique:
            raise ValueError("Reconstruction roster differs from the missing native source rows")
        parts.append(extra.reindex(columns=direct.columns))
    result = pd.concat(parts).reindex(inputs.index)
    if not result.index.is_unique or not np.array_equal(result.signal_time_ms,inputs.entry_ms):
        raise ValueError("Combined original and reconstructed source clocks differ")
    return result


def attach_labels(features, accounts, other):
    panel = features.reindex(accounts.index).copy()
    if (not features.index.is_unique or set(features.index) != set(accounts.index)
            or set(other.index) != set(accounts.index)):
        raise ValueError("Incomplete funded population")
    for name in ("symbol","entry_ms","exit_bar_ms","cash_pnl","order_cash_pnl_usdt",
                 "funding_cashflow_usdt","net_return","exposure","notional"):
        if name in panel and not np.array_equal(panel[name],accounts[name]):
            raise ValueError(f"Feature metadata disagrees with the frozen parent: {name}")
        panel[name] = accounts[name]
    top = accounts.cash_pnl.nlargest(10).index
    panel["winner10"] = panel.index.isin(top)
    panel["loser"] = panel.cash_pnl.lt(0)
    panel["entry_quarter"] = pd.to_datetime(panel.entry_ms,unit="ms",utc=True).dt.tz_localize(None).dt.to_period("Q").astype(str)
    if set(top) != set(other.cash_pnl.nlargest(10).index):
        raise ValueError("Alternate funding boundary changes the specified labels")
    data = pd.read_csv(FUNDING, dtype={"symbol":str}).set_index("event_id").reindex(panel.index)
    if not (data.index.is_unique and data.entry_ms.equals(panel.entry_ms) and data.symbol.equals(panel.symbol)):
        raise ValueError("Pre-entry funding receipt identity or clock mismatch")
    if (data.loc[data.available,"asof_funding_time_ms"] >= data.loc[data.available,"entry_ms"]).any():
        raise ValueError("Funding observation is at or later than the signal")
    panel["recent_24h_rate_sum"] = data.recent_24h_rate_sum.where(data.available)
    panel["bar_close_location"] = panel.wick_ratio + (panel.body_atr/panel.range_atr).clip(lower=0)
    if not panel.bar_close_location.dropna().between(-1e-7,1+1e-7).all():
        raise ValueError("Within-candle close location is not in [0,1]")
    return panel


def counts(panel, target, passed, observed):
    chosen = panel[passed]
    win = chosen.index.isin(target)
    losses = panel.loser
    nt = int(panel.index.isin(target).sum())
    return dict(tails=nt, losses=int(losses.sum()), longs=len(panel), passed=len(chosen),
                kept_top=int(win.sum()), kept_losses=int(chosen.loser.sum()), actual_winners=int(chosen.cash_pnl.gt(0).sum()),
                missing_top=int(((~observed) & panel.index.isin(target)).sum()),missing_losses=int(((~observed)&losses).sum()),
                missing_longs=int((~observed).sum()), relevant_base_rate=nt/len(panel),
                precision=win.mean() if len(win) else None,
                tickers_kept=",".join(chosen.loc[win].sort_values("cash_pnl",ascending=False).symbol),
                top_share=win.sum()/nt if nt else None, loss_share=chosen.loser.sum()/losses.sum() if losses.any() else None)


def stratified_tail(frame, flag):
    """Conditional enrollment of tail cases among observed cases and losses with the same entry quarter."""
    subset = frame.loc[(frame.winner10|frame.loser) & flag.notna()].copy()
    subset["passed"] = flag.reindex(subset.index)
    prob = np.array([1.])
    obs = int((subset.winner10 & subset.passed.astype(bool)).sum())
    expectation = 0.
    for _, group in subset.groupby("entry_quarter"):
        n, wins, selected = len(group),int(group.winner10.sum()),int(group.passed.sum())
        if wins:
            expectation += wins*selected/n
            prob = np.convolve(prob,hypergeom.pmf(np.arange(wins+1),n,selected,wins))
    return float(np.clip(prob[obs:].sum(),0,1)),expectation


def evaluate(panel, rules):
    early = panel[(panel.entry_ms < CUTOFF) & ((panel.exit_bar_ms+3600000) <= CUTOFF)]
    late = panel[panel.entry_ms >= CUTOFF]
    global_top = panel.index[panel.winner10]
    train_top = early.cash_pnl.nlargest(int(np.ceil(.015*len(early)))).index
    screens, selection = [], []
    for name, field, cmp, threshold in rules:
        x = panel[field].replace([np.inf,-np.inf],np.nan)
        passed = OP[cmp](x,threshold) & x.notna()
        all_count = counts(panel,global_top,passed,x.notna())
        # Unique loss parents, even if a pair of winners share a decision clock.
        losses = panel[panel.loser]
        twins = losses[losses.entry_ms.isin(panel.loc[panel.winner10,"entry_ms"])]
        matched_cases = panel[panel.winner10 & panel.entry_ms.isin(twins.entry_ms)]
        p,expect = stratified_tail(panel,passed.where(x.notna()))
        screens.append(dict(rule=name,feature=field,comparison=cmp,threshold=threshold,**all_count,
                            quarter_conditional_p=p,quarter_expected_hits=expect,
                            same_close_tails=len(matched_cases),same_close_tails_selected=int(passed[matched_cases.index].sum()),
                            same_close_losses=len(twins),same_close_losses_selected=int(passed[twins.index].sum())))
        for part,tab,target in [("early_mature",early,train_top),("later",late,global_top)]:
            score = counts(tab,target,passed.loc[tab.index],x.notna().loc[tab.index])
            selection.append(dict(rule=name,period=part,**score))
    screens = pd.DataFrame(screens)
    ps = screens.quarter_conditional_p.sort_values()
    adjusted = (ps * np.arange(len(ps),0,-1)).cummax().clip(upper=1)
    screens["holm_p"] = adjusted.reindex(screens.index)
    windows = pd.DataFrame(selection)
    eligible = windows[(windows.period=="early_mature") & (windows.top_share>=.5) & (windows.passed>=20)].copy()
    eligible["score"] = eligible.top_share-eligible.loss_share
    selected = str(eligible.sort_values("score",ascending=False,kind="stable").iloc[0].rule) if len(eligible) else None
    return screens, windows, dict(cutoff_utc="2025-01-01", maturing_train_longs=len(early), later_longs=len(late),
        purged_open_earlier_positions=len(panel)-len(early)-len(late), training_tail=len(train_top),
        training_ids=list(train_top), selected=selected,
        rows=json.loads(windows[windows.rule.eq(selected)].to_json(orient="records")))


def describe(panel, columns):
    """Summaries for all losers, all other positive longs, and exact concurrent loss controls per top10 case."""
    cases = panel[panel.winner10].sort_values("cash_pnl",ascending=False)
    losers = panel[panel.loser]
    ordinary = panel[panel.cash_pnl.gt(0) & ~panel.winner10]
    summary, paired = [], []
    for key in columns:
        for name, group in [("top10",cases),("losers",losers),("other_winners",ordinary)]:
            x = group[key].replace([np.inf,-np.inf],np.nan).dropna()
            summary.append(dict(feature=key,group=name,n=len(x),median=x.median(),q25=x.quantile(.25),q75=x.quantile(.75)))
        for event, row in cases.iterrows():
            contemporaries = losers[losers.entry_ms.eq(row.entry_ms)]
            x = contemporaries[key].replace([np.inf,-np.inf],np.nan).dropna()
            paired.append(dict(event_id=event,symbol=row.symbol,feature=key,losers_at_same_close=len(contemporaries),
                               observed_losers=len(x),same_close_loss_median=x.median(),value=row[key]))
    return pd.DataFrame(summary),pd.DataFrame(paired)


def shared_observations(panel):
    """After seeing top10: unity volume/range thresholds and first touch, separately and jointly.

    Volume uses the signal bar / median previous 20 completed 12H volumes; range uses
    its (H-L) / current Wilder ATR14. A missing required input remains in the denominator.
    """
    top = panel.index[panel.winner10]
    tests = {
        "above_median_volume": {"log_vol_ratio": panel.log_vol_ratio.gt(0)},
        "range_above_atr": {"range_atr": panel.range_atr.gt(1)},
        "first_wick_of_week": {"prior_touches": panel.prior_touches.eq(0)},
    }
    tests["all_three_observed"] = {k:v for part in list(tests.values()) for k,v in part.items()}
    losses = panel[panel.loser]
    contemporaries = losses[losses.entry_ms.isin(panel.loc[top,"entry_ms"])]
    cases = panel[panel.winner10 & panel.entry_ms.isin(contemporaries.entry_ms)]
    out = []
    for name, factors in tests.items():
        columns = list(factors)
        observed = panel[columns].apply(np.isfinite).all(axis=1)
        passed = pd.DataFrame(factors).all(axis=1) & observed
        for part, tab in [("all",panel),("before_2025",panel[panel.entry_ms<CUTOFF]),("later",panel[panel.entry_ms>=CUTOFF])]:
            row = counts(tab,top,passed.loc[tab.index],observed.loc[tab.index])
            out.append(dict(rule=name,period=part,**row))
        out[-3].update(same_close_tails=len(cases),same_close_tails_selected=int(passed[cases.index].sum()),
                       same_close_losses=len(contemporaries),same_close_losses_selected=int(passed[contemporaries.index].sum()),
                       same_close_losses_missing=int((~observed[contemporaries.index]).sum()))
    return pd.DataFrame(out)


def main():
    if (EXP/"summary.json").exists():
        raise FileExistsError("Completed comparison")
    config = json.loads((EXP/"config.json").read_text())
    original = funded("include")
    alternate = funded("exclude")
    # Do not pass outcomes, exposure allocations, or holding length into the feature extractor.
    inputs = original[["symbol","entry_ms","side"]].copy()
    features = stored_or_reconstructed(inputs)
    panel = attach_labels(features,original,alternate)
    rules = config["univariate_rules"]
    screens, windows, temporal = evaluate(panel,rules)
    columns = list(dict.fromkeys([r[1] for r in rules] + [k for k in (
        "pen_pct","close_pos","stop_dist_pct","rr_close","ret_24h","age_days","log_qv_7d","btc_ret_24h","hours_since_monday","notional","exposure") if k in panel]))
    desc, matched = describe(panel,columns)
    shared_observations(panel).to_csv(EXP/"posthoc_common_traits.csv",index=False)
    ten = panel[panel.winner10].sort_values("cash_pnl",ascending=False)
    panel.to_csv(EXP/"entry_rows.csv.gz")
    ten.to_csv(EXP/"top10.csv")
    desc.to_csv(EXP/"feature_distributions.csv",index=False)
    matched.to_csv(EXP/"same_close_loss_controls.csv",index=False)
    screens.to_csv(EXP/"fixed_rules.csv",index=False)
    windows.to_csv(EXP/"time_split.csv",index=False)
    if not np.allclose(panel.cash_pnl.div(panel.notional),panel.net_return):
        raise ValueError("Alternative return-label units changed")
    ranked = {name: list(tab.nlargest(10).index) for name,tab in dict(alternative_boundary=alternate.cash_pnl,
              trade_cash_only=panel.order_cash_pnl_usdt,return_on_entry_notional=panel.net_return).items()}
    summary = dict(longs=len(panel),winners=int(panel.cash_pnl.gt(0).sum()),losses=int(panel.loser.sum()),
           other_winners=int((panel.cash_pnl.gt(0) & ~panel.winner10).sum()),scratch=int(panel.cash_pnl.eq(0).sum()),
           ten=list(ten.index),alternate_ten=ranked,overlap={k:len(set(v)&set(ten.index)) for k,v in ranked.items()},
           timestamp_match_cases=int(ten.entry_ms.isin(panel.loc[panel.loser,"entry_ms"]).sum()),
           causal_source_counts=panel.signal_feature_source_status.value_counts(dropna=False).to_dict(),
           tests=len(rules),holm_significant=int(screens.holm_p.lt(.05).sum()),chronology=temporal)
    (EXP/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print(json.dumps(summary,ensure_ascii=False,allow_nan=False),flush=True)


if __name__ == "__main__":
    main()
