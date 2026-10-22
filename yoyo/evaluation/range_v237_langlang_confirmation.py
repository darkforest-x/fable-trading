"""Owner-approved Langlang-style confirmation on frozen V2.37 opportunities.

Source: the owner's accepted October22 rule, not the full main-wave system.
Only completed 5m OHLC from the arrow through the decision close is inspected:
three bars hold the arrow extreme, then a directional candle closes beyond
the preceding candle's extreme. Pair publication must also be known. Entry
uses only the next observed open; its later high/low cannot select a trade.
No added 48-bar extreme/expiry, volume, trend, TP, BE, or position filters.
Native Nautilus receives a real opening quote followed by the unchanged OHLC
bar, so entry-bar protection is active without changing its shared adapter.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_2h_scaleout import load
from yoyo.evaluation.range_v237_twoyear import read

EXP = ROOT / 'experiments/active/exp-range-v237-langlang-confirm-20261022-v1'
CONFIG = EXP / 'config.json'
OHLC = ['open', 'high', 'low', 'close']
BAR_NS = 300_000_000_000
EPS = 1e-7


def confirmation(bars, anchor, publication, side, tick, hold_bars=24):
    """Sequential decision using completed candles; next open only for fill.

    Equality at the extreme is allowed while waiting (no strict break).
    After entry the native protective stop triggers on equality. No arbitrary
    preparation expiry: first confirmation or extreme break ends the wait.
    """
    stop = float(bars[anchor, 2 if side == 1 else 1])
    answer = dict(anchor_i=anchor, publication_i=publication, stop=stop,
                  turn_i=-1, decision_i=-1, open_i=-1, status='unconfirmed')
    for j in range(anchor + 1, len(bars)):
        o, h, l, c = bars[j]
        if side * ((l if side == 1 else h) - stop) < -tick * EPS:
            return dict(answer, status='extreme_broken', invalid_i=j)
        if j >= anchor + 4 and answer['turn_i'] < 0:
            boundary = bars[j-1, 1 if side == 1 else 2]
            if side*(c-o) > tick*EPS and side*(c-boundary) > tick*EPS:
                answer['turn_i'] = j
        if answer['turn_i'] >= 0 and j >= publication:
            answer['decision_i'] = j
            k = j+1
            if k >= len(bars):
                return dict(answer, status='missing_open')
            entry = float(bars[k, 0])
            risk = side*(entry-stop)
            if risk < tick-tick*EPS:
                return dict(answer, status='invalid_open_risk', open_i=k)
            end = k+hold_bars-1
            if end >= len(bars):
                return dict(answer, status='incomplete_horizon', open_i=k)
            return dict(answer, status='filled', open_i=k, end_bar_i=end,
                        entry=entry, risk=risk)
    return answer


def opening_frame(frame):
    """Observed opening point then full candle, preserving entry-bar SL.

    +1ns disambiguates the previous close from the next open. It is an event
    ordering convention, not measured latency or a fabricated market price.
    Only SL and a scheduled close exit are allowed with this representation.
    """
    values = frame[OHLC].to_numpy()
    out = np.empty((2*len(frame), 4))
    out[::2] = values[:, :1]
    out[1::2] = values
    stamps = np.empty(2*len(frame), dtype=np.int64)
    stamps[::2] = frame.index.as_unit('ns').asi8-BAR_NS+1
    stamps[1::2] = frame.index.as_unit('ns').asi8
    result = pd.DataFrame(out, columns=OHLC, index=pd.to_datetime(stamps, utc=True))
    assert result.index.is_monotonic_increasing and result.index.is_unique
    return result


def open_entries(rows):
    result = rows.copy()
    result['entry_i'] = result.open_i.astype(int)*2
    result['end_i'] = result.end_bar_i.astype(int)*2+1
    result['target_price'] = np.nan
    return result


def audit_open_exits(bars, rows, native, tick):
    """Independent barrier audit includes the entry candle and opening gaps."""
    expected = []
    for r in rows.itertuples():
        j = int(r.end_bar_i)
        price = bars[j, 3]
        reason, phase = 0, 1
        for k in range(int(r.open_i), int(r.end_bar_i)+1):
            if r.side*(bars[k, 0]-r.stop) <= tick*EPS:
                j, price, reason, phase = k, bars[k, 0], 1, 0
                break
            if r.side*(bars[k, 2 if r.side == 1 else 1]-r.stop) <= tick*EPS:
                j, price, reason, phase = k, r.stop, 1, 1
                break
        expected.append((j*2+phase, price, reason))
    expected = np.asarray(expected)
    np.testing.assert_array_equal(native.exit_i, expected[:, 0])
    np.testing.assert_allclose(native.exit_price, expected[:, 1], atol=tick*EPS, rtol=0)
    np.testing.assert_array_equal(native.reason, expected[:, 2])


def build_opportunities(frame, occ, midpoint, tick):
    """Signals use original/matched5m arrow; controls translate its known lag.

    Random preparation points are the frozen matched controls. Apply identical
    confirmation and use THEIR translated anchor candle's extreme, not a real
    signal's future prices. Zero-fill opportunities remain in paired analysis.
    """
    bars = frame[OHLC].to_numpy()
    signal = occ[occ.population.eq('signal')].set_index(['lane', 'event_id'])
    output = []
    for raw in occ.to_dict('records'):
        source = signal.loc[(raw['lane'], raw['event_id'])]
        opened = int(source.signal_ns if raw['lane'] == 5 else source.matched_ns)
        actual_anchor = int(frame.index.get_indexer([pd.Timestamp(opened+BAR_NS, tz='UTC')])[0])
        assert actual_anchor >= 0
        lag = int(source.entry_i)-actual_anchor
        assert 0 <= lag <= 7
        publication = int(raw['entry_i'])
        anchor = publication-lag
        decision = confirmation(bars, anchor, publication, int(raw['side']), tick)
        row = dict(raw)
        row.update(baseline_pair_id=int(raw['pair_id']), baseline_entry_i=publication,
                   baseline_entry=float(raw['entry']), baseline_stop=float(raw['stop']),
                   baseline_risk=float(raw['risk']), anchor_lag=lag,
                   trade_id=f"{raw['population']}-{raw['event_id']}", **decision)
        if row['status'] == 'filled':
            start = frame.index[row['open_i']]-pd.Timedelta(minutes=5)
            end = frame.index[row['end_bar_i']]
            assert end-start == pd.Timedelta(hours=2)
            # Keep each setup and outcome within its original chronological year.
            upper = midpoint if int(raw['segment']) == 0 else frame.index[-1]
            if end > upper:
                row['status'] = 'crosses_period_boundary'
            row['entry_time'] = start.isoformat()
            row['deadline'] = end.isoformat()
            row['wait_minutes'] = (start-frame.index[publication]).total_seconds()/60
            row['turn_wait_minutes'] = (frame.index[row['turn_i']]-frame.index[anchor]).total_seconds()/60
        output.append(row)
    result = pd.DataFrame(output)
    assert len(result) == len(occ) and result.trade_id.is_unique
    return result


def prepare(symbol):
    cfg = json.loads(CONFIG.read_text())
    _, _, frame, _, occ, stamps = load(symbol)
    spec = next(s for s in cfg['symbols'] if s['symbol'] == symbol)
    folder = EXP/symbol
    folder.mkdir(exist_ok=True)
    rows = build_opportunities(frame, occ, pd.Timestamp(cfg['midpoint']), spec['tick'])
    filled = rows[rows.status.eq('filled')].copy().reset_index(drop=True)
    # Frozen admission prefix checks never compare censored future outcomes.
    probes = np.unique(filled.decision_i.astype(int).quantile([.25,.5,.75]).astype(int))
    for end in probes:
        cutoff = end+2  # Only next OPEN is required; its H/L/C is not used.
        for r in filled[(filled.decision_i <= end) & (filled.decision_i > end-100)].itertuples():
            a = confirmation(frame[OHLC].to_numpy()[:cutoff], int(r.anchor_i),
                             int(r.publication_i), int(r.side), spec['tick'], hold_bars=1)
            assert a['status'] == 'filled' and a['decision_i'] == r.decision_i
            assert a['entry'] == r.entry and a['stop'] == r.stop
    rows.to_csv(folder/'opportunities.csv.gz', index=False)
    filled.to_csv(folder/'entries.csv.gz', index=False)
    dump(folder/'preparation.json', dict(symbol=symbol, inputs=stamps,
        config=file_stamp(CONFIG), builder=file_stamp(__file__),
        opportunities=len(rows), filled=len(filled), prefix_probes=len(probes),
        statuses=rows.groupby(['population','lane','status']).size().rename('n').reset_index().to_dict('records')))
    print(symbol, 'opportunities', len(rows), 'filled', len(filled), flush=True)


def execute(symbol):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads(CONFIG.read_text())
    _, _, frame, _, _, _ = load(symbol)
    folder = EXP/symbol
    tick = next(s['tick'] for s in cfg['symbols'] if s['symbol'] == symbol)
    entries = read(folder/'entries.csv.gz').sort_values('trade_id').reset_index(drop=True)
    expanded = opening_frame(frame)
    if (folder/'native.csv.gz').exists():
        raise RuntimeError('Native results already exist; audit/analyze saved fills instead of replay')
    policy = ExitPolicy(target_from_entry=True, opposite_hour_exit=False, round_trip_cost=cfg['cost'])
    zeros = np.zeros(len(expanded))
    native, fills, receipt = run_native(expanded, zeros, zeros, open_entries(entries),
                                       policy, tick=tick, symbol=symbol)
    native.to_csv(folder/'native.csv.gz', index=False)
    fills.to_csv(folder/'fills.csv.gz', index=False)
    dump(folder/'native_receipt.json', receipt)
    audit(symbol)


def audit(symbol):
    cfg = json.loads(CONFIG.read_text())
    _, _, frame, _, _, _ = load(symbol)
    folder = EXP/symbol
    tick = next(s['tick'] for s in cfg['symbols'] if s['symbol'] == symbol)
    entries = read(folder/'entries.csv.gz').sort_values('trade_id').reset_index(drop=True)
    native = read(folder/'native.csv.gz').sort_values('trade_id').reset_index(drop=True)
    np.testing.assert_array_equal(native.trade_id, entries.trade_id)
    for col in ['entry','stop','risk','side']:
        np.testing.assert_array_equal(native[col], entries[col])
    audit_open_exits(frame[OHLC].to_numpy(), entries, native, tick)
    np.testing.assert_allclose(native.net_r, native.gross_r-cfg['cost']*native.entry/native.risk, atol=EPS, rtol=0)
    assert (native.arm_i == -1).all()
    # Restore logical open/close clocks instead of leaking synthetic timestamps.
    native['framework_event_i'] = native.exit_i
    native['exit_bar_i'] = native.exit_i.astype(int)//2
    native['exit_at_open'] = native.exit_i.astype(int)%2 == 0
    native['entry_time'] = entries.entry_time
    native['exit_time'] = [str(frame.index[i]-pd.Timedelta(minutes=5 if opening else 0))
        for i,opening in zip(native.exit_bar_i,native.exit_at_open)]
    native.to_csv(folder/'trades.csv.gz', index=False)
    dump(folder/'audit.json', dict(native_positions=len(native), native_fills=len(read(folder/'fills.csv.gz')),
        all_entry_price_stop_parity=True, all_exit_price_time_reason_parity=True,
        synthetic_open_ordering_ns=1, original_ohlc_unchanged=True,
        exit_timestamp='Opening gap exact open, otherwise bar close bounds intrabar SL; deadlines exact close',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()))
    print(symbol, 'native audited', len(native), flush=True)


def metric(g):
    filled = g[g.filled]
    loss = -filled.loc[filled.net_r<0,'net_r'].sum()
    return dict(opportunities=len(g), n=len(filled), fill_rate=len(filled)/len(g),
        net_wins=int(filled.net_r.gt(EPS).sum()), win_rate=float(filled.net_r.gt(EPS).mean()),
        gross_win_rate=float(filled.gross_r.gt(EPS).mean()), gross_r=float(g.gross_r.sum()),
        net_r=float(g.net_r.sum()), mean_trade_r=float(filled.net_r.mean()),
        mean_opportunity_r=float(g.net_r.mean()), mean_trade_bp=float(filled.net_notional_bp.mean()),
        mean_opportunity_bp=float(g.net_notional_bp.mean()),
        pf=float(filled.loc[filled.net_r>0,'net_r'].sum()/loss) if loss else None,
        stops=int(filled.reason.eq(1).sum()), time_exits=int(filled.reason.eq(0).sum()),
        median_stop_percent=float((filled.risk/filled.entry*100).median()),
        median_fee_r=float(filled.fee_r.median()))


def analyze():
    from yoyo.evaluation.pepe_range_exits import inference
    from yoyo.evaluation.range_management_study import holm
    cfg=json.loads(CONFIG.read_text()); stats=[]; tests=[]; retention=[]; censoring=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol']; folder=EXP/symbol
        opp=read(folder/'opportunities.csv.gz'); trades=read(folder/'trades.csv.gz')
        censored=opp[opp.status.isin(['unconfirmed','missing_open','incomplete_horizon','crosses_period_boundary'])]
        excluded=set(zip(censored.lane,censored.event_id))
        censoring.append(dict(symbol=symbol,excluded_pairs=len(excluded),rows=censored[['lane','event_id','population','status']].to_dict('records')))
        eligible=lambda df: np.array([(lane,event) not in excluded for lane,event in zip(df.lane,df.event_id)])
        # A setup with an unobserved outcome is not a real zero-return rejection.
        # Remove its whole matched pair from both policies, retaining raw ledgers.
        opp=opp[eligible(opp)].copy()
        outcomes=['gross_r','fee_r','net_r','net_notional_bp','reason','exit_bar_i']
        combined=opp.merge(trades[['trade_id',*outcomes]],on='trade_id',validate='one_to_one')
        assert combined.status.eq('filled').all()
        combined['filled']=True
        missing=opp[~opp.status.eq('filled')].copy();missing['filled']=False
        for col in outcomes:missing[col]=0. if col not in ['reason','exit_bar_i'] else -1
        new=pd.concat([combined,missing],ignore_index=True);new['arm']='confirmation'
        baseline=read(ROOT/cfg['baseline']/symbol/'hold/trades.csv.gz')
        source_occ=load(symbol)[4]
        source_occ=source_occ[eligible(source_occ)].copy()
        old=source_occ.merge(baseline[['pair_id',*outcomes[:-1]]],on='pair_id',validate='many_to_one')
        old['arm']='original';old['filled']=True;old['trade_id']=old.population+'-'+old.event_id
        old['status']='filled'
        allrows=pd.concat([new,old],ignore_index=True)
        allrows.to_csv(folder/'results.csv.gz',index=False)
        for (lane,arm),g in allrows.groupby(['lane','arm']):
            for period,sub in [('all',g),('first_year',g[g.segment==0]),('later_year',g[g.segment==1])]:
                s=sub[sub.population=='signal']; r=sub[sub.population=='random']
                assert len(s)==len(r)
                stats.append(dict(symbol=symbol,lane=int(lane),arm=arm,period=period,**metric(s),random=metric(r)))
            s=g[g.population=='signal'];r=g[g.population=='random']
            paired=s[['event_id','week','net_r']].merge(r[['event_id','net_r']],on='event_id',suffixes=('','_random'),validate='one_to_one')
            tests.append(dict(symbol=symbol,lane=int(lane),arm=arm,contrast='same_rule_random',**inference(paired,cfg['seed'])))
        for lane in [5,15]:
            a=new[(new.lane==lane)&new.population.eq('signal')]
            b=old[(old.lane==lane)&old.population.eq('signal')]
            pair=a[['event_id','week','net_r','filled']].merge(b[['event_id','net_r']],on='event_id',suffixes=('','_random'),validate='one_to_one')
            tests.append(dict(symbol=symbol,lane=lane,arm='confirmation',contrast='original_all_opportunities',**inference(pair,cfg['seed'])))
            live=a[a.filled]
            delta=live.entry-live.baseline_entry
            retention.append(dict(symbol=symbol,lane=lane,baseline_winners=int((pair.net_r_random>EPS).sum()),
                missed_baseline_winners=int((~pair.filled & pair.net_r_random.gt(EPS)).sum()),
                admitted_baseline=metric(b[b.event_id.isin(live.event_id)]),
                median_wait_minutes=float(live.wait_minutes.median()), max_wait_minutes=float(live.wait_minutes.max()),
                median_entry_adverse_bp=float((live.side*delta/live.baseline_entry*10000).median()),
                median_risk_ratio=float((live.risk/live.baseline_risk).median()),
                statuses=a.status.value_counts().to_dict()))
    for t,p in zip(tests,holm([t['p_one_sided'] for t in tests])):t['holm_p']=p
    pd.json_normalize(stats).to_csv(EXP/'comparison.csv',index=False)
    dump(EXP/'summary.json',dict(config=cfg,comparison=stats,retention=retention,inference=tests,censoring=censoring))
    dump(EXP/'completion.json',dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        audits=[json.loads((EXP/s['symbol']/'audit.json').read_text()) for s in cfg['symbols']],
        training_eligible=False,production_eligible=False))
    print(json.dumps([r for r in stats if r['period']=='all'],ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run','audit','analyze'])
    p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    for path in [__file__,str(CONFIG)]:
        subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,check=True,capture_output=True)
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    if a.stage=='analyze':analyze()
    else:
        assert a.symbol
        {'prepare':prepare,'run':execute,'audit':audit}[a.stage](a.symbol)
