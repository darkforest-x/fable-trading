"""Reconcile independent blinded four-bar references and frozen YOLO detections.
Only pre-breakout four-bar tight bundles called 'yes' are clear references.
Per case/timeframe, allow a match only to a same-direction fresh structural
box overlapping >=3 of those four bars. Record the 2-bar overlap sensitivity.
The primary 4h horizon is shared. Additional 9 closes flag late reads, without
silently reclassifying them as primary. No price outcome enters either label.
"""
import numpy as np
import pandas as pd
from windows import HERE,DATA,MINUTE,cohort,after_read,window_end

def audit(labels, refs, boxes):
    assert labels.key.is_unique and refs.key.is_unique and set(labels.key)==set(refs.key)
    assert labels.vote.isin(['yes','no','border']).all()
    reviewed=refs.merge(labels,on='key',validate='one_to_one')
    observations=[]
    for r in reviewed.itertuples():
        hits=boxes.loc[boxes.case_id.eq(r.case_id) & boxes.tf.eq(r.tf) & boxes.structural_pass & boxes.same_side & boxes.fresh].copy()
        start=np.maximum(hits.core_start_ms,r.core_start_ms)
        end=np.minimum(hits.core_end_ms,r.core_end_ms)
        overlap=(end-start)/(r.tf*MINUTE)+1
        primary=hits[(overlap>=3)&hits.within4]
        extended=hits[overlap>=3]
        observations.append(dict(key=r.key,matched=not primary.empty,late_or_matched=not extended.empty,
            overlap_two=bool(((overlap>=2)&hits.within4).any()),matched_ms=primary.read_ms.min()))
    return reviewed.merge(pd.DataFrame(observations),on='key',validate='one_to_one')

def main():
    labels=pd.read_csv(HERE/'blind_votes.csv',dtype={'key':str})
    refs=pd.read_csv(DATA/'blind_key.csv',dtype={'key':str})
    boxes=pd.read_csv(DATA/'all_boxes.csv.gz')
    reads=pd.read_csv(HERE/'first_reads.csv')
    reviewed=audit(labels,refs,boxes)
    # Once votes are sealed, measure the price path of clear reference shapes,
    # including those with no model decision; future prices never reach the rater.
    cases_by_id={r.case_id:r for r in cohort().itertuples()}
    visual_prices=[]
    for r in reviewed[reviewed.vote.eq('yes')].itertuples():
        case=cases_by_id[r.case_id]
        tape=np.load(DATA/'tapes'/f'{r.case_id}.npy')
        mark=after_read(case,r.read_ms,tape)
        visual_prices.append(dict(key=r.key,visual_delay=(r.read_ms-case.known_ms)//MINUTE,
                                  **{f'visual_{k}':v for k,v in mark.items()}))
    if visual_prices:
        reviewed=reviewed.merge(pd.DataFrame(visual_prices),on='key',how='left',validate='one_to_one')
    reviewed.to_csv(HERE/'blind_reconciliation.csv',index=False)
    positives=reviewed[reviewed.vote.eq('yes')]
    shapes=positives.groupby(['case_id','tf']).agg(clear_candidates=('key','size'),matched=('matched','any'),
        extended=('late_or_matched','any'),overlap_two=('overlap_two','any')).reset_index()
    compared=reads.merge(shapes,how='left',on=['case_id','tf'],validate='one_to_one')
    for col in ('matched','extended','overlap_two'):compared[col]=compared[col].eq(True)
    compared['clear_candidates']=compared.clear_candidates.fillna(0).astype(int)
    compared['clear']=compared.clear_candidates.gt(0)
    cases=cohort().set_index('case_id')
    for k in ['wick_reclaim','asset_class','close_reclaim']:
        if k in cases:compared[k]=compared.case_id.map(cases[k])
    compared.to_csv(HERE/'case_comparison.csv',index=False)
    summary=compared.groupby(['role','tf']).agg(cases=('case_id','size'),any_yolo=('found','sum'),
        any_reference=('clear','sum'),same_core=('matched','sum'),same_core_extended=('extended','sum'),
        loose_match=('overlap_two','sum'),median_delay_min=('delay','median'),median_already_pct=('before_pct','median'),
        median_favorable24=('favorable24','median'),median_adverse24=('adverse24','median'),median_change24=('change24','median'))
    summary.to_csv(HERE/'summary.csv',float_format='%.6f')
    print(summary.to_string())
    print('votes',reviewed.groupby('vote').size().to_dict())
if __name__=='__main__':main()
