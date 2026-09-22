"""Build a deterministic review sheet; annotated pixels never enter training."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from PIL import Image, ImageDraw
from yoyo.datasets.ma_profit_negative_redo import ROOT, rows, write_json, sha256

EXP=ROOT/'experiments/active/exp-ma-profit3r-negatives-20260922-v2'
DATA=ROOT/'datasets/ma_profit3r_owner1500_neg_v5'


def main() -> None:
    manifest=rows(DATA/'manifest.jsonl');groups=defaultdict(list)
    for r in manifest:
        if r['split']=='train' and r['variant']=='A':
            groups[(r['class_id'] is not None,r['direction'],r['bar_minutes'])].append(r)
    chosen=[]
    for key in sorted(groups,key=lambda k:(not k[0],k[2],k[1])):
        chosen.append(sorted(groups[key],key=lambda r:r['event_id'])[0])
    # Include B1/B2 views of both directions at the same c+5 endpoint.
    anchors=[r for r in chosen if r['class_id'] is None and r['bar_minutes']==1]
    for anchor in anchors:
        chosen.extend(sorted([r for r in manifest if r['event_id']==anchor['event_id'] and r['variant']!='A'],key=lambda r:r['variant']))
    out=EXP/'visual_review'
    if out.exists():raise FileExistsError(out)
    out.mkdir()
    width,thumb_h,title_h=400,232,40
    canvas=Image.new('RGB',(width*4,(thumb_h+title_h)*((len(chosen)+3)//4)), '#e5e7eb')
    draw=ImageDraw.Draw(canvas)
    for i,r in enumerate(chosen):
        im=Image.open(DATA/r['image_path']).convert('RGB')
        box=r.get('box') or r.get('candidate_box')
        if box:
            d=ImageDraw.Draw(im);d.rectangle((box['x0'],box['y0'],box['x1'],box['y1']),outline='#d946ef',width=3)
        im.thumbnail((width,thumb_h),Image.Resampling.LANCZOS)
        x=(i%4)*width;y=(i//4)*(thumb_h+title_h)
        kind='P' if r['class_id'] is not None else 'N '+r['profit']['outcome']
        title=f"{kind} {r['direction']} {r['bar_minutes']}m {r['variant']} {r['core_end_time'][:10]}"
        draw.text((x+5,y+3),title,fill='black')
        draw.text((x+5,y+19),r['event_id'][-18:],fill='black')
        canvas.paste(im,(x+(width-im.width)//2,y+title_h))
    canvas.save(out/'contact_sheet.png')
    write_json(out/'selection.json',{'note':'Review-only magenta core/candidate boxes; no overlay in training pixels. Negative means unmet3R target, not no morphology.','manifest_sha256':sha256(DATA/'manifest.jsonl'),'samples':chosen,'contact_sheet_sha256':sha256(out/'contact_sheet.png')})
    print(out/'contact_sheet.png')


if __name__=='__main__':main()
