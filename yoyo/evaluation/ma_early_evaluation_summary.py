"""Summarize frozen replay predictions without turning observations into gold.

Object matches require the existing class and IoU>=0.5. First-hit latency is
credited only after the candidate's causally available rule anchor. Market
windows have no exhaustive human labels, so their alert counts are descriptive.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
from statistics import mean,median

from yoyo.evaluation.ma_early_replay import read_rows,write_json,iou
from yoyo.evaluation.ma_early_validation import sha,ROOT,PLAN


def matched(row,boxes):
    return row.get('target_xyxy') is not None and any(
        b['class_id']==row['class_id'] and iou(b['xyxy'],row['target_xyxy'])>=.5 for b in boxes)


def fraction(n,d):
    return n/d if d else None


def static_metrics(rows,predictions):
    tp=fp=fn=negative_alarms=negative_images=positive_images=0
    for r in rows:
        boxes=predictions[r['id']];hit=int(matched(r,boxes));positive=int(r['class_id'] is not None)
        tp+=hit;fp+=len(boxes)-hit;fn+=positive-hit;positive_images+=positive
        negative_images+=1-positive;negative_alarms+=int(not positive and bool(boxes))
    return {'images':len(rows),'positive_images':positive_images,'negative_images':negative_images,
        'tp':tp,'fp_boxes':fp,'fn':fn,'precision':fraction(tp,tp+fp),'recall':fraction(tp,tp+fn),
        'negative_images_with_alarms':negative_alarms,'negative_image_alarm_rate':fraction(negative_alarms,negative_images)}


def latency_events(rows,predictions):
    grouped=defaultdict(list)
    for r in rows:grouped[r['event_id']].append(r)
    events=[]
    for ident,group in sorted(grouped.items()):
        group=sorted(group,key=lambda r:(r['post'],r['n']));ref=group[0]['reference_post']
        hits=[r for r in group if matched(r,predictions[r['id']])]
        valid=[r for r in hits if r['post']>=ref];first=valid[0] if valid else None
        reference=next(r for r in group if r['post']==ref)
        side=1 if reference['class_id']==0 else -1
        events.append({'event_id':ident,'split':reference['split'],'symbol':reference['symbol'],
            'minutes':reference['minutes'],'class_id':reference['class_id'],'reference_post':ref,
            'first_any_match_post':min(r['post'] for r in hits) if hits else None,
            'first_valid_post':first['post'] if first else None,'first_valid_image':first['id'] if first else None,
            'delay_bars':first['post']-ref if first else None,
            'delay_minutes':(first['post']-ref)*reference['minutes'] if first else None,
            'pre_reference_match':any(r['post']<ref for r in hits),
            'matched_posts':sorted({r['post'] for r in hits}),
            'signed_price_move_from_reference_pct':side*(first['last_close']/reference['last_close']-1)*100 if first else None,
            'signed_price_move_from_core_pct':side*(first['last_close']/first['core_close']-1)*100 if first else None})
    return events


def latency_metrics(events):
    hit=[r for r in events if r['first_valid_post'] is not None]
    return {'events':len(events),'detected_by_core_plus8':len(hit),'missed_by_core_plus8':len(events)-len(hit),
        'recall_by_core_plus8':fraction(len(hit),len(events)),
        'at_reference':sum(r['delay_bars']==0 for r in hit),
        'within_reference_plus1':sum(r['delay_bars']<=1 for r in hit),
        'pre_reference_match_events':sum(r['pre_reference_match'] for r in events),
        'pre_reference_only_events':sum(r['pre_reference_match'] and r['first_valid_post'] is None for r in events),
        'never_matched_events':sum(r['first_any_match_post'] is None for r in events),
        'median_delay_bars_among_detected':median(r['delay_bars'] for r in hit) if hit else None,
        'mean_delay_bars_among_detected':mean(r['delay_bars'] for r in hit) if hit else None,
        'median_delay_minutes_among_detected':median(r['delay_minutes'] for r in hit) if hit else None,
        'median_core_to_first_hit_bars_among_detected':median(r['first_valid_post'] for r in hit) if hit else None,
        'median_signed_price_move_from_core_pct_among_detected':median(r['signed_price_move_from_core_pct'] for r in hit) if hit else None,
        'first_valid_post_histogram':dict(Counter(r['first_valid_post'] for r in hit)),
        'per_step_matching_events':{str(p):sum(p in r['matched_posts'] for r in events) for p in range(9)}}


def market_metrics(rows,predictions):
    endpoints=set();alarms=set();spans=Counter();post=Counter();boxes=windows=0;streams=set();tip_endpoints=set()
    for r in rows:
        key=(r['symbol'],r['minutes'],r['endpoint']);endpoints.add(key);streams.add(key[:2])
        bs=predictions[r['id']];windows+=bool(bs);boxes+=len(bs)
        if bs:alarms.add(key)
        for b in bs:
            bounds=[round((b['xyxy'][i]-r['left'])/r['plot_width']*(r['n']-1)+r['visible_start']) for i in (0,2)]
            distance=r['endpoint']-bounds[1]
            post[distance]+=1;spans[(r['symbol'],r['minutes'],b['class_id'],*bounds)]+=1
            if 0<=distance<=2:tip_endpoints.add(key)
    return {'windows':len(rows),'streams':len(streams),'closed_endpoints':len(endpoints),
        'windows_with_boxes':windows,'window_alarm_rate':fraction(windows,len(rows)),
        'endpoints_with_boxes':len(alarms),'endpoint_alarm_rate':fraction(len(alarms),len(endpoints)),
        'endpoints_with_predicted_core_within2_bars':len(tip_endpoints),
        'raw_boxes':boxes,'exact_distinct_predicted_core_spans':len(spans),
        'raw_boxes_per_exact_span':fraction(boxes,len(spans)),
        'predicted_core_distance_histogram':dict(sorted(post.items())),
        'false_positive_rate':None,'false_positive_rate_reason':'No exhaustive human-adjudicated labels in these market windows.'}


def summarize(inputs, scores, output):
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    plan=json.loads((ROOT/PLAN).read_text(encoding='utf-8'));build=json.loads((inputs/'build_receipt.json').read_text(encoding='utf-8'))
    score=json.loads((scores/'score_receipt.json').read_text(encoding='utf-8'))
    assert score['status']=='complete' and score['manifest_sha256']==sha(inputs/'manifest.jsonl')==build['manifest_sha256']
    rows=read_rows(inputs/'manifest.jsonl');ids={r['id'] for r in rows};assert len(ids)==len(rows)
    stat=[r for r in rows if r['cohort']=='static'];events=[r for r in rows if r['cohort']=='event'];market=[r for r in rows if r['cohort']=='market']
    result={'status':'diagnostics_complete_acceptance_not_granted','model_results':{},'source_hashes':{
        'manifest':sha(inputs/'manifest.jsonl'),'build_receipt':sha(inputs/'build_receipt.json'),'score_receipt':sha(scores/'score_receipt.json')},
        'limitations':['Positive anchors and negative labels are rule-selected research candidates, not individually adjudicated onset gold.',
        'Continuous market sample is a frozen same-day top-gainers retrospective cohort, not a causal trading universe.',
        'Main-event delay statistics are conditional on previously selected positive events and report censored misses explicitly.',
        'A valid-launch miss can still have an earlier core match; pre-reference-only and never-matched counts are separate.',
        'Old/new comparison uses identical early-model canvas and windows; this is not a native Grade-A production comparison.',
        'Only two test Grade-A challenge negatives; no robust challenge-test false alarm estimate.'],
        'production_eligible':False,'training_eligible':False,'economic_metrics':'not_applicable_to_detection_diagnostics'}
    by_model={}
    for name in plan['models']:
        path=scores/(name+'.jsonl');assert sha(path)==score['predictions_sha256'][name]
        rr=read_rows(path);pred={r['id']:r['boxes'] for r in rr};assert len(rr)==len(pred) and set(pred)==ids
        event_results=latency_events(events,pred);by_model[name]={r['event_id']:r for r in event_results}
        write_json(output/(name+'_event_latency.json'),event_results)
        negative_cases=[r['id'] for r in stat if r['class_id'] is None and pred[r['id']]]
        missing_cases=[r['id'] for r in stat if r['class_id'] is not None and not matched(r,pred[r['id']])]
        write_json(output/(name+'_review_candidates.json'),{'negative_alarms':negative_cases,'positive_misses':missing_cases})
        result['model_results'][name]={'static':{},'negative_strata':{},'latency':{},'market':market_metrics(market,pred)}
        for split in ('val','test'):
            for pool in ('reference','grade_a_challenge'):
                selection=[r for r in stat if r['split']==split and r['pool']==pool]
                result['model_results'][name]['static'][split+'_'+pool]=static_metrics(selection,pred)
            result['model_results'][name]['latency'][split]=latency_metrics([r for r in event_results if r['split']==split])
            for kind in sorted({r['negative_kind'] for r in stat if r['class_id'] is None}):
                result['model_results'][name]['negative_strata'][split+'_'+kind]=static_metrics(
                    [r for r in stat if r['split']==split and r['negative_kind']==kind],pred)
    a,b=by_model['early_v6'],by_model['old_v6a'];assert set(a)==set(b)
    paired=[(a[k],b[k]) for k in a if a[k]['first_valid_post'] is not None and b[k]['first_valid_post'] is not None]
    result['paired_latency']={'events':len(a),'both_detected':len(paired),
        'new_only_detected':sum(a[k]['first_valid_post'] is not None and b[k]['first_valid_post'] is None for k in a),
        'old_only_detected':sum(a[k]['first_valid_post'] is None and b[k]['first_valid_post'] is not None for k in a),
        'new_earlier':sum(x['delay_bars']<y['delay_bars'] for x,y in paired),
        'same_time':sum(x['delay_bars']==y['delay_bars'] for x,y in paired),
        'new_later':sum(x['delay_bars']>y['delay_bars'] for x,y in paired),
        'median_bars_saved_among_both_detected':median(y['delay_bars']-x['delay_bars'] for x,y in paired) if paired else None}
    templates={r['id']:[build['training_templates'][str(r['n'])]] for r in stat}
    result['position_template_null']={split:static_metrics([r for r in stat if r['split']==split],templates) for split in ('val','test')}
    write_json(output/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--scores',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.inputs.resolve(),a.scores.resolve(),a.output.resolve())
