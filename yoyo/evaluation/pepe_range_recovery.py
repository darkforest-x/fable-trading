"""Descriptive PEPE holding MFE and post-stop recovery from frozen V2.8 paths.

Inputs: frozen entry/exit/risk ledger plus canonical 5m OHLC. Holding excursions
use only entry+1 through exit, with stop-bar favorable OHLC excluded. Post-exit
labels deliberately use future OHLC within fixed 15m/30m/1h/4h/24h horizons and
the original segment boundary; they MUST NOT be used as entry-time features.
R always uses the original risk, never a denominator recomputed after a stop.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_data import EXP


def excursions(bars, row):
    """Reconstruct conservative holding MFE and a stop-bar-inclusive upper bound."""
    a, z, side = int(row.entry_i), int(row.exit_i), int(row.side)
    favorable = 1 if side == 1 else 2
    stop_exit = int(row.reason) in (1, 2)
    last = z if stop_exit else z + 1
    values = side * (bars[a+1:last, favorable] - row.entry) / row.risk
    peak = max(0., float(values.max())) if len(values) else 0.
    full = side * (bars[a+1:z+1, favorable] - row.entry) / row.risk
    upper = max(peak, float(full.max())) if len(full) else peak
    return peak, upper


def observe(bars, row, minutes, thresholds):
    """Label a fully observed post-stop horizon; never consume its hit-bar wick."""
    z, side = int(row.exit_i), int(row.side)
    count = minutes // 5
    end = min(int(row.end_i), len(bars)-1)
    full = z + count <= end
    ans = dict(pair_id=int(row.pair_id), population=row.population,
               segment=int(row.segment), reason=int(row.reason),
               minutes=minutes, complete=full)
    if not full:
        return ans
    after = bars[z+1:z+count+1]
    fav_col, bad_col = (1, 2) if side == 1 else (2, 1)
    fav = side * (after[:, fav_col]-row.entry)/row.risk
    close = side * (after[:, 3]-row.entry)/row.risk
    bad = side * (after[:, bad_col]-row.entry)/row.risk
    # A price tolerance far below a tick, converted to original-risk units.
    eps = 1e-16/row.risk
    ans.update(post_max_r=float(fav.max()), post_min_r=float(bad.min()),
               end_r=float(close[-1]))
    for threshold in thresholds:
        touch = np.flatnonzero(fav >= threshold-eps)
        closed = np.flatnonzero(close >= threshold-eps)
        ans[f"touch_{threshold}r"] = bool(len(touch))
        ans[f"close_{threshold}r"] = bool(len(closed))
        ans[f"first_touch_{threshold}r_minutes"] = int((touch[0]+1)*5) if len(touch) else None
    touch = np.flatnonzero(fav >= -eps)
    ans["adverse_bound_before_entry_reclaim_r"] = None
    if len(touch):
        # Include both ambiguous endpoint bars, giving a conservative bound.
        path = bars[z:z+int(touch[0])+2, bad_col]
        ans["adverse_bound_before_entry_reclaim_r"] = float(
            max(0., -np.min(side*(path-row.entry)/row.risk)))
    return ans


def counts(frame, columns):
    return {key: int(frame[key].eq(True).sum())
            for key in columns}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n")


def audit_windows(raw, rows, rec, thresholds):
    """Independent rolling-aggregate oracle for every complete future window."""
    sources = rows.set_index(["population", "pair_id"])
    checked = 0
    for minutes, group in rec.groupby("minutes"):
        width = int(minutes)//5
        rolling = dict(high=raw.high.rolling(width).max(),
                       low=raw.low.rolling(width).min(),
                       high_close=raw.close.rolling(width).max(),
                       low_close=raw.close.rolling(width).min())
        for window in group.itertuples():
            row = sources.loc[(window.population, window.pair_id)]
            z = int(row.exit_i)
            last = z + width
            assert bool(window.complete) == (last <= min(int(row.end_i), len(raw)-1))
            if not window.complete:
                continue
            favorable = rolling["high" if row.side == 1 else "low"].iloc[last]
            adverse = rolling["low" if row.side == 1 else "high"].iloc[last]
            closed = rolling["high_close" if row.side == 1 else "low_close"].iloc[last]
            assert abs(window.post_max_r-row.side*(favorable-row.entry)/row.risk) < 1e-7
            assert abs(window.post_min_r-row.side*(adverse-row.entry)/row.risk) < 1e-7
            for threshold in thresholds:
                for method, extreme in (("touch", favorable), ("close", closed)):
                    assert getattr(window, f"{method}_{threshold}r") == (
                        row.side*(extreme-row.entry) >= threshold*row.risk-1e-16)
                when = getattr(window, f"first_touch_{threshold}r_minutes")
                if pd.notna(when):
                    idx = z + int(when)//5
                    key = "high" if row.side == 1 else "low"
                    price = row.entry + row.side*threshold*row.risk
                    assert row.side*(raw[key].iloc[idx]-price) >= -1e-16
                    previous = raw[key].iloc[z+1:idx]
                    assert not (row.side*(previous-price) >= -1e-16).any()
            checked += 1
    return checked


def main():
    cfg = json.loads((EXP/"recovery_config.json").read_text())
    out = EXP/"results"/"recovery_v1"
    out.mkdir(exist_ok=True)
    rows = pd.read_csv(EXP/"results"/f"trades_{cfg['baseline']}.csv",
                       float_precision="round_trip")
    raw = pd.read_csv(EXP/"inputs"/"okx_pepe_5m.csv.gz", float_precision="round_trip")
    assert (raw.ts.diff().dropna() == 300000).all()
    bars = raw[["open", "high", "low", "close"]].to_numpy()
    close_times = pd.to_datetime(raw.ts, unit="ms", utc=True)+pd.Timedelta(minutes=5)
    assert pd.to_datetime(rows.entry_time).reset_index(drop=True).equals(
        close_times.iloc[rows.entry_i.to_numpy(dtype=int)].reset_index(drop=True))
    holding = []
    recovery = []
    for row in rows.itertuples():
        peak, upper = excursions(bars, row)
        assert abs(peak-row.peak_r) < 1e-7, (row.pair_id, row.population, peak, row.peak_r)
        holding.append(dict(holding_peak_r=peak, holding_peak_upper_r=upper))
        if int(row.reason) in (1, 2):
            for minutes in cfg["windows_minutes"]:
                recovery.append(observe(bars, row, minutes, cfg["thresholds_r"]))
    rows = pd.concat([rows.reset_index(drop=True), pd.DataFrame(holding)], axis=1)
    rows["entry_shanghai"] = pd.to_datetime(rows.entry_time).dt.tz_convert("Asia/Shanghai")
    rows["exit_shanghai"] = pd.to_datetime(rows.exit_time).dt.tz_convert("Asia/Shanghai")
    rows["stop_bar_close_shanghai"] = close_times.iloc[
        rows.exit_i.to_numpy(dtype=int)].dt.tz_convert("Asia/Shanghai").to_numpy()
    rec = pd.DataFrame(recovery)
    checked_windows = audit_windows(raw, rows, rec, cfg["thresholds_r"])
    boolean_cols = [f"{method}_{r}r" for method in ("touch", "close") for r in cfg["thresholds_r"]]
    summaries, holding_summaries = [], []
    for population in ("signal", "random"):
        for segment in (-1, 0, 1, 2):
            sample = rows[rows.population == population]
            if segment >= 0:
                sample = sample[sample.segment == segment]
            for threshold in (1, 3, 5, 10, 20):
                hit = sample[sample.holding_peak_r >= threshold-1e-7]
                holding_summaries.append(dict(population=population, segment=segment,
                    threshold=threshold, n=len(sample), reached=len(hit),
                    could_reach_in_ambiguous_stop_bar=int(
                        ((sample.holding_peak_r < threshold-1e-7) &
                         (sample.holding_peak_upper_r >= threshold-1e-7)).sum()),
                    ended_at_or_above=int((hit.gross_r >= threshold-1e-7).sum()),
                    ended_net_positive=int((hit.net_r > 0).sum()),
                    ended_initial_stop=int((hit.reason == 1).sum()),
                    median_peak=float(hit.holding_peak_r.median()) if len(hit) else None,
                    median_gross=float(hit.gross_r.median()) if len(hit) else None,
                    median_net=float(hit.net_r.median()) if len(hit) else None,
                    gross_capture_ratio=float(hit.gross_r.sum()/hit.holding_peak_r.sum()) if len(hit) else None))
            for reason in (1, 2):
                for minutes in cfg["windows_minutes"]:
                    subset = rec[(rec.population == population) & (rec.reason == reason) &
                                 (rec.minutes == minutes)]
                    if segment >= 0:
                        subset = subset[subset.segment == segment]
                    complete = subset[subset.complete]
                    adverse = complete.adverse_bound_before_entry_reclaim_r.dropna()
                    summaries.append(dict(population=population, segment=segment,
                        reason=reason, minutes=minutes, n=len(subset),
                        eligible=len(complete), censored=int((~subset.complete).sum()),
                        **counts(complete, boolean_cols),
                        median_post_max_r=float(complete.post_max_r.median()) if len(complete) else None,
                        recovered_adverse_bound_median_r=float(adverse.median()) if len(adverse) else None,
                        recovered_with_adverse_bound_lt_1_5r=int((adverse < 1.5).sum()),
                        recovered_with_adverse_bound_lt_2r=int((adverse < 2).sum())))
    pd.DataFrame(holding_summaries).to_csv(out/"holding_thresholds.csv", index=False)
    pd.DataFrame(summaries).to_csv(out/"recovery_summary.csv", index=False)
    rec.to_csv(out/"post_stop_windows.csv.gz", index=False, compression="gzip")
    # Conditional paired control: identical pair membership and horizon coverage.
    paired = []
    for minutes in cfg["windows_minutes"]:
        sample = rec[(rec.reason == 1) & rec.complete & (rec.minutes == minutes)]
        pair = sample[sample.population == "signal"].merge(
            sample[sample.population == "random"], on="pair_id", suffixes=("", "_random"))
        paired.append(dict(minutes=minutes, both_initial_stop_and_covered=len(pair),
            **{f"{name}_{key}": int(pair[key+suffix].eq(True).sum())
               for name, suffix in (("signal", ""), ("random", "_random"))
               for key in boolean_cols}))
    write_json(out/"conditional_matched_controls.json", paired)
    # Useful per-trade wide export, including unrecovered and censored trades.
    detail = rows.copy()
    for minutes in (15, 30, 60, 240, 1440):
        sub = rec[rec.minutes == minutes].drop(columns=["segment", "reason", "minutes"])
        sub = sub.rename(columns={c:f"post_{minutes}m_{c}" for c in sub
                                  if c not in ("pair_id", "population")})
        detail = detail.merge(sub, on=["pair_id", "population"], how="left", validate="one_to_one")
    detail.to_csv(out/"all_trade_excursions.csv.gz", index=False, compression="gzip")
    signal = detail[detail.population == "signal"]
    columns = ["pair_id", "entry_shanghai", "exit_shanghai", "side", "risk_percent",
               "holding_peak_r", "holding_peak_upper_r", "gross_r", "net_r", "reason",
               "post_60m_first_touch_0r_minutes", "post_1440m_post_max_r",
               "post_1440m_adverse_bound_before_entry_reclaim_r"]
    signal.sort_values("holding_peak_r", ascending=False).head(20)[columns].to_csv(
        out/"largest_holding_peaks.csv", index=False)
    fast = signal[(signal.reason == 1) & (signal.post_60m_touch_0r == True) &
                  (signal.post_1440m_complete == True)]
    fast.sort_values("post_1440m_post_max_r", ascending=False).head(20)[columns].to_csv(
        out/"fast_recoveries_with_largest_24h_move.csv", index=False)
    bucket = pd.cut(signal.holding_peak_r+1e-7, [float("-inf"),1,3,5,10,20,float("inf")],
                    right=False, labels=["<1", "1–3", "3–5", "5–10", "10–20", ">=20"])
    bucket_table = signal.assign(peak_bucket=bucket).groupby("peak_bucket", observed=False).agg(
        n=("pair_id","count"), gross_r=("gross_r","sum"), net_r=("net_r","sum"))
    bucket_table.to_csv(out/"holding_buckets.csv")
    stopped = signal[signal.reason == 1]
    combined = []
    for segment in (-1, 0, 1, 2):
        sub = stopped[stopped.post_1440m_complete == True]
        if segment >= 0:
            sub = sub[sub.segment == segment]
        quick = sub[sub.post_60m_touch_0r == True]
        combined.append(dict(segment=segment, eligible_24h=len(sub), recovered_entry_in_1h=len(quick),
            **{f"quick_recovery_then_24h_touch_{r}r":int((quick[f"post_1440m_touch_{r}r"] == True).sum())
               for r in (1,3,5,10,20)}))
    write_json(out/"summary.json", dict(
        builder_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        configuration=cfg, source_rows=len(raw), audited_holding_paths=len(rows),
        audited_recovery_windows=checked_windows,
        signal_trades=len(signal),
        reasons={str(int(k)):int(v) for k,v in signal.reason.value_counts().items()},
        maximum_holding_r=float(signal.holding_peak_r.max()),
        gross_r=float(signal.gross_r.sum()), net_r=float(signal.net_r.sum()),
        initial_stops_that_touched_5r=int(((stopped.holding_peak_r >= 5-1e-7)).sum()),
        compound_recovery_counts=combined,
        reproduction=".venv/bin/python -m yoyo.evaluation.pepe_range_recovery",
        interpretation="Descriptive excursions; future recovery is not a realizable strategy payoff. No strategy, cost, or sizing changes."))
    print(json.dumps(dict(signal=len(signal), paths_audited=len(rows), output=str(out))))


if __name__ == "__main__":
    main()
