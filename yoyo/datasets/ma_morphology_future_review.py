"""Show frozen training labels beside forty future bars for Owner review only.

The training PNG and YOLO TXT are read-only. The causal crop is replayed from
the same 1,200-bar HL2 warmup and must match its original PNG SHA. A YOLO box
is inverted through that crop's pixel transform into bar/price coordinates,
then projected into the expanded chart; it is never guessed or re-labelled.
Future OHLC changes only separate review charts, not the crop or its box.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import html
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from yoyo.datasets import ma_profit_dataset as dataset_render
from yoyo.datasets.ma_morphology_redo import read_interval, rows, sha, utc, write_json
from yoyo.layers.l1_detection import render

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ma-morphology-v6-threeview-20260925-v1'
SELECTION = EXP / 'review20_selection_v2.json'
RECOVERY = ROOT / 'data/crypto/research/ma_morphology_future_review20_20260925/raw_review_only/recovery_manifest.json'
WIDTH, HEIGHT = 1800, 740


def font(size: int):
    return ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', size)


def transform(frame: pd.DataFrame, width: int, height: int):
    """Use the frozen visible-range rule, including its flat-view fallback."""
    values = pd.concat([frame.low, frame.high, *(frame[c] for c in render.ALL_MA_COLS)]).dropna()
    lo, hi = float(values.min()), float(values.max())
    span = hi - lo
    if span == 0:
        span = max(abs(lo) * 1e-6, 1e-9)
        lo, hi = lo - span/2, hi + span/2
    return replace(render.make_chart_transform(frame, width=width, height=height),
                   price_min=lo - span*.06, price_max=hi + span*.06)


def label_coordinates(label: str, tf) -> dict | None:
    """Invert the ACTUAL normalized TXT box, preserving pixel quantization."""
    if not label.strip():
        return None
    cls, cx, cy, w, h = map(float, label.split())
    if cls not in (0, 1) or w <= 0 or h <= 0:
        raise ValueError('Invalid source label')
    x0, x1 = (cx-w/2)*tf.width, (cx+w/2)*tf.width
    y0, y1 = (cy-h/2)*tf.height, (cy+h/2)*tf.height
    if not (0 <= x0 < x1 <= tf.width and 0 <= y0 < y1 <= tf.height):
        raise ValueError('Source label outside image')
    return {'class_id': int(cls), 'pixel_box': [x0, y0, x1, y1],
            'bar_left': (x0-tf.left)/tf.plot_w*(tf.n_bars-1),
            'bar_right': (x1-tf.left)/tf.plot_w*(tf.n_bars-1),
            'price_high': tf.price_max-(y0-tf.top)/tf.plot_h*(tf.price_max-tf.price_min),
            'price_low': tf.price_max-(y1-tf.top)/tf.plot_h*(tf.price_max-tf.price_min)}


def review_assets(frame: pd.DataFrame, row: dict, label: str, future_bars: int = 40) -> dict:
    """Read OHLC from core-1211 through decision+40 closed bars, no later rows."""
    step = pd.Timedelta(minutes=int(row['bar_minutes']))
    support_start = utc(row['core_start_time']) - 1211*step
    decision = utc(row['decision_at_utc'])
    frame = frame.loc[(frame.open_time >= support_start) & (frame.open_time < decision + future_bars*step)].reset_index(drop=True)
    core_start, core_end = 1211, 1210+int(row['core_bars'])
    training_end = core_end+5
    if len(frame) != training_end+1+future_bars or not frame.open_time.diff().iloc[1:].eq(step).all():
        raise ValueError('Incomplete/gapped support or future context: '+row['event_id'])
    if frame.open_time.iloc[core_start] != utc(row['core_start_time']) or frame.open_time.iloc[core_end] != utc(row['core_end_time']):
        raise ValueError('Core clock drift')
    if frame.open_time.iloc[training_end]+step != decision:
        raise ValueError('Training cutoff drift')
    pre = int(row['variant'][1:])
    png, _, _ = dataset_render._window_asset(frame, core_start_i=core_start, core_end_i=core_end,
        pre_bars=pre, post_bars=5, support_start_i=0, price_scale=dataset_render.VISIBLE_RANGE_PRICE_SCALE)
    if hashlib.sha256(png).hexdigest() != row['image_sha256']:
        raise ValueError('Training crop replay SHA mismatch: '+row['event_id'])
    # Prefix computation is frozen before the review future is added.
    causal = dataset_render.add_hl2_mas(frame.iloc[:training_end+1])
    training = causal.iloc[core_start-pre:].reset_index(drop=True)
    tf = transform(training, 1280, 742)
    box = label_coordinates(label, tf)
    if (box is None) != (row['class_id'] is None) or (box and box['class_id'] != row['class_id']):
        raise ValueError('Label/class mismatch')
    expanded = dataset_render.add_hl2_mas(frame).iloc[core_start-pre:].reset_index(drop=True)
    future_tf = transform(expanded, WIDTH, HEIGHT)
    pixels, _ = render.render_chart(expanded, width=WIDTH, height=HEIGHT, fixed_transform=future_tf)
    pixels = dataset_render._recolor_candles(pixels)
    image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB))
    cutoff = future_tf.x_at(len(training)-.5)
    projected = None
    if box:
        projected = [future_tf.x_at(box['bar_left']), future_tf.y_at(box['price_high']),
                     future_tf.x_at(box['bar_right']), future_tf.y_at(box['price_low'])]
        if projected[0] >= projected[2] or projected[1] >= projected[3] or projected[2] >= cutoff:
            raise ValueError('Projected box lost its geometry or entered future')
        ImageDraw.Draw(image).rectangle(projected, outline='#e32942', width=5)
    draw = ImageDraw.Draw(image)
    for y in range(0, HEIGHT, 18):
        draw.line((cutoff,y,cutoff,min(y+10,HEIGHT)), fill='#1673c9', width=3)
    return {'image': image, 'box': box, 'projected_box': projected, 'cutoff_x': cutoff,
            'training_bars': len(training), 'review_bars': len(expanded),
            'review_start_utc': expanded.open_time.iloc[0].isoformat(),
            'review_end_utc': (expanded.open_time.iloc[-1]+step).isoformat()}


def type_name(row: dict) -> str:
    if row['class_id'] is not None:
        return '正样本 · '+('多头' if row['class_id']==0 else '空头')
    return {'grade_a_hard':'负样本 · 困难反例', 'grade_a_easy':'负样本 · Grade-A 普通背景',
            'whole_view_non_dense':'负样本 · 原 v6 非密集背景'}[row['negative_kind']]


def resolve_geometry(row: dict, ledger: dict) -> dict:
    """Legacy B1/B2 backgrounds omitted core fields; join their frozen ledger."""
    if row.get('core_start_time') is not None and row.get('core_bars') is not None:
        return row
    event = ledger[row['event_id']]
    for key in ('core_end_time','bar_minutes','source_sha256'):
        if event[key] != row[key]: raise ValueError('Legacy ledger geometry identity drift')
    return {**row,'core_start_time':event['core_start_time'],'core_bars':event['core_bars']}


def draw_review(item: dict, assets: dict, target: Path) -> None:
    row = item['sample']
    canvas = Image.new('RGB', (WIDTH, HEIGHT+180), '#ffffff')
    draw = ImageDraw.Draw(canvas)
    title = f"{item['review_id']}  {row['canonical_asset']} · {row['bar_minutes']} 分钟  |  {type_name(row)}"
    draw.text((24,12), title, font=font(30), fill='#172e36')
    date = utc(row['core_start_time']).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
    detail = f"核心开始 {date} 北京时间  ·  训练视图 {row['variant']}  ·  同一原图延伸未来 40 根"
    draw.text((24,58),detail,font=font(22),fill='#637982')
    cutoff = assets['cutoff_x']
    draw.text((24,95),'原训练可见范围',font=font(21),fill='#1673c9')
    draw.text((cutoff+16,95),'蓝线右侧：后来发生的走势，仅供人工审核',font=font(21),fill='#637982')
    canvas.paste(assets['image'],(0,132))
    note = '红框＝实际 YOLO 标签对应的核心范围；按原训练像素坐标换算，不是重新画的新标签。' if assets['box'] else '原训练标签为空，所以不画目标框。蓝线右侧之后是否启动，不会倒改这张训练图的标签。'
    draw.text((24,HEIGHT+142),note,font=font(21),fill='#637982')
    canvas.save(target)


def gallery(out: Path, records: list[dict]) -> None:
    cards=[]
    for r in records:
        kind = 'positive' if r['class_id'] is not None else 'negative'
        cards.append(f'''<article data-kind="{kind}" id="{r['review_id']}"><div class="title"><h2>{r['review_id']} · {html.escape(r['asset'])} · {r['bar_minutes']} 分钟</h2><span>{html.escape(r['type_name'])}</span></div><a href="{r['future_image']}" target="_blank"><img loading="lazy" src="{r['future_image']}" alt="{r['review_id']} 带未来走势与原框的审核图"></a><details><summary>查看原训练图{'及实际标签框' if kind=='positive' else '（空标签）'}</summary><a href="{r['training_overlay']}" target="_blank"><img loading="lazy" src="{r['training_overlay']}" alt="原训练图标签"></a><p>事件：{html.escape(r['event_id'])} · {r['variant']} · 点击图片查看原尺寸</p></details></article>''')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPIKE · 20 张训练样本审核</title><style>*{box-sizing:border-box}body{margin:0;background:#eef3f4;color:#19343d;font:16px/1.65 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1400px;margin:auto;padding:24px}h1{margin:0;font-size:30px}header p{color:#60777f;margin:8px 0}nav{position:sticky;top:0;background:#eef3f4eb;backdrop-filter:blur(12px);padding:12px 0;z-index:2;display:flex;gap:8px;flex-wrap:wrap}button{font:inherit;background:white;border:1px solid #c6d7db;border-radius:9px;padding:8px 20px;cursor:pointer}button.active{background:#217d66;color:white;border-color:#217d66}article{background:white;border:1px solid #dce7e9;border-radius:14px;margin:0 0 24px;overflow:hidden}.title{padding:16px 22px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}h2{font-size:20px;margin:0}.title span{color:#60777f}img{display:block;width:100%;height:auto}details{padding:14px 22px;background:#f9fbfb}summary{cursor:pointer;color:#217d66}details img{max-width:1000px;margin:16px auto}details p{overflow-wrap:anywhere;font-size:13px;color:#60777f}article[hidden]{display:none}.legend{border-left:4px solid #217d66;padding:6px 14px;background:white}.red{color:#e32942}.blue{color:#1673c9}a{color:#217d66}@media(max-width:600px){main{padding:12px}h1{font-size:24px}.title{padding:12px}h2{font-size:18px}}</style><main><header><h1>10 张正样本 + 10 张负样本</h1><p>从本轮完整数据集的训练部分分层抽取，每个事件一张；含 5 多、5 空，以及 5 张困难反例、5 张普通背景。</p><p class="legend"><b class="red">红框：当前训练 TXT 的实际标注</b>　<b class="blue">蓝虚线：原训练画面的右边界</b><br>右侧额外展示 40 根后续 K 线。点击任意图片放大；展开卡片可对照原训练图。审核图单独保存，未改动训练图与标签。</p><p>按方向、周期和负例类型固定抽样，没有按后续涨跌挑图。正例继承原样本筛选条件；此处用于看图核对，不是模型识别效果或收益证明。</p></header><nav><button class="active" data-filter="all">全部 20 张</button><button data-filter="positive">正样本 10 张</button><button data-filter="negative">负样本 10 张</button></nav>'''+''.join(cards)+'''<script>document.querySelectorAll('button').forEach(b=>b.onclick=()=>{document.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('article').forEach(x=>x.hidden=b.dataset.filter!=='all'&&x.dataset.kind!==b.dataset.filter);});</script></main></html>'''
    (out/'index.html').write_text(page,encoding='utf-8')
    for name, subset in [('positive',[r for r in records if r['class_id'] is not None]),('negative',[r for r in records if r['class_id'] is None])]:
        canvas=Image.new('RGB',(1800,460*5),'#e6edef')
        for i,r in enumerate(subset):
            im=Image.open(out/r['future_image']).convert('RGB').resize((900,460),Image.Resampling.LANCZOS)
            canvas.paste(im,((i%2)*900,(i//2)*460))
        canvas.save(out/(name+'_10.png'))


def build(selection_path: Path = SELECTION) -> dict:
    selection = json.loads(selection_path.read_text())
    commit = dataset_render._committed([Path(__file__),selection_path,ROOT/'yoyo/datasets/ma_profit_dataset.py',ROOT/'yoyo/layers/l1_detection/render.py'])
    ds, out = ROOT/selection['dataset_root'], ROOT/selection['output']
    if out.exists(): raise FileExistsError(out)
    if sha(ds/'manifest.jsonl') != selection['manifest_sha256']: raise ValueError('Dataset manifest changed')
    current = {(r['event_id'],r['variant']):r for r in rows(ds/'manifest.jsonl')}
    ledger_path=ROOT/selection['parent_ledger']['path']
    if sha(ledger_path)!=selection['parent_ledger']['sha256']: raise ValueError('Parent ledger changed')
    ledger={r['event_id']:r for r in rows(ledger_path)}
    recovery = json.loads(RECOVERY.read_text())['events']
    records=[];out.mkdir(parents=True)
    for item in selection['items']:
        row=item['sample'];ident=item['review_id']
        if current[(row['event_id'],row['variant'])] != row: raise ValueError('Selection changed')
        row=resolve_geometry(row,ledger)
        image, label_file = ds/row['image_path'], ds/row['label_path']
        if sha(image)!=row['image_sha256'] or sha(label_file)!=row['label_sha256']: raise ValueError('Training asset changed')
        step=pd.Timedelta(minutes=row['bar_minutes'])
        source=ROOT/row['source_path'];source_sha=row['source_sha256']
        if 'recovery_archive' in item:
            recovered=recovery[row['event_id']]
            if recovered['full_csv_sha256_verified']!=source_sha or recovered['post_rows']!=45: raise ValueError('Recovered future source drift')
            source=ROOT/recovered['path'];source_sha=recovered['sha256']
        if sha(source)!=source_sha: raise ValueError('Source hash drift')
        frame=read_interval(source,utc(row['core_start_time'])-1211*step,utc(row['decision_at_utc'])+40*step)
        label=label_file.read_text();assets=review_assets(frame,row,label)
        future_path=f'{ident}_future.png';overlay_path=f'{ident}_training_box.png'
        draw_review({**item,'sample':row},assets,out/future_path)
        original=Image.open(image).convert('RGB')
        if assets['box']: ImageDraw.Draw(original).rectangle(assets['box']['pixel_box'],outline='#e32942',width=5)
        original.save(out/overlay_path)
        records.append({'review_id':ident,'event_id':row['event_id'],'asset':row['canonical_asset'],
            'bar_minutes':row['bar_minutes'],'class_id':row['class_id'],'type_name':type_name(row),'variant':row['variant'],
            'future_image':future_path,'future_sha256':sha(out/future_path),'training_overlay':overlay_path,
            'overlay_sha256':sha(out/overlay_path),'original_image_sha256':row['image_sha256'],'original_label_sha256':row['label_sha256'],
            'source_path':str(source.relative_to(ROOT)),'source_sha256':source_sha,'training_replay_sha_matches':True,
            **{k:v for k,v in assets.items() if k!='image'},'training_eligible':False})
        print(ident+' rendered and original crop reproduced',flush=True)
    for item in selection['items']:
        r=item['sample']
        if sha(ds/r['image_path'])!=r['image_sha256'] or sha(ds/r['label_path'])!=r['label_sha256']: raise ValueError('Training asset mutated during review')
    if any(p.suffix=='.txt' or p.name=='labels' for p in out.rglob('*')): raise ValueError('Review directory contains training labels')
    gallery(out,records)
    receipt={'source_commit':commit,'selection_sha256':sha(selection_path),'dataset_manifest_sha256':selection['manifest_sha256'],
        'positive_count':10,'negative_count':10,'future_bars':40,'original_asset_hashes_unchanged':True,
        'no_labels_in_review_directory':True,'training_eligible':False,'production_eligible':False,
        'items':records,'gallery_sha256':sha(out/'index.html')}
    write_json(out/'review_manifest.json',receipt)
    return {'output':str(out),'positive_count':10,'negative_count':10,'original_asset_hashes_unchanged':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--selection',type=Path,default=SELECTION)
    print(json.dumps(build(parser.parse_args().selection),ensure_ascii=False,indent=2))
