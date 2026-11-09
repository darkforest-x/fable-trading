"""Screen existing 12H cloud entries on the market-wide change in open contract quantity."""
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_delivery_control as admin

EXP = admin.EXP.parent / 'exp-cloud-close-open-interest-20261109-v1'
SOURCE = EXP.parent / 'exp-monday-1618-flow-20261023-v1'
KEY_MS = 12 * admin.HOUR
LAG_MS = 5 * 60_000


def panel_column(symbol):
    """Read exact dated 11:55 and 23:55 UTC sum_open_interest observations; no forward fills."""
    observed = {}
    files = sorted((SOURCE / 'inputs/metrics' / symbol).glob('*.zip'))
    if not files:
        raise FileNotFoundError(symbol)
    for path in files:
        if path.stem[-10:] > '2026-09-23':
            continue
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as raw:
                header = raw.readline().decode('utf-8-sig').strip().split(',')
                ti, oi, sy = (header.index(n) for n in ['create_time','sum_open_interest','symbol'])
                for line in raw:
                    if b'11:55:00' not in line and b'23:55:00' not in line:
                        continue
                    row = line.decode().strip().split(',')
                    timestamp = pd.Timestamp(row[ti], tz='UTC')
                    if timestamp.hour not in (11,23) or timestamp.minute != 55 or timestamp.second or timestamp.microsecond:
                        continue
                    if row[sy] != symbol:
                        raise ValueError(f'Unexpected symbol in {path}')
                    if not row[oi]:
                        continue
                    size = float(row[oi])
                    if not np.isfinite(size) or size <= 0:
                        continue
                    stamp = timestamp.value // 1_000_000
                    if stamp in observed and observed[stamp] != size:
                        raise ValueError(f'Conflicting OI snapshots in {path}')
                    observed[stamp] = size
    return pd.Series(observed, name=symbol, dtype='float64')


def market_panel():
    symbols = json.loads((SOURCE / 'config.json').read_text())['symbols']
    if len(set(symbols)) != 12:
        raise ValueError('Open-interest reference panel drifted')
    with ThreadPoolExecutor(max_workers=3) as executor:
        panel = pd.concat(list(executor.map(panel_column, symbols)), axis=1).sort_index()
    prior = panel.reindex(panel.index - KEY_MS).set_axis(panel.index)
    complete = panel.notna() & prior.notna()
    counts = pd.DataFrame({'covered': complete.sum(axis=1), 'shrinking': (panel.lt(prior) & complete).sum(axis=1)})
    counts.index += LAG_MS
    counts['majority'] = counts.shrinking * 2 > counts.covered
    counts.to_csv(EXP / 'market_clocks.csv.gz', index_label='entry_ms')
    return counts


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Completed 12H open-interest screening')
    panel = market_panel()
    archive = admin.archive
    extra = set(json.loads((archive.EXP / 'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP / 'summary.json').read_text())['new_archive_cohorts']['priced'])
    config = json.loads((admin.EXP / 'config.json').read_text())
    previous = json.loads((admin.EXP / 'summary.json').read_text())
    paths = archive.pool(extra)
    records, summary = [], {}
    with archive.market_quotes(extra):
        _, selected = admin.study.tenth.trend.inputs('Binance', paths)
        ready = admin.adjusted_legs(selected, extra, config['settlements'])
        covered = ready[ready.entry_ms.isin(panel.index[panel.covered.ge(7)])]
        for name, rows in [('coverage', covered), ('shrinking', covered[covered.entry_ms.isin(panel.index[panel.majority])])]:
            real = rows[~rows.is_control]
            original = admin.admitted(real)
            metrics, pos, eq = admin.fund(original.copy(), previous['index_price_scenarios'], 'proxy')
            records.extend(dict(scenario=name,period=p,**m) for p,m in metrics.items())
            summary[name] = dict(full=metrics['all'], dev=metrics['dev'], eligible=len(real)//2,
                                 development_pass=admin.study.tenth.trend.second.meets(metrics['dev']),
                                 funded_deliveries=int(original.delivery.sum()))
            pos.to_csv(EXP / f'funded_{name}.csv.gz')
            eq.to_csv(EXP / f'hourly_{name}.csv.gz', index_label='hour_end_utc')
            if name=='shrinking' and summary[name]['development_pass'] and metrics['dev']['final_usdt'] > summary['coverage']['dev']['final_usdt']:
                control = rows[rows.is_control & rows.parent_id.isin(real.parent_id)]
                paired = real[real.parent_id.isin(control.parent_id)]
                for label, cohort, slip in [('paired', paired, 0.), ('control', control, 0.), ('stress', real, .0005)]:
                    checked = admin.fund(admin.admitted(cohort), previous['index_price_scenarios'], 'proxy', slip=slip)[0]
                    records.extend(dict(scenario=label,period=p,**m) for p,m in checked.items())
    summary['first_observed_market_clock_utc'] = pd.Timestamp(panel.index[panel.covered.ge(7)].min(),unit='ms',tz='UTC').isoformat()
    summary['last_observed_market_clock_utc'] = pd.Timestamp(panel.index[panel.covered.ge(7)].max(),unit='ms',tz='UTC').isoformat()
    pd.DataFrame(records).to_csv(EXP / 'account_metrics.csv',index=False)
    (EXP / 'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False) + '\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
