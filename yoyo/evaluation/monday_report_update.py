"""Update the explicitly requested Monday report from immutable framework results.

No signals, orders or trade profits are simulated here. Existing native ledgers
and canonical summaries supply results. Only descriptive aggregation, seeded
sampling and post-exit excursion diagnostics are added. Candle inputs are the
registered Binance 5m history, through each frozen fill's exit interval.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation.monday_body_hold import excursions
from yoyo.evaluation.monday_cases_report_data import compact_bars, records

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'reports/monday-1618-cases-20261023'
OUT = ROOT / 'artifacts/monday-cases-report-20261024'
EXP = ROOT / 'experiments/active'
PATHS = {
    'parameters': EXP / 'exp-monday-vectorbt-parameters-20261023-v1',
    'body': EXP / 'exp-monday-1h-body-hold-vectorbt-20261024-v1',
    'targets': EXP / 'exp-monday-near-far-targets-vectorbt-20261024-v1',
    'partial': EXP / 'exp-monday-tp1-breakeven-20261024-v1',
    'timed': EXP / 'exp-monday-limit-timed-vectorbt-20261024-v1',
    'aug19': EXP / 'exp-monday-aug19-review-20261024-v1',
}
SEED = 20261024


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def query(rows, paths, label):
    fields = sorted({k for row in rows for k in row})
    definitions = {
        'win_rate': 'Positive final R / closed trades; crypto net, XAU and GROUP1_GROSS gross.',
        'mean_r': 'Mean independent per-trade R. One R is original entry-to-pin-stop distance; not account return.',
        'realized_payoff_ratio': 'Mean positive R / absolute mean negative R, distinct from profit factor.',
        'profit_factor': 'Sum of positive R / absolute sum of negative R.',
        'paired_signal_r': 'Mean signal R only on complete matched sets.',
        'paired_random_r': 'Mean of three closed controls matched by symbol, actual NY entry week, side and prior ATR bucket.',
        'mfe_r': 'Retrospective gross maximum favorable excursion truncated at native exit; O-H-L-C hypothesis.',
        'mfe_before_exit_bar_r': 'Favorable excursion from complete bars before exit bar; conservative timing bound.',
        'sl_after_one_r': 'SL trades with gross MFE >=1R before native exit; not a separately simulated exit policy.',
        'grid_win_rate': 'Positive net/gross return at declared candle-grid reference exit; not exact elapsed-time win rate.',
        'true_time_win_rate_lower': 'OHLC-based lower bound for unknown intrabar touch clock, not a confidence interval.',
        'true_time_win_rate_upper': 'OHLC-based upper bound for unknown intrabar touch clock, not an executable strategy.',
        'period': 'all=full history; earlier=before2023 UTC; later=2023 onward. Split-crossing positions excluded in early/later comparisons.',
        'basis': 'net uses OKX maker0.02%/taker0.05% read2026-10-23; gross for spot with unknown broker costs.',
    }
    return {'rows': rows, 'source': {
        'label': label, 'files': [str(p.relative_to(ROOT)) for p in paths],
        'metricDefinitions': [{'id': k, 'label': k, 'definition': definitions.get(k, 'Frozen source field; original units and population retained. See configuration and field-definitions in the source experiment.')} for k in fields],
        'evidenceFlow': [{'title': '冻结结果复用', 'detail': 'No backtest replay. Read native framework outputs, retain censored records and paired-sample denominators; derived statistics are descriptive only.'}],
        'caveats': ['Independent trades, not funded account returns. No slippage/funding/liquidation model. Historical selection is exploratory, not pristine holdout validation.']}}


def n5_data():
    path = PATHS['parameters'] / 'all-trades-open-priority.csv.gz'
    f = pd.read_csv(path, float_precision='round_trip')
    g = f[(f.config_id == 'case4-n5-v20') & (f.symbol == 'BTCUSDT') & (f.population == 'signal')].copy()
    assert len(g) == 35 and g.closed.all() and (g.net_r > 0).sum() == 23
    cfg = json.loads((old.EXP / 'config.json').read_text())
    paths = dict(old.jobs(cfg)[0])['BTCUSDT']
    frame = volume.load(paths)
    # New native rows did not persist both Monday edges. Recover those chart
    # annotations from the original completed NY Monday, and verify barriers.
    for i, r in g.iterrows():
        start=pd.Timestamp(r.week).tz_localize('America/New_York').tz_convert('UTC')
        monday=frame.loc[start:start+pd.Timedelta(days=1)-pd.Timedelta(minutes=5)]
        assert len(monday)==288
        hi,lo=float(monday.high.max()),float(monday.low.min())
        level=lo-.618*(hi-lo) if r.side==1 else hi+.618*(hi-lo)
        assert np.isclose(level,r.level,rtol=1e-9)
        assert np.isclose(lo if r.side==1 else hi,r.target_price,rtol=1e-9)
        g.loc[i,['monday_high','monday_low']]=[hi,lo]
    # Locate the frozen events on the loaded frame rather than assuming a segment offset.
    g['entry_i'] = frame.index.get_indexer(pd.to_datetime(g.entry_time, utc=True))
    g['exit_original_i'] = frame.index.get_indexer(pd.to_datetime(g.exit_interval_start, utc=True))
    g = excursions(frame, g).sort_values('entry_time').reset_index(drop=True)
    g['direction'] = np.where(g.side == 1, '多', '空')
    g['outcome'] = np.where(g.net_r > 0, '盈利', '亏损')
    g['holding_h_upper'] = g.holding_hours + 5/60
    g['fee_bp'] = g.gross_bp - g.net_bp
    g.to_csv(OUT / 'btc-case4-n5-all35.csv', index=False)
    metrics = pd.read_csv(PATHS['parameters'] / 'metrics-entry-week.csv')
    expected=metrics[(metrics.config_id=='case4-n5-v20') & (metrics.group=='BTC') & (metrics.period=='all')].iloc[0]
    assert np.isclose(g.net_r.mean(), expected.mean_r, atol=1e-12)
    rows = []
    for period in ('all', 'earlier', 'later'):
        x = g if period == 'all' else g[(g.segment == int(period == 'later')) & ~g.crosses_selection_cut]
        r = x.net_r; wins = r[r > 0]; losses = r[r < 0]
        base = metrics[(metrics.config_id == 'case4-n5-v20') & (metrics.group == 'BTC') & (metrics.period == period)].iloc[0].to_dict()
        base.update(winners=len(wins), losers=len(losses), mean_win_r=wins.mean(), mean_loss_r=losses.mean(),
                    realized_payoff_ratio=wins.mean()/abs(losses.mean()), planned_rr_median=x.planned_rr.median(),
                    holding_hours_median=x.holding_hours.median(), fee_r_mean=x.fee_r.mean(),
                    sl_after_positive_mfe=int(((x.reason == 1) & (x.mfe_r > 1e-10)).sum()),
                    sl_after_one_r=int(((x.reason == 1) & (x.mfe_r >= 1)).sum()),
                    sl_after_one_r_prior_bar=int(((x.reason == 1) & (x.mfe_before_exit_bar_r >= 1)).sum()),
                    first_entry=x.entry_time.min(), last_entry=x.entry_time.max())
        rows.append(base)
    rng = np.random.default_rng(SEED)
    picks = pd.concat([g.loc[rng.choice(g.index[g.outcome == outcome], size=3, replace=False)] for outcome in ('盈利', '亏损')])
    samples = []
    for num, r in enumerate(picks.to_dict('records'), 1):
        pin, entry, end = map(pd.Timestamp, [r['pin_time'], r['entry_time'], r['exit_interval_end']])
        step = pd.Timedelta(hours=4)
        window = frame.loc[pin-step*24:max(end+step*8, entry+step*24)]
        h = window.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        p = h.loc[pin]
        assert np.isclose(frame.loc[entry].open, r['entry'])
        assert np.isclose(p.low if r['side'] == 1 else p.high, r['stop'])
        assert entry == pin+step*6
        checks = []
        for k in range(1,6):
            t = pin+step*k; b = h.loc[t]
            ok = b.close >= r['level'] if r['side'] == 1 else b.close <= r['level']
            assert ok
            checks.append(dict(number=k, open_time=str(t), close_time=str(t+step), **{v:float(b[v]) for v in old.OHLC}, passed=bool(ok)))
        sample = dict(r, sample_id=f'btc-n5-{num:02}', case=4, timeframe_minutes=240,
                      confirmation_count=5, target=r['target_price'], risk_pct=r['risk_bp']/100,
                      holding_h=r['holding_h_upper'], ambiguous=bool(r['ambiguous_bar']),
                      sampling_pool=int((g.outcome == r['outcome']).sum()),
                      detail_bars=compact_bars(h.loc[pin-step*24:entry+step*24]),
                      full_bars=compact_bars(h.loc[pin-step*24:end+step*8]), checks_json=json.dumps(checks))
        samples.append(json.loads(pd.DataFrame([sample]).to_json(orient='records', double_precision=12))[0])
    return g, records(pd.DataFrame(rows)), samples, [path, *map(Path, paths)]


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating report data')
    OUT.mkdir(parents=True, exist_ok=True)
    data_path = APP / 'src/data.json'
    data = json.loads(data_path.read_text()); identity = data['id']
    data['buildStatus'] = 'updating'
    data['generatedAt'] = pd.Timestamp.now(tz='UTC').isoformat()
    data['title'] = '周一1.618：形态、参数与出场规则的完整回测证据'
    queries = data['queries']; source_paths = []
    for key in ('parameters','body','targets','partial','timed'):
        p = PATHS[key] / ('metrics-entry-week.csv' if key == 'parameters' else 'metrics.csv')
        rows = records(pd.read_csv(p)); queries[f'latest-{key}'] = query(rows, [p, PATHS[key]/'config.json'], key)
        source_paths.append(p)
    for key, filename in [('targets','target-transitions.csv'),('partial','be-changes.csv')]:
        p = PATHS[key]/filename
        queries[f'latest-{key}-changes'] = query(records(pd.read_csv(p)), [p], filename); source_paths.append(p)
    selection = json.loads((PATHS['parameters']/'summary-entry-week.json').read_text())['selection']
    selection_rows = [{k:v for k,v in s.items() if k not in ('later','baseline_later','inference')} |
        {'later_n':s['later']['closed'],'later_mean_r':s['later']['mean_r'],'baseline_later_r':s['baseline_later']['mean_r'],
         'paired_signal_r':s['later']['paired_signal_r'],'paired_random_r':s['later']['paired_random_r'],
         'holm_p':s['inference'].get('holm_p'),'ci95_bp':str(s['inference'].get('ci95_bp'))} for s in selection]
    queries['latest-selection'] = query(selection_rows, [PATHS['parameters']/'summary-entry-week.json'], 'Chronological parameter selection')
    g, stats, samples, sources = n5_data(); source_paths.extend(sources)
    queries['latest-n5-stats'] = query(stats, sources, 'BTC case4 N5 full closed cohort + frozen matched controls')
    queries['latest-n5-ledger'] = query(records(g), sources, 'BTC case4 N5 all35 trades; no outcome exclusions')
    dump(OUT/'n5-samples.json', samples)
    from yoyo.evaluation.monday_cases_report_charts import draw
    assets=APP/'src/content/assets/latest';assets.mkdir(parents=True,exist_ok=True)
    imports=[];pairs=[]
    for s in samples:
        for mode in ('detail','full'):
            name=f"{s['sample_id']}-{mode}"
            src=OUT/draw(s,mode,OUT);shutil.copy2(src,assets/src.name)
            symbol=f'image{len(imports)}'
            imports.append(f'import {symbol} from "../assets/latest/{src.name}";');pairs.append(f'"{name}":{symbol}')
    shutil.copy2(PATHS['aug19']/'btc-eth-aug19.png',assets/'aug19.png')
    imports.append('import aug19 from "../assets/latest/aug19.png";');pairs.append('aug19')
    (APP/'src/content/report/latest-images.js').write_text('\n'.join(imports)+'\nexport const latestImages={'+','.join(pairs)+'};\n')
    queries['latest-n5-samples'] = query([{k:v for k,v in s.items() if k not in ('detail_bars','full_bars')} for s in samples], sources, 'Seed20261024: three uniform win and loss draws, independently')
    aug = json.loads((PATHS['aug19']/'summary.json').read_text())
    queries['latest-aug19'] = query([{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in aug['prices'].values()], [PATHS['aug19']/'summary.json'], '2026-08-19 Beijing retrospective, existing evidence')
    data_path.write_text(json.dumps(data, ensure_ascii=False, separators=(',',':'), allow_nan=False))
    assert json.loads(data_path.read_text())['id'] == identity
    # Keep raw CSV companions separate from the fully embedded HTML to avoid a huge UI payload.
    zip_path = OUT/'monday-1618-raw-csv.zip'
    companions = [(PATHS['parameters']/'metrics-entry-week.csv','参数研究.csv'),(OUT/'btc-case4-n5-all35.csv','BTC-4h-5根确认-35笔完整明细.csv')]
    for key in ('body','targets','partial','timed'):
        companions += [(p, f'{key}/{p.name}') for p in PATHS[key].glob('*.csv') if any(ord(c)>127 for c in p.name)]
    companions += [(PATHS['aug19']/n, f'aug19/{n}') for n in ('first-touches.csv','confirmation-candles.csv','cached-statistics.csv')]
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, name in companions: z.write(src,name)
    dump(OUT/'update-data-receipt.json', {'app_id':identity,'no_engine_replay':True,'sampling_seed':SEED,
        'base_signals':len(queries['trades']['rows']), 'new_n5_signals':len(g),'new_samples':len(samples),
        'queries':{k:len(v['rows']) for k,v in queries.items() if k.startswith('latest-')},
        'source_stamps':[{'path':str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in source_paths],
        'csv_files':[name for _,name in companions]})
    print(json.dumps({'app_id':identity,'n5_signals':len(g),'samples':len(samples),'zip_bytes':zip_path.stat().st_size}))


if __name__ == '__main__': main()
