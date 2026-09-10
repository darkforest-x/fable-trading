"""Reproduce the authenticated ETH1H fixed-rule signal diagnosis.

Only reads frozen local sources and replays the existing fixed Pine rules.
Does not fit, change thresholds, fetch a network feed, or evaluate new returns.
It writes only its ETH diagnostic JSON/CSVs beside this script; completed
cashbook win rates are read verbatim with explicit denominators.
"""
from pathlib import Path
import hashlib, json, re
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_burst_replay import features, replay, price_burst

ROOT = Path('/Users/zhangzc/fable-trading')
R = ROOT / 'experiments/active/exp-spike-burst-validation-20260910-v1/results'
OUT = ROOT / 'experiments/active/exp-spike-burst-three-year-20260910-v1/diagnostics'
START, END = pd.Timestamp('2026-07-10', tz='UTC'), pd.Timestamp('2026-09-09', tz='UTC')
sources = []
def checked(path, expected=None, role='input'):
    path = Path(path)
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        assert actual == expected, str(path)
    sources.append(dict(path=str(path), sha256=actual, size_bytes=len(raw), role=role))
    return raw
dm = json.loads(checked(R / 'dataset_manifest.json', role='frozen_dataset_manifest'))
vm = json.loads(checked(R / 'validation_manifest.json', role='frozen_validation_manifest'))
auth = {str(Path(a['path']).resolve()): a['sha256'] for a in dm['artifacts'] + vm['artifacts']}
def read_csv(name):
    path = R / name
    checked(path, auth[str(path.resolve())], 'frozen_scored_result_read_only')
    return pd.read_csv(path, low_memory=False)
for name in ['yoyo/evaluation/spike_burst_replay.py', 'yoyo/evaluation/pine/spike_burst_v1.pine']:
    checked(ROOT / name, dm['source_hashes'][name], 'frozen_causal_signal_source')
pine = (ROOT / 'yoyo/evaluation/pine/spike_burst_v1.pine').read_text()
display = ROOT / 'yoyo/evaluation/pine/spike_burst_v1_display.pine'
if display.exists():
    checked(display, role='display_variant')
    candidate = display.read_text()
    stripped = re.sub(r'// BEGIN BURST DISPLAY ONLY\n.*?// END BURST DISPLAY ONLY\n', '', candidate, flags=re.S)
    assert stripped == pine
source = next(a for a in dm['source_feature_artifacts'] if a['instrument'] == 'okx:ETH-USDT-SWAP:segment0' and a['minutes'] == 60)
checked(source['path'], source['sha256'], 'excluded_ETH_source_retained_for_background')
old = pd.read_pickle(source['path'])
catalog = next(a for a in dm['catalog_sources'] if a['path'].endswith('/okx.json'))
raw_catalog = json.loads(checked(catalog['path'], catalog['sha256'], 'authenticated_tick_catalog'))
market = next(a for a in raw_catalog['markets'] if a['symbol'] == 'ETH-USDT-SWAP')
tick = float(market['raw']['tickSz'])
assert tick == .01 and old.index.tz is not None
assert np.all(np.diff(old.index.asi8) == pd.Timedelta(hours=1).value)
f = features(old[['open','high','low','close','volume']])
f.attrs.update(old.attrs)
state = replay(f, tick)
prev = state.shift()
clock = f.index + pd.Timedelta(hours=1)
period = (clock >= START) & (clock < END)
assert int(period.sum()) == 1464

