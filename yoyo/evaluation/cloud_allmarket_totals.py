"""Small audit and counts to accompany the all-market V1.7 per-signal ledger."""
import json
import pandas as pd
from yoyo.evaluation.cloud_allmarket_census import EXP,TOP,OLD
from yoyo.evaluation.cloud_peak10_inputs import dump

def totals(g):
    h=g[g.status.eq('hit')]; known=g.exit_reason.isin(['stop','cloud_flip'])
    by=g.symbol.nunique()
    return dict(signals=len(g),symbols=int(by),symbols_with_hits=int(h.symbol.nunique()),
        distinct_peak_bars=len(h.drop_duplicates(['symbol','side','peak_time_bjt'])),
        hit_exit_counts=h.exit_reason.value_counts().to_dict(),status=g.status.value_counts().to_dict(),
        confirmation_basis_hits=int((g.confirmation_basis_lower_r>10).sum()),
        all_fixed_stops=int(g.exit_reason.eq('stop').sum()),known_exits=int(known.sum()),
        first_confirm=g.confirmed_bjt.min() if len(g) else None,last_confirm=g.confirmed_bjt.max() if len(g) else None)

def main():
    f=pd.read_csv(EXP/'signals.csv')
    u=pd.read_csv(EXP/'universe.csv')
    c=pd.read_csv(EXP/'signal_coverage.csv')
    a=f[~f.symbol.isin(['BTC','ETH'])]
    new=a[~a.was_top20]
    assert set(c.symbol)==set(u.symbol) and c.symbol.is_unique
    assert c.signals.sum()==len(f)
    altcover=c[~c.symbol.isin(['BTC','ETH'])]
    old=f[f.was_top20][['symbol','week','status','peak_lower_r','confirmation_basis_lower_r']].set_index(['symbol','week']).sort_index()
    ref=OLD[old.columns.tolist()+['symbol','week']].set_index(['symbol','week']).sort_index()
    pd.testing.assert_frame_equal(old,ref,check_exact=False,rtol=1e-13,atol=1e-13)
    confirmed=pd.to_datetime(f.confirmed_bjt,utc=True)
    first=pd.to_datetime(f.first_gt10_bjt,utc=True)
    days=((first-confirmed).dt.total_seconds()/86400).dropna()
    out=dict(universe_symbols=len(u),current=int(u.is_current.sum()),archived=int((~u.is_current).sum()),
        alts_enumerated=len(altcover),alt_symbols_without_signals=altcover.loc[altcover.signals==0,'symbol'].tolist(),
        counts={k:totals(g) for k,g in [('all',f),('all_alts',a),('main_non_top20_alts',new),
            ('current_alts',a[a.is_current]),('archived_alts',a[~a.is_current]),('old_top20',f[f.was_top20])]},
        yearly_primary={str(k):totals(g) for k,g in new.groupby(new.confirmed_bjt.str[:4])},
        first_detected_10r_bar_after_confirmation_days={str(k):float(v) for k,v in days.quantile([.25,.5,.75]).items()},
        checksum_original_top20='all 1292 weekly keys, outcomes and two lower-bound R series compared row by row')
    gaps=[]
    for receipt in sorted((EXP/'inputs').glob('*/receipt.json')):
        v=json.loads(receipt.read_text())
        for week in v.get('bars1h',{}).get('missing_ny_mondays',[]):
            gaps.append({'币种':v['symbol'],'缺少完整行情的纽约周一':week,'范围':'新增合约，自起始日至研究截止或归档边界'})
    pd.DataFrame(gaps,columns=['币种','缺少完整行情的纽约周一','范围']).to_csv(EXP/'新增合约缺失周一.csv',index=False,encoding='utf-8-sig')
    out['new_contract_missing_monday_count']=len(gaps)
    dump(EXP/'coverage_and_totals.json',out)
if __name__=='__main__':
    main()
