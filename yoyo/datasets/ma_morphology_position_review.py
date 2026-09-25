"""Display the same ten positive events before/after real position correction.

Review overlays are separate from model PNGs. Future context is the original
forty-bar review extension; every new crop displays its own availability time.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw

from yoyo.datasets.ma_morphology_positions import ROOT, EXP, VIEWS, render_positions
from yoyo.datasets.ma_morphology_future_review import font, review_assets
from yoyo.datasets.ma_morphology_redo import rows, read_interval, sha, utc, write_json
from yoyo.datasets import ma_profit_dataset as renderer


def build(output: Path):
    commit=renderer._committed([Path(__file__),ROOT/'yoyo/datasets/ma_morphology_positions.py'])
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    selection=json.loads((EXP/'review20_selection_v2.json').read_text())
    parent=ROOT/'datasets/ma_launch_owner1500_morph_v6_threeview_ready_20260925_v1'
    lookup={r['event_id']:r for r in rows(parent/'manifest.jsonl') if r['variant']=='P9'}
    recovery=json.loads((ROOT/'data/crypto/research/ma_morphology_future_review20_20260925/raw_review_only/recovery_manifest.json').read_text())['events']
    cards=[];receipt=[]
    for item in selection['items']:
        if item['sample']['class_id'] is None:continue
        ident=item['review_id'];r=lookup[item['sample']['event_id']];step=pd.Timedelta(minutes=int(r['bar_minutes']))
        path=ROOT/r['source_path']
        if path.is_file():
            if sha(path)!=r['source_sha256']:raise ValueError('Source SHA drift')
        else:
            recovered=recovery[r['event_id']]
            if recovered['full_csv_sha256_verified']!=r['source_sha256']:raise ValueError('Recovered identity drift')
            path=ROOT/recovered['path']
            if sha(path)!=recovered['sha256']:raise ValueError('Recovery bytes drift')
        frame=read_interval(path,utc(r['core_start_time'])-1211*step,utc(r['core_end_time'])+46*step)
        label=(parent/r['label_path']).read_text()
        views=render_positions(frame,r,label)
        future=review_assets(frame,r,label)['image'];future.save(output/f'{ident}_future.png')
        strip=Image.new('RGB',(1920,480),'white');draw=ImageDraw.Draw(strip)
        cells=[]
        for col,(variant,v) in enumerate(views.items()):
            im=Image.open(io.BytesIO(v['png'])).convert('RGB')
            ImageDraw.Draw(im).rectangle(v['box']['pixel_box'],outline='#e32942',width=4)
            im.save(output/f'{ident}_{variant}.png')
            clock=utc(v['decision_at_utc']).tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M')
            title=f"{variant} · 前{v['pre_bars']}根 / 后{v['post_bars']}根"
            draw.text((col*640+18,8),title,font=font(24),fill='#183a39')
            draw.text((col*640+18,44),f'画面截至 {clock} 北京时间',font=font(19),fill='#557572')
            strip.paste(im.resize((640,371)),(col*640,95))
            cells.append(f'<div><h3>{title}</h3><p>画面截至 {clock} 北京时间</p><a href="{ident}_{variant}.png" target="_blank"><img src="{ident}_{variant}.png"></a></div>')
        strip.save(output/f'{ident}_positions.png')
        title=f"{ident} · {r['canonical_asset']} · {r['bar_minutes']} 分钟 · {'多头' if r['class_id']==0 else '空头'}"
        cards.append(f'<article><h2>{title}</h2><div class="views">'+''.join(cells)+f'</div><details><summary>展开同一事件的未来走势（仅供审核）</summary><p>红框是同一原标签；蓝线是原后5根截止，之后额外展示40根。整张未来审核图不参与训练；上面三图各自只使用标明截止时间之前的 K 线。</p><a href="{ident}_future.png" target="_blank"><img src="{ident}_future.png"></a></details></article>')
        receipt.append({'review_id':ident,'event_id':r['event_id'],'original_label_sha256':r['label_sha256'],
            'views':{k:{x:v[x] for x in ('pre_bars','post_bars','visible_bars','decision_at_utc','box')} for k,v in views.items()}})
    html='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPIKE · 三种真实位置对照</title><style>body{margin:0;background:#eff4f4;color:#233b3a;font-family:system-ui,sans-serif}main{max-width:1700px;margin:auto;padding:24px}p{color:#57706f;line-height:1.7}article{background:white;border:1px solid #d5e2df;border-radius:16px;padding:18px;margin:24px 0}.views{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}img{width:100%;height:auto}h3{margin-bottom:3px}summary{cursor:pointer;color:#167867;padding:16px 0}strong{color:#bb334b}@media(max-width:800px){.views{grid-template-columns:1fr}}</style><main><h1>同一形态，三种真实位置</h1><p>复用刚才 P01–P10 的同一批事件。每行从靠右、居中到靠左：<strong>11前/5后 → 8前/8后 → 5前/11后</strong>。同一行 K 线数量相同，红框均来自原训练 TXT，框对应的核心与价格边界不变。</p><p>不同窗口右端意味着不同可见时间；这些图用于延迟形态识别。模型输入没有红框、标题或未来审核图。</p>'''+''.join(cards)+'</main></html>'
    (output/'index.html').write_text(html)
    write_json(output/'receipt.json',{'source_commit':commit,'review_only':True,'training_eligible':False,'items':receipt,
        'files':{p.name:sha(p) for p in output.glob('*.png')}})
    print(json.dumps({'output':str(output),'events':len(receipt)}))


if __name__=='__main__':build(EXP/'position_review_v1')
