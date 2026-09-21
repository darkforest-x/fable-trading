"""Independent boundary, coverage, score-calibration and chart audit; no future sources."""
import argparse,hashlib,json
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def jl(p):return [json.loads(s) for s in p.read_text().splitlines() if s]
def main(plan_path):
    plan=json.loads(plan_path.read_text());exp=ROOT/plan['experiment_dir'];res=ROOT/plan['output_dir']
    raw=jl(res/'raw_candidates.jsonl');needed={r['source_path'] for r in raw}
    cutoff=pd.Timestamp(plan['scan_end_utc']);start=pd.Timestamp(plan['scan_start_utc']);frames={};counts=Counter();incomplete=[];gapped=[];short=[]
    for src in plan['sources']:
        p=ROOT/src['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==src['prefix_sha256']
        f=pd.read_csv(p);times=pd.to_datetime(f.ts,unit='ms',utc=True);dt=pd.Timedelta(minutes=src['bar_minutes'])
        assert len(f)>0 and times.is_monotonic_increasing and not times.duplicated().any()
        assert (times+dt<=cutoff).all();assert (times==pd.to_datetime(f.open_time,utc=True)).all()
        endpoints=(times+dt>=start)&(times+dt<=cutoff)
        expected=len(pd.date_range(start.ceil(f"{src['bar_minutes']}min"),cutoff.floor(f"{src['bar_minutes']}min"),freq=dt))
        actual=int(endpoints.sum());counts[src['bar_minutes']]+=1
        record={'symbol':src['symbol'],'minutes':src['bar_minutes'],'actual_endpoints':actual,'expected_endpoints':expected}
        if actual!=expected:incomplete.append(record)
        if not (times.diff().iloc[1:]==dt).all():gapped.append(record)
        if int((times+dt<start).sum())<120:short.append(record)
        if src['path'] in needed:frames[src['path']]=f
    scored=jl(res/'scored_candidates.jsonl');matches=jl(res/'grade_a_matches.jsonl');raw=jl(res/'raw_candidates.jsonl');cal=json.loads((res/'calibration.json').read_text())
    original=json.loads((ROOT/'experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/calibration.json').read_text())
    keys=['distance_scale','max_good_combined_distance','max_good_lockstep_distance','accepted_family_leave_one_out_p95','perfect_score_threshold','strong_score_threshold']
    diff={k:float(cal[k])-float(original[k]) for k in keys};assert max(abs(v) for v in diff.values())<1e-12
    for row in raw:
        f=frames[row['source_path']];c=int(row['source_core_end_i']);q=int(row['confirm_i']);m=int(row['bar_minutes'])
        close=pd.Timestamp(int(f.ts.iloc[q]),unit='ms',tz='UTC')+pd.Timedelta(minutes=m)
        assert q==c+5 and row['source_comparison_anchor_i']==c+2
        assert start<=close<=cutoff and close==pd.Timestamp(row['confirmation_close_utc'])
        assert pd.Timestamp(int(f.ts.iloc[c]),unit='ms',tz='UTC')==pd.Timestamp(row['core_end_time'])
        assert np.all(np.diff(f.ts.iloc[int(row['source_core_start_i'])-14:q+1])==m*60000)
    for row in matches:
        assert row['quality_tier']=='PERFECT_CANDIDATE' and row['hard_gate_pass'] and row['reference_gate_pass']
        assert row['quality_score']>=cal['perfect_score_threshold']
    comparisons={}
    comparisons['superseded_high_preflight']='results_high excluded exact-22:00 bar due epoch-ms rounding; final scan verifies complete endpoint counts independently'
    review=exp/'review'/'manifest.json'
    if review.exists():
        from PIL import Image
        manifest=json.loads(review.read_text());imagechecks=[]
        for card in manifest['cards']:
            p=exp/'review'/card['image'];assert hashlib.sha256(p.read_bytes()).hexdigest()==card['image_sha256'];assert pd.Timestamp(card['visible_end_close_utc'])<=cutoff
            assert Image.open(p).size==(1920,960);imagechecks.append(card['image'])
        comparisons['charts_verified']=len(imagechecks)
    result=dict(source_count=len(plan['sources']),source_counts_by_minutes=dict(counts),raw_candidate_checks=len(raw),strict_matches=len(matches),calibration_differences=diff,incomplete_target_coverage=incomplete,gapped_sources=gapped,insufficient_120_bar_warmup=short,future_bars_observed=0,comparisons=comparisons)
    (exp/'independent_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({**result,'incomplete_target_coverage':len(incomplete),'gapped_sources':len(gapped),'insufficient_120_bar_warmup':len(short)},ensure_ascii=False))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);main(p.parse_args().plan)