z = f[['open','high','low','close','volume','atr','rv','expansion','md','sb','middle','pastWidth','pastCrosses','ropeHigh','ropeLow','ready']].copy()
span = f.high - f.low
z['body_fraction'] = (f.close-f.open).abs()/span
z['close_location_long'] = (f.close-f.low)/span
z['prior_quiet_count'] = prev.quiet_count.fillna(0)
z['prior_quiet_high'] = prev.quiet_high
z['prior_quiet_low'] = prev.quiet_low
z['launch_high'] = state.launch_high
z['launch_low'] = state.launch_low
z['wait_bars'] = state.wait_bars
z['release_bar'] = state.release_bar
z['state'] = state.state
z['burst'] = state.burst
z['route'] = state.route
z['old_imacd_release_side'] = old.release_side
z['body_gate'] = z.body_fraction.ge(.55)
z['close_location_gate'] = z.close_location_long.ge(.75)
z['volume_gate'] = f.rv.ge(4)
z['expansion_gate'] = f.expansion.ge(3)
z['bullish_gate'] = f.close.gt(f.open)
z['above_prior_zone_gate'] = f.close.gt(prev.quiet_high)
z['above_launch_zone_gate'] = f.close.gt(state.launch_high)
z['above_six_ma_gate'] = f.close.gt(f.ropeHigh)
z['md_nonnegative_gate'] = f.md.ge(0)
z['md_positive_gate'] = f.md.gt(0)
z['md_ge_signal_gate'] = f.md.ge(f.sb)
z['md_gt_signal_gate'] = f.md.gt(f.sb)
z['mi_rising_gate'] = f.middle.gt(f.middle.shift())
z['md_rising_gate'] = f.md.gt(f.md.shift())
z['dense_gate'] = f.pastWidth.le(3) & f.pastCrosses.ge(2)
z['prior_qualified_quiet_gate'] = z.prior_quiet_count.ge(12)
# No ETH positions exist, including the warmup; keep the exact stop-before-gates clock.
z['free_after_exit_no_samebar_reentry'] = prev.trend_side.fillna(0).eq(0) & ~state.exit
assert not state.trend_side.ne(0).any() and not state.exit.any()
z['can_lead'] = z.prior_qualified_quiet_gate & prev.pending_side.fillna(0).eq(0) & z.free_after_exit_no_samebar_reentry & z.dense_gate & f.ready
z['valid_release_window'] = state.wait_bars.notna() & state.route.ne('price_first') & (state.pending_side.eq(1) | state.burst)
band = (.10*f.atr.shift()).where(~(z.prior_qualified_quiet_gate & prev.frozen_band.notna()), prev.frozen_band)
near = prev.pending_side.fillna(0).eq(0) & pd.concat([f.md.abs(), f.sb.abs()], axis=1).max(axis=1).le(band)
z['qualified_episode_leaves_near'] = z.prior_qualified_quiet_gate & ~near & z.dense_gate & z.free_after_exit_no_samebar_reentry & f.ready
z['release_side_at_near_exit'] = np.where(f.md.gt(band),1,np.where(f.md.lt(-band),-1,0))
z['mirror_short_price_gate_pass_in_long_history'] = False
for i in np.flatnonzero(z.can_lead.to_numpy()):
    row = f.iloc[i]
    z.iloc[i,z.columns.get_loc('mirror_short_price_gate_pass_in_long_history')] = price_burst(
        -1,row.open,row.high,row.low,row.close,row.md,row.sb,row.middle,f.middle.iloc[i-1],
        prev.quiet_high.iloc[i],prev.quiet_low.iloc[i],row.ropeHigh,row.ropeLow,row.rv,row.expansion)

price_gates = ['bullish_gate','above_prior_zone_gate','above_six_ma_gate','md_nonnegative_gate','md_ge_signal_gate','mi_rising_gate','volume_gate','expansion_gate','body_gate','close_location_gate']
release_gates = ['bullish_gate','above_launch_zone_gate','above_six_ma_gate','md_positive_gate','md_gt_signal_gate','md_rising_gate','volume_gate','expansion_gate','body_gate','close_location_gate']
force_gates = ['bullish_gate','volume_gate','expansion_gate','body_gate','close_location_gate']
def conjunction(frame, columns):
    return frame[columns].all(axis=1)
z['all_force_gates_no_context'] = conjunction(z,force_gates)
z['price_directional_before_force'] = z.can_lead & conjunction(z,price_gates[:6])
z['price_full_gate_pass'] = z.can_lead & conjunction(z,price_gates)
z['release_full_gate_pass'] = z.valid_release_window & conjunction(z,release_gates)
assert np.array_equal((z.price_full_gate_pass | z.release_full_gate_pass).to_numpy(),state.burst.to_numpy())
z['bar_open_utc'] = f.index.astype(str)
z['confirmed_at_utc'] = clock.astype(str)
z['bar_open_beijing'] = f.index.tz_convert('Asia/Shanghai').astype(str)
z['confirmed_at_beijing'] = clock.tz_convert('Asia/Shanghai').astype(str)
x = z.loc[period].copy()

