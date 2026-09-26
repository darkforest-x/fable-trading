"""Blind sampling cannot condition on model output or overlap visible history."""
from datetime import datetime, timedelta

from yoyo.evaluation.ma_early_followup import coco_boxes, matching_score, sample_blind


def test_saved_coco_classes_are_one_based_and_boxes_are_xywh():
    records=[{'image_id':'fixture','category_id':2,'bbox':[10,20,30,40],'score':.9}]
    boxes=coco_boxes(records)['s_fixture']
    assert boxes==[{'class_id':1,'confidence':.9,'xyxy':[10,20,40,60]}]
    row={'class_id':1,'target_xyxy':[10,20,40,60]}
    assert matching_score(row,boxes)==.9
    assert matching_score(dict(row,class_id=0),boxes)==0
    assert matching_score(dict(row,class_id=None),boxes) is None


def test_blind_sampler_ignores_predictions_and_preserves_temporal_exclusions():
    cutoff=datetime.fromisoformat('2026-09-21T16:00:00+00:00')
    rows=[]
    for asset in range(12):
        for minutes in (15,30,60):
            for hour in (1,18,36,54):
                rows.append({'id':f'{asset}-{minutes}-{hour}','cohort':'market','n':17,
                    'symbol':f'asset{asset}','minutes':minutes,'decision_at_utc':(cutoff+timedelta(hours=hour)).isoformat()})
    plan={'blind_seed':'fixed','blind_visible_start_not_before':cutoff.isoformat(),'blind_per_timeframe':3}
    first=sample_blind(rows,plan)
    second=sample_blind([dict(r,prediction=not(i%2),future_return=i*100,class_id=i%2)
                        for i,r in enumerate(reversed(rows))],plan)
    assert [r['id'] for r in first]==[r['id'] for r in second]
    assert len(first)==9
    intervals={}
    for r in first:
        end=datetime.fromisoformat(r['decision_at_utc']);start=end-timedelta(minutes=r['minutes']*17)
        assert start>=cutoff
        previous=intervals.setdefault(r['symbol'],[])
        assert len(previous)<2
        assert not any(start<b and a<end for a,b in previous)
        previous.append((start,end))
