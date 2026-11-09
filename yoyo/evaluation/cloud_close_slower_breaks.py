"""Test buying the least advanced members of the same bullish Wednesday breakout pool."""
import json

import pandas as pd

from yoyo.evaluation import cloud_close_leader_rotation as leader

EXP = leader.EXP.parent/'exp-cloud-close-slower-breaks-20261109-v1'
KEY = ['symbol','entry_ms']


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed lowest-momentum entry comparison')
    config = json.loads((leader.EXP/'config.json').read_text())
    src = leader.universe.sources()
    frozen = pd.read_csv(leader.EXP/'candidate_pool.csv.gz',float_precision='round_trip')
    pool = frozen.sort_values(['decision_ms','momentum30','symbol'])
    selected = pool.groupby('decision_ms',sort=False).head(3).copy()
    selected['rank'] = selected.groupby('decision_ms').cumcount()+1
    selected['event_id'] = 'slower:'+selected.decision_ms.astype(str)+':'+selected.symbol
    selected['parent_id'] = 'slower:'+selected.decision_ms.astype(str)
    selected['draw_order'] = selected['control_seed'] = None
    selected['is_control'] = False
    selected['entry_order'] = selected.momentum30
    selected['selected_actual'] = True
    cached = pd.read_csv(leader.EXP/'native_paths.csv.gz',float_precision='round_trip')
    keep = ['entry_price','exit_bar_ms','exit_phase','exit_reason','exit_price','exit_fee_rate','stop_price']
    if (cached.groupby(KEY)[keep].nunique(dropna=False)>1).any().any():
        raise ValueError('A same-clock native execution differs between source arms')
    cached = cached.drop_duplicates(KEY)
    keys = pd.MultiIndex.from_frame(selected[KEY])
    reusable = pd.MultiIndex.from_frame(cached[KEY])
    missing = selected[~keys.isin(reusable)]
    books = [cached[pd.MultiIndex.from_frame(cached[KEY]).isin(keys)]]
    new = 0
    if not missing.empty:
        fresh,_ = leader._path_book(missing,src,config)
        books.append(fresh)
        new = len(fresh)
    paths = pd.concat(books,ignore_index=True).set_index(KEY)
    chosen = selected.set_index(KEY)
    for col in ['parent_id','event_id','rank','entry_order','is_control','control_seed']:
        paths[col] = chosen.loc[paths.index,col]
    paths['id'] = paths.event_id
    if not paths.index.is_unique or set(paths.index)!=set(chosen[chosen.entry_stop_valid].index):
        raise ValueError('Fundable path set differs from selected confirmations with valid entry stops')
    metrics,funds,receipt = leader._run_account(paths.reset_index(),src,3,'lowest30d')
    selected.to_csv(EXP/'selections.csv.gz',index=False)
    funds.to_csv(EXP/'funded.csv.gz',index=False)
    receipt['equity'].to_csv(EXP/'equity.csv.gz',index_label='hour_end_utc')
    prev = json.loads((leader.EXP/'summary.json').read_text())
    out = dict(metrics=metrics.set_index('period').to_dict('index'),admission=receipt['admission'],
        selected=len(selected),reused=len(paths)-new,freshly_priced=new,
        strongest_reference=prev['results']['actual']['metrics']['all'],
        random_final_usdt=prev['random_control_summary']['all']['seed_values'])
    (EXP/'summary.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(out['metrics']['all'],flush=True)


if __name__=='__main__':
    main()