def records(frame, cols=None):
    if cols is not None: frame = frame[cols]
    return json.loads(frame.to_json(orient='records'))
detail_cols = ['bar_open_utc','confirmed_at_utc','confirmed_at_beijing','close','rv','expansion','body_fraction','close_location_long','volume_gate','expansion_gate','body_gate','close_location_gate']
def funnel(context, gates):
    base = x[context] if context else x.ready
    current = base.copy()
    cumulative = [dict(gate='context',count=int(current.sum()))]
    for gate in gates:
        current = current & x[gate]
        cumulative.append(dict(gate=gate,count=int(current.sum())))
    individual = {gate:int((base & x[gate]).sum()) for gate in gates}
    one_failure = {}
    for gate in gates:
        other = [g for g in gates if g != gate]
        rows = base & ~x[gate] & conjunction(x,other)
        one_failure[gate] = dict(count=int(rows.sum()),bars=records(x.loc[rows],detail_cols))
    return dict(context=context or 'all_ready_period_bars',context_bars=int(base.sum()),cumulative=cumulative,
                independent_pass_counts_on_same_context=individual,only_this_gate_fails=one_failure)

release_groups = []
for release_i, group in x.loc[x.valid_release_window].groupby('release_bar'):
    release_i = int(release_i)
    release_groups.append(dict(
        release_bar_open_utc=str(f.index[release_i]),
        release_confirmed_utc=str(clock[release_i]),
        release_confirmed_beijing=str(clock[release_i].tz_convert('Asia/Shanghai')),
        valid_bars=len(group),volume_pass=int(group.volume_gate.sum()),expansion_pass=int(group.expansion_gate.sum()),
        maximum_relative_volume=float(group.rv.max()),maximum_tr_expansion=float(group.expansion.max()),
        burst_count=int(group.burst.sum()),bars=records(group,detail_cols)))

events = read_csv('events.csv.gz')
accounts = read_csv('accounts_summary.csv')
event_summary = read_csv('event_summary.csv')
eth_events = events[(events.venue=='okx') & (events.symbol=='ETH-USDT-SWAP') & (events.minutes==60)]
rates = accounts[(accounts.arm=='burst_trail') & (accounts.population=='all_assets') & (accounts.account=='full')]
natural_counts = {}
for minute in [60,240]:
    pp = R / f'accounts/combined_{minute}_burst_trail_all_assets_full_ledger.csv.gz'
    checked(pp,auth[str(pp.resolve())],'frozen_cashbook_no_new_execution')
    ledger = pd.read_csv(pp,low_memory=False)
    natural = ledger[ledger.portfolio_selected.eq(True) & ledger.natural_exit.eq(True)]
    selected = ledger[ledger.portfolio_selected.eq(True)]
    natural_counts[str(minute)] = dict(selected_count=len(selected),selected_net_wins=int(selected.net_return.gt(0).sum()),
        natural_count=len(natural),natural_net_wins=int(natural.net_return.gt(0).sum()),
        censored_count=int(selected.censored.sum()),natural_only_win_rate=float(natural.net_return.gt(0).mean()))

