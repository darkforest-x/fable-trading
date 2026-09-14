"""Compare saved three-arm exit interventions without reading any OHLC history.

All old outcomes are bound to the preceding delivery manifest. New tier
outputs must pass their own completion receipts. Identical original entries
form the primary comparison; serial reentries and censoring stay separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, paired_stats, periods, SPLIT

OLD = Path('experiments/active/exp-spike-v1-v8-be05-20260914-v1')
SYSTEMS = ('v1_native', 'v1_common', 'v8')
RULES = ('baseline', 'be05', 'tier')
IDS = ['system', 'event_key', 'venue', 'symbol', 'timeframe_min', 'side', 'entry_time']
ECONOMICS = ['exit_time', 'exit_price', 'net_r', 'net_return', 'censored', 'entry_price', 'initial_stop', 'initial_risk']


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified(path: Path, expected: str, sources: list) -> pd.DataFrame:
    if sha(path) != expected:
        raise ValueError(f'output SHA mismatch: {path}')
    f = pd.read_csv(path)
    sources.append(dict(path=str(path), sha256=expected, rows=len(f)))
    return f


def load_old(sources: list) -> pd.DataFrame:
    receipt = json.loads((OLD/'delivery_manifest.json').read_text())
    path = OLD/'delivery_v1/all_outcomes.csv.gz'
    match = [x for x in receipt['files'] if x['path'] == str(path)]
    if len(match) != 1:
        raise ValueError('old delivery lacks unique outcome identity')
    return verified(path, match[0]['sha256'], sources)


def load_common(path: Path, sources: list) -> pd.DataFrame:
    manifest = json.loads((path/'manifest.json').read_text())
    receipts = sorted((path/'streams').glob('*/completion.json'))
    if not manifest.get('complete') or int(manifest['streams']) != 3531 or len(receipts) != 3531:
        raise ValueError('new tier common stream coverage incomplete')
    frames = []
    for rp in receipts:
        receipt = json.loads(rp.read_text())
        if receipt.get('status') != 'complete':
            raise ValueError(f'incomplete receipt: {rp}')
        expected_names = {f'{arm}.{mode}_tier.csv.gz' for arm in ('v1_common_execution_long','v8') for mode in ('fixed','serial')}
        if not expected_names.issubset(receipt['files']):
            raise ValueError(f'missing tier arm or replay mode: {rp}')
        for name, expected in receipt['files'].items():
            if not name.endswith(('.fixed_tier.csv.gz', '.serial_tier.csv.gz')):
                continue
            f = verified(rp.parent/name, expected, sources)
            if f.empty:
                continue
            f['system'] = 'v1_common' if name.startswith('v1_common_execution_long.') else 'v8'
            f['mode'] = 'fixed' if '.fixed_tier.' in name else 'serial'
            f['rule'] = 'tier'
            f['event_key'] = f.stream_key+':'+f.signal_i.astype(int).astype(str)+':'+f.side.astype(int).astype(str)
            frames.append(f)
    return pd.concat(frames, ignore_index=True)


def load_native(path: Path, receipt_path: Path, sources: list) -> pd.DataFrame:
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('status') != 'complete' or int(receipt['paired_events']) != 6185:
        raise ValueError('native tier receipt is not complete')
    expected = receipt['files'][path.name]
    f = verified(path, expected, sources)
    if len(f) != 6185:
        raise ValueError('native tier must retain all 6185 frozen events')
    # Native writer preserves old controls with prefixes in its audit ledger.
    # Load only the new tier economics; old controls come from OLD manifest.
    if 'tier_net_r' in f:
        for name in ('exit_time','exit_price','exit_reason','net_r','net_return','mfe_r','censored'):
            f[name] = f[f'tier_{name}']
        f['stage1_armed'] = f.be05_trigger_count.gt(0)
        f['stage2_armed'] = f.tier15_trigger_count.gt(0)
        f['stage1_trigger_count'],f['stage2_trigger_count'] = f.be05_trigger_count,f.tier15_trigger_count
        for target,source in [('stage1_trigger_time','be05_trigger_bar_open'),('stage2_trigger_time','tier15_trigger_bar_open')]:
            f[target] = pd.to_datetime(f[source],utc=True)+pd.to_timedelta(f.timeframe_min,unit='m')
    f['system'], f['mode'], f['rule'], f['side'] = 'v1_native', 'fixed', 'tier', 1
    f['event_key'] = f.event_id
    return f


def normalize(table: pd.DataFrame) -> pd.DataFrame:
    for name in ('entry_time', 'exit_time'):
        table[name] = pd.to_datetime(table[name], utc=True, errors='raise')
    if not table.censored.isin([True, False]).all():
        raise ValueError('ambiguous censoring flags')
    table['censored'] = table.censored.astype(bool)
    if table.duplicated(['system', 'mode', 'rule', 'event_key']).any():
        raise ValueError('duplicate event identity')
    return table


def triples(table: pd.DataFrame) -> pd.DataFrame:
    """Freeze event identity and actual entry risk across all three policies."""
    parts = []
    for rule in RULES:
        f = table.loc[(table['mode'] == 'fixed') & (table.rule == rule), IDS+ECONOMICS].copy()
        f.rename(columns={c:f'{c}_{rule}' for c in ECONOMICS}, inplace=True)
        parts.append(f)
    out = parts[0]
    for f in parts[1:]:
        before = len(out)
        out = out.merge(f, on=IDS, validate='one_to_one')
        if len(out) != before or len(f) != before:
            raise ValueError('three-arm original-entry coverage differs')
    for name in ('entry_price', 'initial_stop', 'initial_risk'):
        base = out[f'{name}_baseline'].to_numpy(float)
        for rule in ('be05', 'tier'):
            other = out[f'{name}_{rule}'].to_numpy(float)
            if not np.isclose(base, other, rtol=1e-10, atol=1e-18).all():
                raise ValueError(f'frozen economic identity changed: {name}/{rule}')
    out['all_closed'] = ~out.censored_baseline & ~out.censored_be05 & ~out.censored_tier
    return out


def scoped(frame: pd.DataFrame, exit_fields: tuple[str, ...]):
    yield dict(period='full', timeframe_min='all', side_group='all'), frame
    earlier = frame.entry_time < SPLIT
    for field in exit_fields:
        earlier &= frame[field] < SPLIT
    slices = [('full', frame), ('earlier', frame.loc[earlier]), ('later', frame.loc[frame.entry_time >= SPLIT])]
    for period, part in slices:
        if period != 'full':
            yield dict(period=period, timeframe_min='all', side_group='all'), part
        for tf, sub in part.groupby('timeframe_min'):
            yield dict(period=period, timeframe_min=int(tf), side_group='all'), sub
        for side, sub in part.groupby('side'):
            yield dict(period=period, timeframe_min='all', side_group='long' if side == 1 else 'short'), sub


def compare_pair(frame: pd.DataFrame, left: str, right: str) -> dict:
    p = frame[IDS].copy()
    p['joint_closed'] = ~frame[f'censored_{left}'] & ~frame[f'censored_{right}']
    p['net_r_baseline'], p['net_r_be05'] = frame[f'net_r_{left}'], frame[f'net_r_{right}']
    p['delta_r'] = p.net_r_be05-p.net_r_baseline
    result = paired_stats(p)
    # The imported calculator names its generic second arm be05. Persist
    # explicit from/to labels rather than falsely calling the new tier BE05.
    names = {'be_loss_from_original_win':'candidate_loss_from_reference_win'}
    return {names.get(k, k.replace('baseline_', 'from_').replace('be05_', 'to_')):v for k,v in result.items()}


def run(common: Path, native: Path, native_receipt: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    sources = []
    old = load_old(sources)
    table = normalize(pd.concat([old, load_common(common, sources), load_native(native, native_receipt, sources)], ignore_index=True))
    triple = triples(table)
    primary, comparisons, independent = [], [], []
    for system, f in triple.groupby('system'):
        all_closed = f.loc[f.all_closed]
        for scope, part in scoped(all_closed, tuple(f'exit_time_{r}' for r in RULES)):
            for rule in RULES:
                temp = part[IDS].copy()
                for name in ('exit_time', 'net_r', 'net_return'):
                    temp[name] = part[f'{name}_{rule}']
                temp['censored'] = False
                primary.append(dict(system=system, rule=rule, **scope, **metrics(temp)))
        for left, right in [('baseline','be05'), ('baseline','tier'), ('be05','tier')]:
            for scope, part in scoped(f, (f'exit_time_{left}',f'exit_time_{right}')):
                comparisons.append(dict(system=system, from_rule=left, to_rule=right, **scope, **compare_pair(part,left,right)))
    for group, f in table.groupby(['system','mode','rule']):
        meta = dict(zip(['system','mode','rule'],group))
        for scope, part in scoped(f, ('exit_time',)):
            independent.append(dict(**meta, **scope, **metrics(part)))
    pd.DataFrame(primary).to_csv(output/'three_arm_summary.csv',index=False)
    pd.DataFrame(comparisons).to_csv(output/'comparison_summary.csv',index=False)
    pd.DataFrame(independent).to_csv(output/'independent_metrics.csv',index=False)
    triple.to_csv(output/'three_arm_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    table.to_csv(output/'all_outcomes.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    (output/'aggregation_receipt.json').write_text(json.dumps(dict(source_kind='saved outcomes only',sources=sources,
        script_sha256=sha(Path(__file__)),rows=len(table),triple_events=len(triple),
        files={p.name:sha(p) for p in output.iterdir() if p.is_file()}),ensure_ascii=False,indent=2))
    print(pd.DataFrame(primary).query("period=='full' and timeframe_min=='all' and side_group=='all'").to_string(index=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--common',type=Path,required=True);p.add_argument('--native',type=Path,required=True)
    p.add_argument('--native-receipt',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.common,a.native,a.native_receipt,a.output)
