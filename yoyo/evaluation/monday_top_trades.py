"""Owner-requested top-200 winning-trade review from frozen Monday ledgers.

No signal detection, order simulation, parameter search or new trade PnL.
Rank completed SIGNAL rows by existing net_bp (entry-notional return, no
leverage), never by R. XAU's unknown costs prohibit mixing it into this net
ranking; export a separate gross ranking. Keep one best measured exit policy
per symbol/side/entry-time/entry-price, explicitly an ex-post review selection.

Canonical repairs supersede older files: open-priority parameter ledger,
corrected NY-clock gold, body-only additions, far-target additions and split
additions. Timed exits use only the grid-reference scenario, not hindsight
OHLC bounds. Full-population counters remain distinct from the selected200.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, file_stamp, dump

EXP = ROOT / 'experiments/active/exp-monday-top200-review-20261024-v1'
OUT = ROOT / 'artifacts/monday-top200-review-20261024'
DELIVERY = OUT / '收益率前200笔_5分钟复盘'
N = 200
A = ROOT / 'experiments/active'
SOURCES = [
    ('case1', 'exp-monday-5m-wick-engulf-20261023-v1', 'all_trades.csv.gz'),
    ('gold_corrected', 'exp-monday-5m-wick-gold-clock-20261023-v2', 'gold-trades.csv.gz'),
    ('parameters', 'exp-monday-vectorbt-parameters-20261023-v1', 'all-trades-open-priority.csv.gz'),
    ('body', 'exp-monday-1h-body-hold-vectorbt-20261024-v1', 'signal-trades.csv.gz'),
    ('far', 'exp-monday-near-far-targets-vectorbt-20261024-v1', 'signal-trades.csv.gz'),
    ('partial', 'exp-monday-tp1-breakeven-20261024-v1', 'signal-trades.csv.gz'),
    ('timed', 'exp-monday-limit-timed-vectorbt-20261024-v1', 'all-horizons.csv.gz'),
]
FIELDS = set('trade_id pair_id parent_id population symbol side entry stop risk target_price level week '
    'entry_time pin_time confirmation_close_time case confirmations volume_window config_id rule '
    'exit_price reason closed exit_interval_start exit_interval_end gross_r net_r gross_bp net_bp '
    'fee_entry_rate fee_exit_rate ambiguous_bar engine_origin monday_high monday_low target_mode '
    'tp1_price tp2_price tp1_filled tp1_exit_price tp1_time runner_reason runner_exit_price '
    'be_price be_trigger_i be_effective_i horizon scenario entry_interval_end '
    'entry_resolution_minutes opening_fill refinement exit_time actual_duration_exact '
    'grid_hold_min_minutes grid_hold_max_minutes lower_net_bp upper_net_bp'.split())


def truth(s):
    return s.astype(str).str.lower().isin(['true', '1', '1.0'])


def label(r):
    kind = r['source_key']
    if kind in ('case1', 'gold_corrected'):
        return '情况1｜5m插针后紧接吞没｜近侧周一边界全平'
    if kind == 'timed':
        return f"首次触及1.618挂单｜{int(r['horizon'])}分钟网格参考平仓｜无止损"
    if kind == 'partial':
        mode = 'TP1后余仓含费保本' if r['config_id'] == 'split_be' else '余仓保持原止损'
        return f'1h实体守线3根｜近侧/远侧各平半｜{mode}'
    n = int(r['confirmations'])
    if kind in ('body', 'far'):
        return f"1h实体守线{n}根｜{'远侧' if kind == 'far' else '近侧'}周一边界全平"
    case = int(r['case'])
    if case == 2:
        return f"情况2｜15m首次插针｜前{int(r['volume_window'])}根量峰值｜{n}根实体守线｜近侧全平"
    rule = '1h后续影线不碰线' if case == 3 else '4h后续收盘守线'
    return f'情况{case}｜{rule}｜{n}根确认｜近侧周一边界全平'


def normalize(raw, kind, path, offset):
    """Canonical source columns only; no later market bars enter selection."""
    x = raw.copy()
    x['source_csv_record'] = np.arange(offset+1, offset+len(x)+1)
    if 'population' in x:
        x = x[x.population.eq('signal')].copy()
    if kind == 'case1': x = x[x.symbol.ne('XAUUSD')].copy()
    if kind == 'body': x = x[x.config_id.str.startswith('body-', na=False)].copy()
    if kind == 'far': x = x[x.config_id.str.startswith('far-', na=False)].copy()
    if kind == 'partial': x = x[x.config_id.isin(['split_hold', 'split_be'])].copy()
    if kind == 'timed': x = x[x.scenario.eq('grid')].copy()
    if not len(x): return x
    x['source_key'] = kind
    x['source_ledger'] = str(path.relative_to(ROOT))
    if kind == 'timed':
        x['closed'] = x.exit_price.notna()
        x['trade_id'] = x.parent_id.astype(str)+'-h'+x.horizon.astype(int).astype(str)
        x['config_id'] = 'limit-'+x.horizon.astype(int).astype(str)+'m-grid'
        x['pin_time'] = x.entry_time
        x['exit_interval_start'] = x.exit_time
        x['signal_minutes'] = 5
    elif kind in ('case1', 'gold_corrected'):
        x['case'] = 1; x['confirmations'] = 1; x['config_id'] = 'case1-5m-engulf'
        x['signal_minutes'] = 5
        x['pin_time'] = pd.to_datetime(x.entry_time, utc=True)-pd.Timedelta(minutes=10)
        x['engine_origin'] = 'nautilus_cached'
    else:
        x['signal_minutes'] = (x['case'].map({2:15, 3:60, 4:240})
            if 'case' in x else 60)
    x['closed'] = truth(x.closed)
    for col in ('entry_time', 'pin_time', 'exit_interval_start', 'exit_interval_end'):
        x[col] = pd.to_datetime(x[col], utc=True, format='mixed')
    if 'entry_interval_end' not in x: x['entry_interval_end'] = x.entry_time
    else:
        x['entry_interval_end'] = pd.to_datetime(x.entry_interval_end, utc=True, format='mixed')
    x['entry_key'] = (x.symbol+'|'+x.side.astype(int).astype(str)+'|'
        +x.entry_time.astype('int64').astype(str)+'|'+x.entry.map(lambda v: format(v, '.12g')))
    x['fee_bp'] = x.gross_bp-x.net_bp
    x['net_pct'] = x.net_bp/100
    x['gross_pct'] = x.gross_bp/100
    x['fee_pct'] = x.fee_bp/100
    return x


def retain_best(x, metric, n=N):
    if not len(x): return x
    # Deterministic ties favor shorter realized exposure, then stable source IDs.
    return x.sort_values([metric, 'exit_interval_end', 'source_key', 'config_id', 'trade_id'],
        ascending=[False, True, True, True, True], kind='stable').drop_duplicates('entry_key').head(n).copy()


def inspect_selection(x):
    """Reconcile selected cached returns with their frozen exit fill prices."""
    assert len(x) == N and x.entry_key.is_unique
    assert x.net_bp.gt(0).all() and x.closed.all() and x.net_bp.is_monotonic_decreasing
    gross = x.side*(x.exit_price-x.entry)/x.entry*10000
    partial = x.source_key.eq('partial')
    if partial.any():
        weighted = (x.loc[partial, 'tp1_exit_price']+x.loc[partial, 'runner_exit_price'])/2
        gross.loc[partial] = x.loc[partial, 'side']*(weighted-x.loc[partial, 'entry'])/x.loc[partial, 'entry']*10000
    np.testing.assert_allclose(gross, x.gross_bp, atol=1e-7, rtol=1e-10)
    np.testing.assert_allclose(x.net_pct*100, x.net_bp, atol=1e-9)
    assert x.fee_bp.gt(0).all()
    assert (x.exit_interval_end >= x.entry_time).all()
    return dict(selected=len(x), unique_entries=int(x.entry_key.nunique()),
                price_return_checks=len(x), positive_fees=len(x))


def select():
    cfg = json.loads((EXP/'config.json').read_text())
    OUT.mkdir(parents=True, exist_ok=True); DELIVERY.mkdir(exist_ok=True)
    best = pd.DataFrame(); gold = pd.DataFrame(); versions = Counter(); sources = []
    all_keys = set(); gold_keys = set()
    for kind, experiment, filename in SOURCES:
        path = A/experiment/filename
        stat = dict(kind=kind, experiment=experiment, **file_stamp(path),
                    raw_rows=0, signal_configuration_rows=0, closed_rows=0,
                    positive_net_rows=0, gold_gross_positive_rows=0)
        local = pd.DataFrame(); local_gold = pd.DataFrame()
        for raw in pd.read_csv(path, usecols=lambda c:c in FIELDS, chunksize=40000,
                               float_precision='round_trip', low_memory=False):
            x = normalize(raw, kind, path, stat['raw_rows'])
            stat['raw_rows'] += len(raw)
            if not len(x): continue
            stat['signal_configuration_rows'] += len(x)
            x = x[x.closed].copy(); stat['closed_rows'] += len(x)
            w = x[x.symbol.ne('XAUUSD') & x.net_bp.gt(0)].copy()
            g = x[x.symbol.eq('XAUUSD') & x.gross_bp.gt(0)].copy()
            stat['positive_net_rows'] += len(w); stat['gold_gross_positive_rows'] += len(g)
            all_keys.update(w.entry_key); gold_keys.update(g.entry_key); versions.update(w.entry_key)
            local = retain_best(pd.concat([local, w], ignore_index=True), 'net_bp')
            local_gold = retain_best(pd.concat([local_gold, g], ignore_index=True), 'gross_bp')
        best = retain_best(pd.concat([best, local], ignore_index=True), 'net_bp')
        gold = retain_best(pd.concat([gold, local_gold], ignore_index=True), 'gross_bp')
        sources.append(stat)
        print(f'{kind}: {stat["closed_rows"]} closed configuration rows; {stat["positive_net_rows"]} positive net', flush=True)
    best = best.reset_index(drop=True); gold = gold.reset_index(drop=True)
    audit = inspect_selection(best)
    best['rank'] = np.arange(1, len(best)+1)
    best['scheme'] = best.apply(label, axis=1)
    best['positive_tested_versions'] = best.entry_key.map(versions)
    for col in ('entry_time', 'pin_time', 'exit_interval_start', 'exit_interval_end'):
        best[col+'_bj'] = best[col].dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    best['direction'] = best.side.map({1:'多', -1:'空'})
    best['image_file'] = [f'{int(r.rank):03d}_{r.symbol}_{r.direction}_{r.entry_time.tz_convert("Asia/Shanghai"):%Y%m%d-%H%M}_净{r.net_pct:.2f}pct.png' for r in best.itertuples()]
    best.to_csv(OUT/'selected.csv', index=False)
    cols={'rank':'排名','symbol':'币种','direction':'方向','scheme':'方案','net_pct':'净收益率百分比',
          'gross_pct':'毛收益率百分比','fee_pct':'手续费占入场名义额百分比','net_r':'净R',
          'entry_time_bj':'入场时段开始_北京时间','exit_interval_start_bj':'退出时段开始_北京时间',
          'exit_interval_end_bj':'退出时段结束_北京时间','pin_time_bj':'触价信号柱开盘_北京时间',
          'entry':'入场价','stop':'原止损价','target_price':'止盈目标价','exit_price':'退出价',
          'positive_tested_versions':'该入场已测盈利版本数','image_file':'图片文件',
          'source_ledger':'原始成交账本','source_csv_record':'原始CSV数据记录序号',
          'config_id':'原方案ID','trade_id':'原交易ID','signal_minutes':'原信号周期分钟',
          'ambiguous_bar':'原账本同K双触','actual_duration_exact':'定时持仓时钟是否精确',
          'lower_net_bp':'定时真实时钟收益下界bp','upper_net_bp':'定时真实时钟收益上界bp'}
    table=best[[c for c in cols if c in best]].rename(columns=cols)
    table['我的观察']='';table['形态归类']='';table['可复用条件']=''
    table.to_csv(DELIVERY/'00_前200笔清单与复盘笔记.csv', index=False, encoding='utf-8-sig')
    if len(gold):
        gold['rank']=np.arange(1,len(gold)+1);gold['scheme']=gold.apply(label,axis=1)
        gold.to_csv(OUT/'gold-gross-ranking.csv', index=False)
        gc=gold[['rank','symbol','scheme','gross_pct','entry_time','exit_interval_start','source_ledger','config_id']].copy()
        gc.columns=['黄金毛收益排名','品种','方案','毛收益率百分比','入场UTC','退出时段开始UTC','原始账本','方案ID']
        gc.to_csv(DELIVERY/'黄金单独毛收益候选_未混入净收益排名.csv',index=False,encoding='utf-8-sig')
    summary=dict(config=cfg, source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        sources=sources, unique_positive_entries=len(all_keys), gold_unique_positive_entries=len(gold_keys),
        audit=audit, rank1_net_pct=float(best.net_pct.iloc[0]), rank200_net_pct=float(best.net_pct.iloc[-1]),
        gold_highest_gross_pct=float(gold.gross_pct.iloc[0]) if len(gold) else None,
        symbols=int(best.symbol.nunique()),by_source=best.source_key.value_counts().to_dict(),
        by_side=best.direction.value_counts().to_dict(),earliest_entry=str(best.entry_time.min()),
        latest_exit=str(best.exit_interval_end.max()), independent_return_not_account_return=True,
        selection_only_no_replay=True,training_eligible=False,production_eligible=False)
    dump(OUT/'selection.json', summary)
    dump(EXP/'selection-receipt.json', {'artifact':str(OUT.relative_to(ROOT)),'selected':len(best),'source_commit':summary['source_commit']})
    print(json.dumps({k:v for k,v in summary.items() if k not in ['config','sources']},ensure_ascii=False),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--select',action='store_true');ap.add_argument('--render',action='store_true')
    ap.add_argument('--ranks',type=int,nargs='*');args=ap.parse_args()
    builders=['yoyo/evaluation/monday_top_trades.py','yoyo/evaluation/monday_top_trade_charts.py']
    for p in builders:
        if subprocess.run(['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode or subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode:
            raise RuntimeError('Commit builders before generating review artifacts')
    if args.select: select()
    if args.render:
        from yoyo.evaluation.monday_top_trade_charts import render
        render(args.ranks)


if __name__ == '__main__':main()