d = dict(schema='spike-burst-eth1h-gate-diagnostic-v1',created_at=str(pd.Timestamp.now(tz='UTC')),
    scope='Fixed causal signal diagnosis only; no parameter changes, no new return evaluation, no short backtest, no network',
    source_authentication=sources,source_exclusion=source,
    direction=dict(pine_default='多头',pine_options=['多头','双向','空头'],
        pine_has_short_price_first=True,pine_has_short_release=True,pine_has_short_risk_and_protection=True,
        frozen_python_replay='long-only',completed_61day_return_evaluation='long-only',
        short_returns_validated=False,
        short_price_candidates_in_observed_long_state=int(x.mirror_short_price_gate_pass_in_long_history.sum()),
        short_probe_limit='This checks the mirror force function against the observed long-default quiet-state contexts only; it is not a replay of the short or bidirectional state machine.'),
    period=dict(decision_start_utc=str(START),decision_end_exclusive_utc=str(END),
        source_first_open_utc=str(f.index.min()),source_last_open_utc=str(f.index.max()),period_bars=len(x),
        period_ready_bars=int(x.ready.sum()),source_bars=len(f),tick=tick),
    facts=dict(scored_events_ETH1H=len(eth_events),exclusion_reason=source['exclude_reason'],
        actual_fixed_Pine_ETH1H_arrows_period=int(x.burst.sum()),actual_fixed_Pine_ETH1H_arrows_full_source=int(state.burst.sum()),
        actual_signal_dates=records(x.loc[x.burst],detail_cols+['route']),
        prequalified_quiet_bars=int(x.prior_qualified_quiet_gate.sum()),dense_qualified_price_context_bars=int(x.can_lead.sum()),
        valid_release_windows=len(release_groups),valid_release_window_bars=int(x.valid_release_window.sum()),
        samebar_long_force_without_quiet_context=int(x.all_force_gates_no_context.sum()),
        ongoing_trend_suppressed_bars=int((~x.free_after_exit_no_samebar_reentry).sum())),
    funnels=dict(price_first=funnel('can_lead',price_gates),release_confirm=funnel('valid_release_window',release_gates),
        force_on_all_bars=funnel(None,force_gates)),
    release_windows=release_groups,
    qualified_near_exit_side_counts={str(int(k)):int(v) for k,v in x.loc[x.qualified_episode_leaves_near,'release_side_at_near_exit'].value_counts().items()},
    price_breakout_context_bars=records(x.loc[x.price_directional_before_force],detail_cols),
    strong_bars_without_qualified_launch=records(x.loc[x.all_force_gates_no_context],detail_cols+['can_lead','valid_release_window','prior_quiet_count']),
    old_IMACD_releases_not_SPIKE_bursts=records(x.loc[x.old_imacd_release_side.ne(0)],detail_cols+['old_imacd_release_side']),
    frozen_completed_account_rates=records(rates,['scope','minutes','trades','win_rate','return_pct','max_drawdown_pct','natural_exits','boundary_marks']),
    frozen_completed_combined_natural_rates=natural_counts,
    frozen_completed_combined_event_rates=records(event_summary[(event_summary.scope=='combined')&(event_summary.arm=='burst_trail')],['minutes','valid','win_rate','natural_exits','boundary_marks']),
    limitations=[
        'ETH/BTC were excluded from the completed altcoin cashbooks. Their absent event rows alone are not evidence of zero indicator arrows.',
        'The fixed-source signal replay independently confirms ETH1H zero arrows in this period. It does not check a later live chart, another feed, or changed chart inputs.',
        'Conditional gates are explanatory counts on the actual frozen long-default state; removing a gate is not simulated and would alter later episodes and suppression.',
        'Sequential funnel losses depend on listed gate order; independent counts and single-failure rows are provided to avoid attributing all rejection to one arbitrary ordering.',
        'Eight high-force candles exist outside a fully qualified entry context; force alone is not this indicator.',
        'A stricter engineering threshold is not a demonstrated optimal threshold for ETH or altcoins.',
        'Existing candidate and cashbook win rates have different denominators; headline selected rates include boundary marks. Natural-only rates are separately reported.'
    ],
    reproduction_script=str(Path(__file__).resolve()),
    reproduce_command=".venv/bin/python experiments/active/exp-spike-burst-three-year-20260910-v1/diagnostics/reproduce_eth_1h.py")
OUT.mkdir(parents=True,exist_ok=True)
x.to_csv(OUT/'eth_1h_bar_gates.csv',index=False)
x.loc[x.can_lead | x.valid_release_window | x.old_imacd_release_side.ne(0)].to_csv(OUT/'eth_1h_launch_contexts.csv',index=False)
for name in ['eth_1h_bar_gates.csv','eth_1h_launch_contexts.csv']:
    q=OUT/name
    d.setdefault('diagnostic_artifacts',[]).append(dict(path=str(q),sha256=hashlib.sha256(q.read_bytes()).hexdigest(),size_bytes=q.stat().st_size))
(OUT/'eth_1h_gate_audit.json').write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(facts=d['facts'],qualified_near_exit_side_counts=d['qualified_near_exit_side_counts'],
    short_probe=d['direction']['short_price_candidates_in_observed_long_state'],
    price_breakout_context_bars=d['price_breakout_context_bars'],
    completed_rates=d['frozen_completed_account_rates'],natural_rates=natural_counts,outputs=d['diagnostic_artifacts']),ensure_ascii=False,indent=2))
