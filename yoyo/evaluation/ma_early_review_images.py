"""Render phone-readable review figures from immutable scored input images.

These overlays are produced after inference. Predictions and reference boxes
are copied from the saved ledger, never inserted into model input pixels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from PIL import Image,ImageDraw,ImageFont

from yoyo.evaluation.ma_early_replay import read_rows,write_json
from yoyo.evaluation.ma_early_validation import sha


def font(size):
    return ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',size)


def panel(inputs,row,boxes,title):
    p=inputs/row['image_path'];assert sha(p)==row['image_sha256']
    image=Image.open(p).convert('RGB');draw=ImageDraw.Draw(image)
    target=row.get('target_xyxy')
    if target:
        draw.rectangle(target,outline='#ba9000',width=4)
    for b in boxes:
        xy=b['xyxy'];draw.rectangle(xy,outline='#e74b58',width=4)
        name=('多' if b['class_id']==0 else '空')+f" {b['confidence']:.2f}"
        draw.text((max(8,xy[0]),max(8,xy[1]-32)),name,font=font(25),fill='#c32b39')
    canvas=Image.new('RGB',(1280,836),'white');canvas.paste(image,(0,94));d=ImageDraw.Draw(canvas)
    d.text((20,12),title,font=font(28),fill='#17353c')
    stamp=row.get('decision_at_utc','冻结样本')
    detail=f"{row['symbol']} · {row['minutes']}分钟 · {stamp}"
    d.text((20,52),detail,font=font(19),fill='#5a6c71')
    return canvas


def build(inputs,scores,summary,out):
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    rows={r['id']:r for r in read_rows(inputs/'manifest.jsonl')}
    preds={n:{r['id']:r['boxes'] for r in read_rows(scores/(n+'.jsonl'))} for n in ('early_v6','old_v6a')}
    a=json.loads((summary/'early_v6_event_latency.json').read_text(encoding='utf-8'))
    b={r['event_id']:r for r in json.loads((summary/'old_v6a_event_latency.json').read_text(encoding='utf-8'))}
    pair=[r for r in a if r['split']=='test' and r['first_valid_post'] is not None and b[r['event_id']]['first_valid_post'] is not None
          and b[r['event_id']]['delay_bars']>r['delay_bars']]
    selections=[]
    if pair:
        middle=median(b[r['event_id']]['delay_bars']-r['delay_bars'] for r in pair)
        selected=min(pair,key=lambda r:(abs((b[r['event_id']]['delay_bars']-r['delay_bars'])-middle),r['event_id']))
        row=rows[selected['first_valid_image']];saved=b[selected['event_id']]['delay_bars']-selected['delay_bars']
        titles=[f"新模型 · 核心后{row['post']}根 · 此案例比旧模型早{saved}根",
                '旧 v6A · 完全相同的输入图']
        figures=[panel(inputs,row,preds[n][row['id']],title) for n,title in zip(('early_v6','old_v6a'),titles)]
        combined=Image.new('RGB',(1280,1728),'#eff4f5')
        for i,fig in enumerate(figures):combined.paste(fig,(0,i*846))
        d=ImageDraw.Draw(combined);d.text((20,1697),'红框＝模型原始输出；金框＝原核心参考。单例展示，整体统计另列。',font=font(19),fill='#435960')
        name='earlier_same_input.png';combined.save(out/name)
        selections.append({'type':'paired_earlier','file':name,'image_id':row['id'],'event_id':row['event_id'],
            'rule':'Test paired improvements, nearest median bars saved, event ID tie break','bars_saved':saved,'sha256':sha(out/name)})
    negative=[r for r in rows.values() if r['cohort']=='static' and r['split']=='test' and r['class_id'] is None and preds['early_v6'][r['id']]]
    if negative:
        row=min(negative,key=lambda r:(-max(b['confidence'] for b in preds['early_v6'][r['id']]),r['id']))
        name='negative_alarm.png';fig=panel(inputs,row,preds['early_v6'][row['id']],
            '待复核失败例 · 测试集规则负样本上的最高置信度告警')
        fig.save(out/name);selections.append({'type':'negative_alarm','file':name,'image_id':row['id'],
            'rule':'Highest-confidence new-model test empty-label alarm; not selected by price outcome','sha256':sha(out/name)})
    missed=[r for r in a if r['split']=='test' and r['first_any_match_post'] is None]
    if missed:
        event=min(missed,key=lambda r:r['event_id'])
        row=min((r for r in rows.values() if r['cohort']=='event' and r['event_id']==event['event_id'] and r['post']==r['reference_post']),key=lambda r:(abs(r['n']-14),r['id']))
        name='missed_event.png';fig=panel(inputs,row,preds['early_v6'][row['id']],
            '待复核漏检例 · 此事件直到核心后8根仍未匹配原核心')
        fig.save(out/name);selections.append({'type':'missed_event','file':name,'image_id':row['id'],
            'rule':'First test censored miss by stable event ID, shown at reference observation','sha256':sha(out/name)})
    write_json(out/'selection.json',{'source_manifest_sha256':sha(inputs/'manifest.jsonl'),
        'summary_sha256':sha(summary/'summary.json'),'items':selections,'raw_input_modified':False})
    print(json.dumps(selections,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('inputs','scores','summary','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();build(a.inputs.resolve(),a.scores.resolve(),a.summary.resolve(),a.output.resolve())
