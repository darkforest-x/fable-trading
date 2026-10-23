"""Reconcile prepared N5 report evidence and preserve a portable raw-data pack.

Uses frozen fills only. Descriptive planned-RR bands retain three-control
matching by actual entry week. Outcome bands are counts, never new filters.
No new strategy, execution engine, target or stop rule is evaluated.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import zipfile
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_alt_n5_report import ROOT, SOURCE, OUT, dump
from yoyo.evaluation.monday_parameter_analysis import add_actual_week, strict_pairs


def main():
    subprocess.run(['git','diff','--exit-code','HEAD','--',__file__],cwd=ROOT,check=True)
    original=pd.read_csv(SOURCE/'全部9065笔交易.csv',float_precision='round_trip').set_index('trade_id')
    enriched=pd.read_csv(OUT/'全部9065笔含浮盈浮亏.csv',float_precision='round_trip').set_index('trade_id')
    pd.testing.assert_frame_equal(original,enriched[original.columns],check_exact=False,rtol=1e-12,atol=1e-12)
    assert enriched.closed.sum()==9041 and (~enriched.closed).sum()==24
    closed=enriched[enriched.closed]
    assert (closed.mfe_r>=-1e-10).all() and (closed.mae_r>=-1e-10).all()
    assert (closed.mfe_r+1e-8>=closed.mfe_before_exit_bar_r).all()
    assert (pd.to_datetime(closed.mfe_time,utc=True)<=pd.to_datetime(closed.exit_interval_start,utc=True)).all()
    assert (pd.to_datetime(closed.mae_time,utc=True)<=pd.to_datetime(closed.exit_interval_start,utc=True)).all()
    source=SOURCE.parent/'all-trades-open-priority.csv.gz'
    cols=['config_id','population','pair_id','trade_id','symbol','closed','entry_time','bucket','side','net_r','net_bp','entry','risk','target_price']
    parts=[]
    for chunk in pd.read_csv(source,usecols=cols,chunksize=150000,float_precision='round_trip'):
        parts.append(chunk[chunk.config_id.eq('case4-n5-v20')&~chunk.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])])
    pairs=strict_pairs(add_actual_week(pd.concat(parts,ignore_index=True)),'net').set_index('trade_id')
    assert len(pairs)==6519
    c=closed.copy()
    c['band']=pd.cut(c.planned_rr,[-np.inf,.25,.5,1,2,np.inf],right=False)
    bands=[]
    for interval,g in c.groupby('band',observed=False):
        p=pairs.loc[pairs.index.intersection(g.index)]
        bands.append(dict(band=str(interval),closed=len(g),wins=int(g.net_r.gt(0).sum()),
            win_rate=float(g.net_r.gt(0).mean()),mean_net_r=float(g.net_r.mean()),
            paired=len(p),paired_signal_r=float(p.net_r.mean()),paired_random_r=float(p.control_net_r.mean())))
    pd.DataFrame(bands).to_csv(OUT/'计划盈亏比分层及配对对照.csv',index=False,encoding='utf-8-sig')
    data=json.loads((OUT/'reviewed-queries.json').read_text())
    from yoyo.evaluation.monday_report_update import query
    data['alt-n5-rr-bands']=query(bands,[SOURCE/'全部9065笔交易.csv',source],
        'Descriptive planned-RR bands; fixed existing entries and same-cohort three-control comparisons')
    # Do not encourage return-conditioned groups to masquerade as an entry test.
    for r in data['alt-n5-distributions']['rows']:
        r.pop('mean_net_r',None)
    data['alt-n5-ledger']['source']['caveats'].append('Unresolved rows contain original data-boundary fields, not actual exits or observed continuous holding times. Display their exit, PnL and duration as unavailable.')
    data['alt-n5-excursions']['source']['metricDefinitions'] += [
        dict(id='sl_after_positive_mfe',label='止损前毛浮盈',definition='SL positions whose gross MFE before original native exit was positive; no fee threshold.'),
        dict(id='sl_after_positive_net_mfe',label='止损前含费浮盈',definition='Best observed price before exit could cover original taker entry and hypothetical taker exit fees; not an executed profit.'),
        dict(id='sl_after_one_r_prior_bar',label='完整K线达到1R后止损',definition='SL positions with MFE at least1R in complete5m bars strictly before original exit bar; not a breakeven-policy result.')]
    dump(OUT/'reviewed-queries.json',data)
    validation=dict(original_fields_and_outcomes_preserved=True,signals=9065,closed=9041,censored=24,
        excursion_rows=9041,source_pairs=6519,samples=12,charts=24,confirmed_sample_candles=60,
        no_strategy_replay=True,exit_truncated_excursions=True,
        visual_check=['alt-n5-01-detail.png','alt-n5-04-full.png'],
        html_status='not_generated_waiting_for_Stitch',pullback_test='not_started_waiting_for_entry_price_and_order_expiry',
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
    dump(OUT/'validation.json',validation)
    items=[(OUT/'全部9065笔含浮盈浮亏.csv','全部9065笔含浮盈浮亏.csv'),
        (OUT/'浮盈回吐与周聚类区间.csv','浮盈回吐与周聚类区间.csv'),
        (OUT/'计划盈亏比分层及配对对照.csv','计划盈亏比分层及配对对照.csv')]
    items += [(SOURCE/name,name) for name in ['分组统计.csv','分布与持仓时间.csv','连续止损及连亏区间.csv','最长市场连续止损逐笔.csv','字段定义.json','summary.json']]
    items += [(OUT/name,name) for name in ['receipt.json','validation.json','sample-selection.json','stitch-status.json']]
    items += [(p,'charts/'+p.name) for p in sorted((OUT/'charts').glob('*.png'))]
    with zipfile.ZipFile(OUT/'山寨4h五根确认-详细数据与盈亏图.zip','w',zipfile.ZIP_DEFLATED) as z:
        for path,name in items:z.write(path,name)
    print(json.dumps({'validation':validation,'rr_bands':bands},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
