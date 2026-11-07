"""Summarize constituent outcomes for fixed-timeframe Monday breadth signals.

The signal row is the equal-weight unit: first summarize each signal's eligible
constituents, then report the equal-weight mean of those per-signal summaries.
Pooled constituent statistics and coverage are also retained so larger signals
cannot silently dominate the signal-level result.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


SIGNAL_KEYS = ['minutes', 'known', 'decision']
SPLIT = pd.Timestamp('2025-01-01', tz='UTC')
SIX_DAYS = pd.Timedelta(days=6)
PERIOD_ORDER = ['all', 'early', 'late']
ASSET_ORDER = ['BTC', 'ETH', 'SOL']
BAND_ORDER = ['1', '2-4', '5-9', '10-24', '25-34', '35-49', '50-99', '100-199', '200+']


def _require(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f'{label} is missing required columns: {missing}')


def _as_bool(values: pd.Series, label: str) -> pd.Series:
    """Parse bool columns without treating nonempty strings such as 'False' as true."""
    if values.isna().any():
        raise ValueError(f'{label} contains null values')
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    mapped = normalized.map({'true': True, '1': True, 'yes': True,
                             'false': False, '0': False, 'no': False})
    if mapped.isna().any():
        bad = sorted(normalized[mapped.isna()].unique().tolist())
        raise ValueError(f'{label} contains non-boolean values: {bad}')
    return mapped.astype(bool)


def _prepare_inputs(members: pd.DataFrame, signals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    _require(members, SIGNAL_KEYS + ['symbol', 'eligible', 'net_pct', 'peak_pct', 'control_n',
                                     'control_net_pct', 'control_win_pct', 'control_peak_pct'], 'members')
    _require(signals, SIGNAL_KEYS + ['initial', 'band', 'continued', 'new_coins', 'period'], 'signals')

    member_columns = SIGNAL_KEYS + ['symbol', 'eligible', 'net_pct', 'peak_pct', 'control_n',
                                    'control_net_pct', 'control_win_pct', 'control_peak_pct']
    m = members.loc[:, member_columns].copy()
    s = signals.copy()
    for frame, label in ((m, 'members'), (s, 'signals')):
        frame['minutes'] = pd.to_numeric(frame.minutes, errors='raise').astype(int)
        frame['known'] = pd.to_datetime(frame.known, utc=True, errors='raise')
        frame['decision'] = pd.to_datetime(frame.decision, utc=True, errors='raise')
    m['eligible'] = _as_bool(m.eligible, 'members.eligible')
    s['continued'] = _as_bool(s.continued, 'signals.continued')
    s['initial'] = pd.to_numeric(s.initial, errors='raise').astype(int)
    s['new_coins'] = pd.to_numeric(s.new_coins, errors='raise').astype(int)
    s['band'] = s.band.astype(str)
    m['symbol'] = m.symbol.astype(str)
    for column in ['net_pct', 'peak_pct', 'control_n', 'control_net_pct', 'control_win_pct', 'control_peak_pct']:
        m[column] = pd.to_numeric(m[column], errors='coerce')
    m['control_n'] = m.control_n.fillna(0).astype(int)

    if 'period' in s:
        period = s.period.astype(str).str.lower()
        if period.isin(['early', 'late', 'crossing']).all():
            s['period'] = period
        else:
            # Six-day labels match the frozen study: early paths finish before
            # the split, late paths start on/after it, and crossing paths stay
            # in the all-period output only.
            s['period'] = np.select(
                [s.decision + SIX_DAYS < SPLIT, s.decision >= SPLIT],
                ['early', 'late'], default='crossing')
    else:
        s['period'] = np.select(
            [s.decision + SIX_DAYS < SPLIT, s.decision >= SPLIT],
            ['early', 'late'], default='crossing')

    if s.duplicated(SIGNAL_KEYS).any():
        raise ValueError('signals must contain one row per timeframe and signal clock')
    if m.duplicated(SIGNAL_KEYS + ['symbol']).any():
        raise ValueError('members contain duplicate signal/symbol rows')
    if s.empty:
        raise ValueError('signals is empty')
    if m.empty:
        raise ValueError('members is empty')

    signal_keys = s[SIGNAL_KEYS].drop_duplicates()
    membership = m[SIGNAL_KEYS].drop_duplicates()
    matched = membership.merge(signal_keys, on=SIGNAL_KEYS, how='left', indicator=True, validate='one_to_one')
    if matched._merge.ne('both').any():
        raise ValueError('members include signal clocks absent from signals')
    counts = m.groupby(SIGNAL_KEYS, sort=False).size().rename('member_rows').reset_index()
    check = s.merge(counts, on=SIGNAL_KEYS, how='left', validate='one_to_one')
    if check.member_rows.isna().any() or not check.member_rows.eq(check.initial).all():
        bad = check.loc[check.member_rows.ne(check.initial), SIGNAL_KEYS + ['initial', 'member_rows']].head(5)
        raise ValueError('each signal must have one row per initial constituent; mismatches: '
                         + bad.to_dict('records').__repr__())
    return m, s


def _stat_inputs(rows: pd.DataFrame) -> pd.DataFrame:
    """Create masked metric columns once so all summaries use vectorized groupby."""
    work = rows.copy()
    eligible = work.eligible
    net_ok = eligible & work.net_pct.notna()
    peak_ok = eligible & work.peak_pct.notna()
    control_ok = eligible & work.control_net_pct.notna() & work.control_n.gt(0)
    control_peak_ok = eligible & work.control_peak_pct.notna() & work.control_n.gt(0)
    paired_ok = eligible & work.net_pct.notna() & work.control_net_pct.notna()
    peak_paired_ok = eligible & work.peak_pct.notna() & work.control_peak_pct.notna()

    work['_net'] = work.net_pct.where(net_ok)
    work['_net_win_pct'] = (100.0 * work.net_pct.gt(0)).where(net_ok)
    work['_net_win'] = work.net_pct.gt(0).where(net_ok)
    work['_peak'] = work.peak_pct.where(peak_ok)
    work['_peak_ge5_pct'] = (100.0 * work.peak_pct.ge(5)).where(peak_ok)
    work['_peak_ge10_pct'] = (100.0 * work.peak_pct.ge(10)).where(peak_ok)
    work['_control_member'] = control_ok.astype('int8')
    work['_control_n'] = work.control_n.where(control_ok, 0)
    work['_control_net'] = work.control_net_pct.where(control_ok)
    work['_control_win'] = work.control_win_pct.where(control_ok)
    work['_control_peak'] = work.control_peak_pct.where(control_peak_ok)
    work['_paired_excess'] = (work.net_pct - work.control_net_pct).where(paired_ok)
    work['_peak_paired_excess'] = (work.peak_pct - work.control_peak_pct).where(peak_paired_ok)
    return work


def _aggregate_members(rows: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    """Pool eligible constituent rows, preserving classification and price coverage."""
    work = _stat_inputs(rows)
    grouped = work.groupby(dimensions, sort=True, observed=True, dropna=False)
    result = grouped.agg(
        member_rows=('symbol', 'size'),
        eligible_members=('eligible', 'sum'),
        net_n=('_net', 'count'),
        net_wins=('_net_win', 'sum'),
        win_pct=('_net_win_pct', 'mean'),
        mean_net_pct=('_net', 'mean'),
        median_net_pct=('_net', 'median'),
        worst_net_pct=('_net', 'min'),
        peak_n=('_peak', 'count'),
        mean_peak_pct=('_peak', 'mean'),
        median_peak_pct=('_peak', 'median'),
        max_peak_pct=('_peak', 'max'),
        peak_ge5_pct=('_peak_ge5_pct', 'mean'),
        peak_ge10_pct=('_peak_ge10_pct', 'mean'),
        control_member_n=('_control_member', 'sum'),
        control_n=('_control_n', 'sum'),
        mean_control_net_pct=('_control_net', 'mean'),
        mean_control_win_pct=('_control_win', 'mean'),
        mean_control_peak_pct=('_control_peak', 'mean'),
        paired_n=('_paired_excess', 'count'),
        matched_excess_pp=('_paired_excess', 'mean'),
        peak_paired_n=('_peak_paired_excess', 'count'),
        paired_peak_excess_pp=('_peak_paired_excess', 'mean'),
    ).reset_index()
    result['excluded_members'] = result.member_rows - result.eligible_members
    result['class_eligible_pct'] = 100 * result.eligible_members / result.member_rows
    result['net_coverage_pct'] = 100 * result.net_n / result.eligible_members.replace(0, np.nan)
    result['net_coverage_all_members_pct'] = 100 * result.net_n / result.member_rows.replace(0, np.nan)
    result['peak_coverage_pct'] = 100 * result.peak_n / result.eligible_members.replace(0, np.nan)
    result['peak_coverage_all_members_pct'] = 100 * result.peak_n / result.member_rows.replace(0, np.nan)
    result['control_coverage_pct'] = 100 * result.control_member_n / result.eligible_members.replace(0, np.nan)
    result['all_positive_assessable'] = result.eligible_members.gt(0) & result.net_n.eq(result.eligible_members)
    result['all_members_positive'] = result.all_positive_assessable & result.net_wins.eq(result.eligible_members)
    result['all_members_positive'] = result.all_members_positive.where(result.all_positive_assessable)
    return result


def _signal_rows(members: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    stats = _aggregate_members(members, SIGNAL_KEYS)
    result = signals.merge(stats, on=SIGNAL_KEYS, how='left', validate='one_to_one', indicator=True)
    if result._merge.ne('both').any():
        raise ValueError('one or more signals have no constituent rows')
    result = result.drop(columns='_merge')
    if not result.member_rows.eq(result.initial).all():
        raise ValueError('each signal must have one row per initial constituent')
    return result


def _aggregate_summary(members: pd.DataFrame, signal_rows: pd.DataFrame) -> pd.DataFrame:
    group_dims = ['period', 'minutes', 'band', 'continued']
    signal_variants = [signal_rows.assign(period='all')]
    member_signals = members.merge(signal_rows[SIGNAL_KEYS + ['period', 'band', 'continued']],
                                   on=SIGNAL_KEYS, how='inner', validate='many_to_one')
    member_variants = [member_signals.assign(period='all')]
    for split in ('early', 'late'):
        signal_part = signal_rows[signal_rows.period.eq(split)]
        member_part = member_signals[member_signals.period.eq(split)]
        if not signal_part.empty:
            signal_variants.append(signal_part.assign(period=split))
            member_variants.append(member_part.assign(period=split))
    signal_data = pd.concat(signal_variants, ignore_index=True)
    member_data = pd.concat(member_variants, ignore_index=True)

    equal = signal_data.groupby(group_dims, sort=True, observed=True, dropna=False).agg(
        signals=('known', 'size'),
        initial_signal_members=('initial', 'sum'),
        equal_signal_win_pct=('win_pct', 'mean'),
        equal_signal_mean_net_pct=('mean_net_pct', 'mean'),
        equal_signal_median_net_pct=('median_net_pct', 'mean'),
        equal_signal_mean_peak_pct=('mean_peak_pct', 'mean'),
        equal_signal_peak_ge5_pct=('peak_ge5_pct', 'mean'),
        equal_signal_peak_ge10_pct=('peak_ge10_pct', 'mean'),
        equal_signal_control_net_pct=('mean_control_net_pct', 'mean'),
        equal_signal_control_win_pct=('mean_control_win_pct', 'mean'),
        equal_signal_control_peak_pct=('mean_control_peak_pct', 'mean'),
        equal_signal_matched_excess_pp=('matched_excess_pp', 'mean'),
        equal_signal_paired_peak_excess_pp=('paired_peak_excess_pp', 'mean'),
        equal_signal_net_coverage_pct=('net_coverage_pct', 'mean'),
        equal_signal_peak_coverage_pct=('peak_coverage_pct', 'mean'),
        equal_signal_control_coverage_pct=('control_coverage_pct', 'mean'),
        positive_mean_net_signals=('mean_net_pct', lambda x: int(x.gt(0).sum())),
        assessable_mean_net_signals=('mean_net_pct', 'count'),
        positive_mean_net_signal_pct=('mean_net_pct', lambda x: 100 * x.gt(0).sum() / x.notna().sum()
                                      if x.notna().any() else np.nan),
        all_positive_signals=('all_members_positive', 'sum'),
        all_positive_assessable_signals=('all_positive_assessable', 'sum'),
        all_positive_signal_pct=('all_members_positive', 'mean'),
    ).reset_index()
    equal['all_positive_signal_coverage_pct'] = (
        100 * equal.all_positive_assessable_signals / equal.signals.replace(0, np.nan))
    pooled = _aggregate_members(member_data, group_dims)
    pooled = pooled.rename(columns={
        'all_positive_assessable': 'pooled_all_positive_assessable',
        'all_members_positive': 'pooled_all_members_positive',
    })
    summary = equal.merge(pooled, on=group_dims, how='left', validate='one_to_one')
    return summary.sort_values(group_dims, kind='stable').reset_index(drop=True)


def _symbol_summary(members: pd.DataFrame, signal_rows: pd.DataFrame) -> pd.DataFrame:
    joined = members.merge(signal_rows[SIGNAL_KEYS + ['period', 'band', 'continued']],
                           on=SIGNAL_KEYS, how='inner', validate='many_to_one')
    variants = [joined.assign(period='all')]
    for split in ('early', 'late'):
        part = joined[joined.period.eq(split)]
        if not part.empty:
            variants.append(part.assign(period=split))
    data = pd.concat(variants, ignore_index=True)
    result = _aggregate_members(data, ['period', 'minutes', 'symbol'])
    result['signals'] = result.member_rows
    return result.sort_values(['period', 'minutes', 'symbol'], kind='stable').reset_index(drop=True)


def _focus_tables(summary: pd.DataFrame, signal_rows: pd.DataFrame,
                  major_summary: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    _require(major_summary, ['minutes', 'asset', 'band', 'continued', 'period'], 'major_summary')
    major = major_summary.copy()
    major['minutes'] = pd.to_numeric(major.minutes, errors='raise').astype(int)
    major['continued'] = _as_bool(major.continued, 'major_summary.continued')
    major['band'] = major.band.astype(str)
    major['period'] = major.period.astype(str).str.lower()
    focus = major[(major.minutes.eq(5)) & major.period.eq('all') & major.asset.isin(ASSET_ORDER)
                  & major.band.isin(BAND_ORDER)].copy()
    keys = ['minutes', 'band', 'continued']
    major_metrics = [column for column in focus.columns if column not in keys + ['asset', 'period']]
    wide_parts = []
    for asset in ASSET_ORDER:
        part = focus[focus.asset.eq(asset)][keys + major_metrics].copy()
        if len(part) != 18 or part.duplicated(keys).any():
            raise ValueError(f'major_summary must provide 18 frozen 5m rows for {asset}')
        part = part.rename(columns={column: f'{asset}_{column}' for column in major_metrics})
        wide_parts.append(part)
    major_wide = wide_parts[0]
    for part in wide_parts[1:]:
        major_wide = major_wide.merge(part, on=keys, how='inner', validate='one_to_one')
    if len(major_wide) != 18:
        raise ValueError('the frozen 5m major table must have all 9 breadth bands x both follower states')

    member_focus = summary[(summary.minutes.eq(5)) & summary.period.eq('all')
                           & summary.band.isin(BAND_ORDER)].copy()
    if len(member_focus) != 18 or member_focus.duplicated(keys).any():
        raise ValueError('constituent summary must provide all 9 5m breadth bands x both follower states')
    table = member_focus.merge(major_wide, on=keys, how='inner', validate='one_to_one')
    table['period'] = 'all'
    table['_band_rank'] = table.band.map({name: i for i, name in enumerate(BAND_ORDER)})
    table = (table.sort_values(['minutes', '_band_rank', 'continued'], kind='stable')
             .drop(columns='_band_rank').reset_index(drop=True))

    cases = signal_rows[(signal_rows.minutes.eq(5)) & signal_rows.band.eq('50-99')
                        & ~signal_rows.continued & signal_rows.period.isin(['early', 'late'])].copy()
    if len(cases) != 13 or cases.duplicated(SIGNAL_KEYS).any():
        raise ValueError('the focused 5m/50-99/no-followers cohort must contain 13 distinct signals')
    cases = cases.sort_values(['known', 'decision'], kind='stable').reset_index(drop=True)
    return table, cases


def write_outputs(members: pd.DataFrame, signals: pd.DataFrame, out: str | Path,
                  major_summary: pd.DataFrame) -> dict[str, Path]:
    """Write constituent summaries and return their artifact paths.

    `eligible=False` is a classification exclusion. Price coverage is measured
    independently from non-null `net_pct` and `peak_pct` among eligible rows.
    Every supplied member row is an actual first-candle constituent.
    """
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    m, s = _prepare_inputs(members, signals)
    signal_rows = _signal_rows(m, s)
    summary = _aggregate_summary(m, signal_rows)
    symbol_summary = _symbol_summary(m, signal_rows)
    five_minute, cases = _focus_tables(summary, signal_rows, major_summary)

    paths = {
        'summary': directory / 'summary.csv',
        'signals': directory / 'signals.csv.gz',
        'symbols': directory / 'symbol_summary.csv',
        'five_minute': directory / '5m_table.csv',
        'five_minute_cases': directory / '5m_13_cases.csv',
    }
    summary.to_csv(paths['summary'], index=False)
    signal_rows.sort_values(['minutes', 'known', 'decision'], kind='stable').to_csv(paths['signals'], index=False)
    symbol_summary.to_csv(paths['symbols'], index=False)
    five_minute.to_csv(paths['five_minute'], index=False)
    cases.to_csv(paths['five_minute_cases'], index=False)
    return paths
