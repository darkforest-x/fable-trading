"""Check confirmation attribution against a separate exit archive and closed prefixes."""
import argparse
import json
import math
from pathlib import Path
import pandas as pd
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v131_excursions import MANIFEST, provenance


def run(root):
    root=Path(root)
    events=pd.read_csv(root/'events.csv.gz').set_index(['cohort','trade_key'])
    path=Path('experiments/active/exp-spike-exit-giveback-20261007-v1/results_v1/trades.csv.gz')
    before=provenance(path);counts={}
    for chunk in pd.read_csv(path,chunksize=100000):
        z=chunk[(chunk.timeframe_min.eq(15)&chunk.policy.eq('incumbent')) |
                (chunk.timeframe_min.eq(60)&chunk.policy.eq('trail2'))]
        for r in z.itertuples():
            key=(r.cohort,r.trade_key)
            if key not in events.index:continue
            old=events.loc[key]
            column='parent_r' if r.version=='baseline' else 'delayed_r'
            if column=='delayed_r' and not old.confirmed:continue
            assert math.isclose(r.net_r,old[column],abs_tol=1e-8),(key,r.version,r.net_r,old[column])
            label=f'{r.cohort}:{int(r.timeframe_min)}:{r.version}'
            counts[label]=counts.get(label,0)+1
    assert before==provenance(path)
    d=pd.read_csv(root/'diagnostics.csv.gz');d=d[d.cohort.eq('signal')]
    chosen=[]
    for _,g in d.groupby('timeframe_min'):
        for col,ascending in [('chase_decision_r',True),('chase_decision_r',False),('delay_bars',False)]:
            chosen.append(g.sort_values(col,ascending=ascending).iloc[0])
    chosen=pd.DataFrame(chosen).drop_duplicates('trade_key')
    manifest=json.loads(MANIFEST.read_text());by={r['symbol']:r for r in manifest['streams']}
    checks=[]
    for symbol,g in chosen.groupby('symbol'):
        source=by[symbol]['path'];p=provenance(source)
        raw=pd.read_csv(source,usecols=['ts','open','high','low','close','volume'])
        base=raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
        for minutes,part in g.groupby('timeframe_min'):
            bars,_=complete_bars(base,int(minutes))
            for r in part.itertuples():
                e=bars.index.get_loc(pd.Timestamp(r.entry_time));c=e-1
                anchor=bars.index.get_loc(pd.Timestamp(r.anchor_time))-1
                assert c-anchor==r.delay_bars and e>anchor+1
                o,h,l,cl=[bars[k].to_numpy(float)[:c+1] for k in ['open','high','low','close']]
                # The actual fill is deliberately excluded from this prefix.
                a=float(o[anchor+1]);price=float(cl[c]);side=int(r.side);stop=float(r.initial_stop)
                risk=side*(a-stop)
                assert math.isclose(a,r.anchor_entry,abs_tol=1e-9)
                assert math.isclose(side*(price-a)/risk,r.chase_decision_r,abs_tol=1e-8)
                assert math.isclose(.002*price/(side*(price-stop)),r.fee_decision_r,abs_tol=1e-8)
                assert bars.index[c]+pd.Timedelta(minutes=int(minutes))==pd.Timestamp(r.entry_time)
                checks.append(dict(trade_key=r.trade_key,minutes=int(minutes),prefix_last_bar=str(bars.index[c]),
                    entry_bar_excluded=True,chase_decision_r=float(r.chase_decision_r),delay=int(r.delay_bars),source=p))
        assert p==provenance(source)
    result=dict(status='passed',exit_archive_comparisons=counts,exit_archive_total=sum(counts.values()),
        prefix_cases=checks,source_exit_archive=before,
        limitations='Extreme/delay prefix spot checks, not an exhaustive new audit of the inherited signal detector.')
    (root/'lineage_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='prefix_cases'}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);run(p.parse_args().root)
