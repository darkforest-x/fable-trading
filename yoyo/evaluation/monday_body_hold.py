"""Owner's 2026-10-24 case3 body-only confirmation and exit-truncated audits.

Signal inputs: completed NY Monday range; first hourly touch per week/side;
pin OHLC; next N complete, consecutive hourly open/close pairs. Equality is
allowed for confirmation bodies, never for the original pin body. Wicks may
cross. No volume, outcome or excursion enters admission. Existing preparation,
candidate timing and native VectorBT execution are reused without modification.
Excursions are retrospective diagnostics, truncated at the framework exit.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_parameter_signals as s
from yoyo.evaluation.monday_15m_volume_rejection import week_keys


def prepare_body(base):
    ctx = s.prepare_case(base, 3)
    h, week, step = ctx['h'], ctx['week'], ctx['step']
    local, _ = week_keys(h.index)
    complete = h.bars.eq(12) & h.minutes.eq(60)
    start = pd.DatetimeIndex(pd.to_datetime(week)).tz_localize('America/New_York').tz_convert('UTC').as_unit('ns')
    expected = (h.index.asi8-start.asi8)//step.value
    seen = pd.Series(np.arange(len(h)), index=h.index).groupby(week).cumcount()
    known = seen.eq(expected) & complete.groupby(week).cummin()
    if base['symbol'] == 'XAUUSD':
        known[:] = True
    body_min, body_max = h[['open','close']].min(axis=1), h[['open','close']].max(axis=1)
    for side in (1, -1):
        level = ctx['sides'][side]['level']
        touch = (h.low.le(level) if side == 1 else h.high.ge(level))
        touch &= ctx['hi'].notna() & ctx['hi'].gt(ctx['lo']) & (local.dayofweek >= 1)
        first = touch & touch.astype(int).groupby(week).cumsum().eq(1)
        pin = body_min.gt(level) if side == 1 else body_max.lt(level)
        inside = body_min if side == 1 else body_max
        confirms = complete.copy()
        for n in range(1, 6):
            confirms &= complete.shift(-n, fill_value=False)
            confirms &= (h.index.to_series().shift(-n)-h.index.to_series()).eq(step*n)
            confirms &= inside.shift(-n).ge(level) if side == 1 else inside.shift(-n).le(level)
            ctx['sides'][side]['masks'][n] = (first & known & pin & confirms).copy()
    return ctx


def excursions(frame, trades):
    """Post-exit audit only; no order selection, barriers or trade PnL computed.

Use whole completed bars BEFORE exit, then only observed-open and monotone
vertices BEFORE the native exit segment plus its fill. A gap target ends at
the fixed limit fill (no credit for a better opening price). Legacy phase is
inferred from the frozen O-H-L-C hypothesis. Times have five-minute resolution.
The prior-bar MFE is also retained to expose uncertainty in the exit bar path.
"""
    f = trades.copy()
    if f.empty:
        return f
    a = frame[['open','high','low','close']].to_numpy(float)
    decimals = max(2, min(9, int(np.ceil(-np.log10(a.min())))+7))
    a = np.round(a, decimals)
    rows = []
    for r in f.itertuples():
        i, j, side = int(r.entry_i), int(r.exit_original_i), int(r.side)
        if not 0 <= i <= j < len(a):
            raise AssertionError('Excursion indices outside the source segment')
        if pd.Timestamp(r.entry_time) != frame.index[i] or pd.Timestamp(r.exit_interval_start) != frame.index[j]:
            raise AssertionError('Cached fill is not aligned with this source segment')
        # Start at entry; find extremes in full bars preceding the exit bar.
        hi = lo = float(r.entry); hi_i = lo_i = i
        if j > i:
            ih = i+int(np.argmax(a[i:j,1])); il = i+int(np.argmin(a[i:j,2]))
            if a[ih,1] > hi: hi, hi_i = a[ih,1], ih
            if a[il,2] < lo: lo, lo_i = a[il,2], il
        prior_mfe = max(0., side*((hi if side == 1 else lo)-r.entry)/r.risk)
        o,h,l,c = a[j]
        reason = int(r.reason)
        gap = reason != 0 and (side*(o-r.stop) <= 0 or side*(o-r.target_price) >= 0)
        phase = r.exit_path_segment
        if pd.isna(phase):
            phase = 2 if reason == 0 else (0 if gap or (side == 1 and reason == 4) or (side == -1 and reason == 1) else 1)
        phase = int(phase)
        vertices = [] if gap else [o,h,l][:phase+1]
        vertices.append(float(r.exit_price))
        for price in vertices:
            if price > hi: hi, hi_i = price, j
            if price < lo: lo, lo_i = price, j
        best, worst = (hi,lo) if side == 1 else (lo,hi)
        best_i, worst_i = (hi_i,lo_i) if side == 1 else (lo_i,hi_i)
        mfe = max(0.,side*(best-r.entry)/r.risk)
        mae = max(0.,-side*(worst-r.entry)/r.risk)
        net_mfe = np.nan if pd.isna(r.fee_entry_rate) else (side*(best-r.entry)-r.entry*r.fee_entry_rate-best*r.fee_entry_rate)/r.risk
        rows.append(dict(planned_rr=side*(r.target_price-r.entry)/r.risk,
            risk_bp=r.risk/r.entry*10000, mfe_r=mfe, mae_r=mae,
            mfe_bp=mfe*r.risk/r.entry*10000, mfe_net_taker_r=net_mfe,
            mfe_before_exit_bar_r=prior_mfe,
            mfe_time=str(frame.index[best_i]), mae_time=str(frame.index[worst_i]),
            holding_hours=(frame.index[j]-frame.index[i]).total_seconds()/3600,
            exit_phase_audited=phase, exit_at_open=gap))
    return pd.concat([f.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
