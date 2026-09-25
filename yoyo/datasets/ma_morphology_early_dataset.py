"""Build the explicitly authorized v6 low-latency research dataset.

Positive timing is the first closed post-core candle outside the original core
in its inherited direction and beyond all six displayed MAs (within old post5).
This uses open_time/OHLC and HL2 SMA/EMA20/60/120 up to that close only. Three
left contexts share this earliest time; no future bars are appended for layout.
Labels remain rule candidates, not newly certified per-sample Owner gold.
Grade-A negatives retain their old no-launch provenance and are additionally
screened against observable dense launches in their earlier visible prefix.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as render
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_morphology_future_review import label_coordinates, transform
from yoyo.datasets.ma_morphology_positions import _load_source_group, event_support, RECOVERY
from yoyo.datasets.ma_morphology_redo import read_interval, rows, sha, utc, write_json, write_rows

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-ma-morphology-v6-threeview-20260925-v1'
PLAN=EXP/'early_build_plan_20260926.json'


def earliest_launch(frame: pd.DataFrame, row: dict) -> tuple[int | None,list[dict]]:
    """Read only core+1..5 closed prefixes; no 3R/outcome value selects timing."""
    a,b=1211,1210+int(row['core_bars'])
    core=frame.iloc[a:b+1]
    enriched=render.add_hl2_mas(frame.iloc[:b+6])
    evidence=[]
    for post in range(1,6):
        last=enriched.iloc[b+post];mas=last[list(render.SIX_MA_COLUMNS)].to_numpy(dtype=float)
        long=int(row['class_id'])==0
        outside=float(last.close)>float(core.high.max()) if long else float(last.close)<float(core.low.min())
        beyond=float(last.close)>float(mas.max()) if long else float(last.close)<float(mas.min())
        evidence.append({'post':post,'outside_original_core':bool(outside),'beyond_all_six_mas':bool(beyond)})
        if outside and beyond:return post,evidence
    return None,evidence


def early_negative_screen(frame: pd.DataFrame, row: dict, post: int, hard: dict) -> dict:
    """Causal extra veto, with unchanged Grade-A density bounds and ATR14.

    Only pre11/core/post and 1200 warmup bars are used. A core's ATR is its
    last already-known ATR (not legacy core+2). An observable directional
    close beyond both its wick bounds and six MAs vetoes an empty label.
    This conservative veto does not claim manual semantic adjudication.
    """
    end=1210+int(row['core_bars'])+post
    enriched=add_candidate_features(frame.iloc[:end+1].copy())
    ambiguous=[]
    for basis,df in [('close',enriched),('hl2',render.add_hl2_mas(enriched))]:
        mas=df[list(render.SIX_MA_COLUMNS)].to_numpy(dtype=float)
        for n in (4,5):
            for b in range(1200+n-1,end):
                core=df.iloc[b-n+1:b+1];atr=float(df.iloc[b].atr)
                if not np.isfinite(atr) or atr<=0:raise ValueError('Invalid causal ATR')
                m=mas[b-n+1:b+1]
                density=((m.max()-m.min())/atr<=hard['ma_envelope_atr_max']
                    and (mas[b].max()-mas[b].min())/atr<=hard['ma_spread_end_atr_max']
                    and float((core.close-core.open).abs().max())/atr<=hard['max_body_atr_max']
                    and float(core.high.max()-core.low.min())/atr<=hard['candle_envelope_atr_max']
                    and float(np.abs(core.close.to_numpy()[:,None]-m).min())/atr<=hard['minimum_close_to_ma_atr_max'])
                if not density:continue
                for t in range(b+1,end+1):
                    c=float(df.iloc[t].close)
                    if ((c>float(core.high.max()) and c>mas[t].max())
                            or (c<float(core.low.min()) and c<mas[t].min())):
                        ambiguous.append({'basis':basis,'core_end_visible':b-1200,'observed_visible':t-1200,'core_bars':n})
                        break
    return {'accepted':not ambiguous,'ambiguous':ambiguous,'uses_future_after_observation':False,
            'density_thresholds':hard,'prior_label_status':'inherited_screened_rule_negative_not_manual_gold'}


def render_views(frame: pd.DataFrame,row: dict,original_label: str,post: int) -> dict:
    """Keep original physical core and fully visible edge candles in all views."""
    a,b=1211,1210+int(row['core_bars'])
    old,_,_=render._window_asset(frame,core_start_i=a,core_end_i=b,pre_bars=9,post_bars=5,
        support_start_i=0,price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
    if hashlib.sha256(old).hexdigest()!=row['image_sha256']:raise ValueError('Original P9 replay mismatch')
    old_visible=render.add_hl2_mas(frame.iloc[:b+6]).iloc[a-9:].reset_index(drop=True)
    actual=label_coordinates(original_label,transform(old_visible,1280,742))
    return render_known_prefixes(frame,row,actual,post)


def render_known_prefixes(frame: pd.DataFrame,row: dict,actual: dict | None,post: int) -> dict:
    """Render causal pixels against a previously frozen physical annotation."""
    a,b=1211,1210+int(row['core_bars'])
    if type(post) is not int or not 0<=post<=5:raise ValueError('Invalid early post')
    enriched=render.add_hl2_mas(frame.iloc[:b+post+1])
    views={}
    for pre in (7,9,11):
        visible=enriched.iloc[a-pre:].reset_index(drop=True);n=len(visible)
        tf=transform(visible,1280,742)
        # Half a candle slot on BOTH sides: containment, not position augmentation.
        slot=tf.plot_w/n
        tf=replace(tf,left=tf.left+slot/2,plot_w=slot*(n-1),candle_half_w=max(1,int(slot*.34)))
        image,_=render.chart_render.render_chart(visible,width=1280,height=742,fixed_transform=tf)
        image=render._recolor_candles(image)
        ok,png=cv2.imencode('.png',image,[cv2.IMWRITE_PNG_COMPRESSION,3])
        if not ok:raise ValueError('PNG encoding failed')
        label='';box=None
        if actual:
            left,right=actual['bar_left']-9+pre,actual['bar_right']-9+pre
            x0,x1=[tf.left+v/(n-1)*tf.plot_w for v in (left,right)]
            y0,y1=[tf.top+(tf.price_max-v)/(tf.price_max-tf.price_min)*tf.plot_h for v in (actual['price_high'],actual['price_low'])]
            if not (0<=x0<x1<=1280 and 0<=y0<y1<=742):raise ValueError('Unclipped original box does not fit')
            label=f"{row['class_id']} {(x0+x1)/2560:.8f} {(y0+y1)/1484:.8f} {(x1-x0)/1280:.8f} {(y1-y0)/742:.8f}\n"
            box={'core_relative_left':actual['bar_left']-9,'core_relative_right':actual['bar_right']-9,
                'bar_left':left,'bar_right':right,'price_high':actual['price_high'],'price_low':actual['price_low'],
                'pixel_box':[x0,y0,x1,y1]}
        step=pd.Timedelta(minutes=int(row['bar_minutes']))
        views[f'P{pre}']={'png':png.tobytes(),'label':label,'box':box,'pre_bars':pre,'post_bars':post,'visible_bars':n,
            'chart_x_left':tf.left,'chart_plot_w':tf.plot_w,'candle_half_w':tf.candle_half_w,
            'visible_start_utc':visible.open_time.iloc[0].isoformat(),
            'decision_at_utc':(visible.open_time.iloc[-1]+step).isoformat()}
    return views


def process_group(job):
    source_path,group,recovery,parent,hard,posts=job
    _,_,frame=_load_source_group((source_path,group));result=[];parent=Path(parent)
    for row in group:
        recovered=None
        if frame is None:
            recovered=recovery[row['event_id']];path=ROOT/recovered['path']
            if sha(path)!=recovered['sha256'] or recovered['full_csv_sha256_verified']!=row['source_sha256']:raise ValueError('Recovery identity drift')
            step=pd.Timedelta(minutes=int(row['bar_minutes']))
            current=read_interval(path,utc(row['core_start_time'])-1211*step,utc(row['core_end_time'])+12*step)
        else:current=frame
        support=event_support(current,row)
        evidence=None
        if row['sample_kind']=='positive':post,evidence=earliest_launch(support,row)
        else:
            post=posts[row['event_id']]
            evidence=early_negative_screen(support,row,post,hard)
            if not evidence['accepted']:post=None
        if post is None:
            result.append((row,None,evidence,None,recovered));continue
        for kind in ('image','label'):
            if sha(parent/row[kind+'_path'])!=row[kind+'_sha256']:raise ValueError('Parent asset drift')
        views=render_views(support,row,(parent/row['label_path']).read_text(),post)
        result.append((row,post,evidence,views,recovered))
    return result


def build(plan_path: Path, output: Path, pilot: bool=False) -> dict:
    plan=json.loads(plan_path.read_text())
    commit=render._committed([Path(__file__),plan_path,ROOT/'yoyo/datasets/ma_profit_dataset.py',
        ROOT/'yoyo/datasets/ma_morphology_positions.py',ROOT/'yoyo/datasets/ma_morphology_future_review.py',
        ROOT/'yoyo/datasets/fifteen_minute_launch_candidates.py',ROOT/'yoyo/layers/l1_detection/render.py'])
    for binding in plan['inputs'].values():
        if sha(ROOT/binding['path'])!=binding['sha256']:raise ValueError('Plan input drift')
    parent=ROOT/plan['parent_dataset']
    selected={r['event_id']:dict(r) for r in rows(parent/'manifest.jsonl') if r['variant']=='P9'}
    ledger={r['event_id']:r for r in rows(ROOT/plan['inputs']['parent_ledger']['path'])}
    for row in selected.values():
        if row.get('core_start_time') is None:
            old=ledger[row['event_id']];row.update(core_start_time=old['core_start_time'],core_bars=old['core_bars'])
    if pilot:
        ids=set(plan['review_event_ids'])
        for split in ('val','test'):
            for kind in ('positive','negative'):
                ids.update(sorted(r['event_id'] for r in selected.values() if r['split']==split and r['sample_kind']==kind)[:3])
        selected={k:v for k,v in selected.items() if k in ids}
    recovery=json.loads(RECOVERY.read_text())['events']
    hard=json.loads((ROOT/plan['inputs']['grade_protocol']['path']).read_text())['negative_sampling']['hard_definition']
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    manifest=[];excluded=[];screens=[];post_pools=defaultdict(list)
    for kind in ('positive','negative'):
        group=defaultdict(list);posts={}
        for row in selected.values():
            if row['sample_kind']!=kind:continue
            group[row['source_path']].append(row)
        if kind=='negative':
            for row in selected.values():
                if row['sample_kind']!=kind:continue
                pool=post_pools[row['split']]
                if not pool:raise ValueError('No positive timing pool for split')
                slot=int(hashlib.sha256(('negative-post-v1:'+row['event_id']).encode()).hexdigest(),16)%len(pool)
                posts[row['event_id']]=sorted(pool)[slot]
        jobs=[(path,sorted(g,key=lambda r:r['event_id']),{r['event_id']:recovery[r['event_id']] for r in g if r['event_id'] in recovery},str(parent),hard,{r['event_id']:posts[r['event_id']] for r in g if r['event_id'] in posts}) for path,g in sorted(group.items())]
        with ProcessPoolExecutor(max_workers=3) as executor:
            pending=deque();iterator=iter(jobs)
            for _ in range(3):
                job=next(iterator,None)
                if job is not None:pending.append(executor.submit(process_group,job))
            number=0
            while pending:
                processed=pending.popleft().result();number+=1
                job=next(iterator,None)
                if job is not None:pending.append(executor.submit(process_group,job))
                for row,post,evidence,views,recovered in processed:
                    ident=row['event_id'];screens.append({'event_id':ident,'post_bars':post,'evidence':evidence})
                    if views is None:
                        excluded.append({'event_id':ident,'sample_kind':kind,'split':row['split'],
                            'reason':'no_visible_directional_launch_by_old_endpoint' if kind=='positive' else 'early_visible_dense_launch_conflicts_with_empty_label'})
                        continue
                    if kind=='positive':post_pools[row['split']].append(post)
                    for variant,v in views.items():
                        stem=render.asset_stem(ident,variant)
                        ip,lp=f"images/{row['split']}/{stem}.png",f"labels/{row['split']}/{stem}.txt"
                        for file,content in [(ip,v['png']),(lp,v['label'].encode())]:
                            dest=output/file;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
                        entry={k:value for k,value in row.items() if k not in ('box','label_horizon_end_utc')}
                        entry.update({k:value for k,value in v.items() if k not in ('png','label')})
                        entry.update(variant=variant,visible_end_close_time_utc=v['decision_at_utc'],
                            image_path=ip,label_path=lp,image_sha256=sha(output/ip),label_sha256=sha(output/lp),
                            original_image_sha256=row['image_sha256'],original_label_sha256=row['label_sha256'],
                            original_decision_at_utc=row['decision_at_utc'],source_replay_sha_matches=True,
                            parent_label_horizon_end_utc=row.get('label_horizon_end_utc'),
                            geometry_source='actual_P9_TXT_bar_price_projection_full_edge_candle_slot',
                            early_label_status='owner_authorized_rule_candidate_not_individual_gold',
                            earliest_launch_post=post if kind=='positive' else None,
                            negative_early_screen_accepted=True if kind=='negative' else None,
                            source_recovery_path=recovered['path'] if recovered else None,
                            source_recovery_sha256=recovered['sha256'] if recovered else None,
                            training_eligible=False,production_eligible=False)
                        manifest.append(entry)
                if number%10==0 or number==len(jobs):print(json.dumps({'kind':kind,'groups':number,'total':len(jobs),'images':len(manifest),'excluded':len(excluded)}),flush=True)
    write_rows(output/'manifest.jsonl',manifest);write_rows(output/'exclusions.jsonl',excluded);write_rows(output/'screening.jsonl',screens)
    counts=Counter((r['split'],r['sample_kind']) for r in manifest)
    receipt={'source_commit':commit,'plan_sha256':sha(plan_path),'manifest_sha256':sha(output/'manifest.jsonl'),
        'dataset_ready':not pilot,'pilot':pilot,'early_contract':'case_first_visible_launch_v1',
        'screening_sha256':sha(output/'screening.jsonl'),
        'train_positive_images':counts['train','positive'],'train_negative_images':counts['train','negative'],
        'images':len(manifest),'events':len({r['event_id'] for r in manifest}),
        'positive_events':len({r['event_id'] for r in manifest if r['sample_kind']=='positive'}),
        'negative_events':len({r['event_id'] for r in manifest if r['sample_kind']=='negative'}),
        'counts':dict(Counter(f"{r['split']}:{r['sample_kind']}:{r.get('negative_kind','positive')}" for r in manifest)),
        'post_counts_by_kind':dict(Counter(f"{r['split']}:{r['sample_kind']}:{r['post_bars']}" for r in manifest if r['variant']=='P9')),
        'expected_main_eval_counts':{s:sum(r['split']==s and r['variant']=='P9' and r['evaluation_pool']=='reference' for r in manifest) for s in ('val','test')},
        'excluded_events':len(excluded),'training_eligible':False,'production_eligible':False}
    write_json(output/'build_receipt.json',receipt);return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,default=PLAN)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--pilot',action='store_true')
    a=p.parse_args();print(json.dumps(build(a.plan,a.output,a.pilot),ensure_ascii=False,indent=2))
