"""Correct case1 gold clock using the actual local archive's independent overlap.

The previous fixedEST mapping followed provider documentation but conflicts with
the local2025 archive: source-NY mapping aligns15m returns with independent UTC
OKX gold data; fixedEST does not. Preserve v1 and write a new gold-only revision.
Reuse every candidate rule, cost assumption and native engine. Crypto fills are
read unchanged. This clock repair does not supply the volume absent from XAU.
"""
from __future__ import annotations

import json
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation.monday_15m_volume_rejection import compare
from yoyo.evaluation.range_framework import dump,file_stamp,runtime_versions

EXP=old.ROOT/'experiments/active/exp-monday-5m-wick-gold-clock-20261023-v2'
CODE=[*quality.CODE,'yoyo/evaluation/monday_5m_wick_gold_clock.py',
      'yoyo/evaluation/monday_15m_volume_rejection.py','tests/evaluation/test_monday_gold_clock.py']


def local_index(values):
    return pd.DatetimeIndex(pd.to_datetime(values,format='%Y%m%d %H%M%S')).tz_localize(
        'America/New_York',ambiguous='NaT',nonexistent='NaT').tz_convert('UTC').as_unit('ns')


def read_minutes(paths):
    parts=[];year2025=None;invalid=0
    for path in paths:
        with zipfile.ZipFile(path) as z:
            with z.open(next(n for n in z.namelist() if n.endswith('.csv'))) as handle:
                raw=pd.read_csv(handle,sep=';',header=None,names=['time',*old.OHLC,'volume'])
        if path.endswith('_2025.zip'):year2025=raw.copy()
        raw.index=local_index(raw.time);invalid+=int(raw.index.isna().sum())
        parts.append(raw.loc[raw.index.notna(),old.OHLC].astype(float))
    if invalid:raise ValueError(f'{invalid} ambiguous/nonexistent local source times; do not silently drop')
    return pd.concat(parts),year2025


def clock_audit(raw):
    path=old.ROOT/'data/crypto/market/kline_fetched/okx_XAU_USDT_SWAP_15m_41900.csv'
    f=pd.read_csv(path);f.index=pd.to_datetime(f.ts,unit='ms',utc=True)
    reference=f.close.astype(float).pct_change(fill_method=None)
    selected=raw[raw.time.between('20250604 000000','20250831 235900')]
    rows=[]
    for name,clock in [('fixed_EST',old.histdata_index),('local_New_York',local_index)]:
        s=pd.Series(selected.close.to_numpy(),index=clock(selected.time)).resample('15min').last().pct_change(fill_method=None)
        joined=pd.concat([reference.rename('okx'),s.rename('spot')],axis=1).dropna()
        rows.append(dict(mapping=name,n=len(joined),correlation=float(joined.corr().iloc[0,1]),
            by_month={str(month):dict(n=len(g),correlation=float(g.corr().iloc[0,1])) for month,g in joined.groupby(joined.index.month)}))
    assert rows[1]['correlation']>.9 and abs(rows[0]['correlation'])<.2,'Clock alignment evidence failed'
    return dict(reference=file_stamp(path),observations=rows,
        conclusion='Local source archive behaves as NY wall time; fixedEST v1 gold and combined-group values superseded',
        scope='Local files only, not a claim about every HistData download')


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=old.ROOT).returncode:raise RuntimeError('Commit builder first')
    cfg=json.loads((old.EXP/'config.json').read_text());EXP.mkdir(exist_ok=True)
    paths=next(p for s,p in old.jobs(cfg)[0] if s=='XAUUSD')
    identity=dict(config=cfg,source_clock='America/New_York observed local archives',
        inputs=[file_stamp(p) for p in paths],code=[file_stamp(old.ROOT/p) for p in CODE],runtime=runtime_versions())
    if (EXP/'summary.json').exists():
        assert json.loads((EXP/'identity.json').read_text())==identity
        print('Unchanged correction cached');return
    raw,year=read_minutes(paths);dump(EXP/'clock-audit.json',clock_audit(year))
    f,q=quality.clean_minutes(raw);blocked=quality.invalid_monday_weeks(q)
    q['invalid_monday_weeks']=sorted(blocked);dump(EXP/'data-quality.json',q)
    ledgers=[];audits=[]
    for segment,chunk in f.groupby('quality_segment',sort=True):
        entries,detail=old.candidates(chunk,'XAUUSD',cfg)
        if len(entries):entries=entries[~entries.week.isin(blocked)].copy()
        if not len(entries):continue
        for col in ['trade_id','pair_id']:entries[col]=f'q{segment}-'+entries[col]
        folder=EXP/f'segment-{segment}';folder.mkdir(exist_ok=True)
        entries.to_csv(folder/'candidates.csv.gz',index=False)
        rows,audit=old.execute(chunk,entries,'XAUUSD',folder,cfg)
        ledgers.append(rows);audits.append(audit)
    gold=pd.concat(ledgers,ignore_index=True);gold.to_csv(EXP/'gold-trades.csv.gz',index=False)
    crypto=pd.read_csv(old.EXP/'all_trades.csv.gz');crypto=crypto[crypto.symbol.isin(['BTCUSDT','ETHUSDT'])]
    combined=pd.concat([crypto,gold],ignore_index=True)
    tables={};comparisons={}
    for name,g in [('XAUUSD',gold),('BTC_ETH_GOLD',combined)]:
        tables[name]={}
        for segment in ['all','earlier','later']:
            x=g if segment=='all' else g[g.segment.eq(int(segment=='later'))]
            tables[name][segment]={p:old.stats(x[x.population.eq(p)]) for p in ['signal','random']}
        # Same paired test function, explicitly applied to gross basis points.
        c=g.copy();c['net_bp']=c.gross_bp
        comparisons[name]=compare(c,cfg);comparisons[name]['metric']='gross_bp'
    out=dict(supersedes='case1 XAU and combined first-group results only; all crypto v1 fills unchanged',
        tables=tables,comparisons=comparisons,positions=len(gold),native_fills=sum(a['fills'] for a in audits),
        oracle_checked=sum(a['oracle_checked'] for a in audits),
        previous=json.loads((old.EXP/'summary.json').read_text())['tables']['XAUUSD'],
        source_clock='America/New_York empirically verified on local archive',
        production_eligible=False,training_eligible=False)
    dump(EXP/'identity.json',identity);dump(EXP/'summary.json',out)
    print(json.dumps(out),flush=True)


if __name__=='__main__':main()
