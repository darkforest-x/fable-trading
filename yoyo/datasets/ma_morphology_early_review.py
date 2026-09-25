"""Inspect real early prefixes without assigning early positive/negative labels.

Owner correction 2026-09-25: reduce detection delay, not spread targets by
adding later candles. The same frozen 20 cases are inspected from core close
through the old post-five endpoint. These six diagnostic observations are
not six training augmentations or a new global crop policy. Original physical
box edges are retained; OHLC, HL2 MAs and scaling stop at each observation.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
from pathlib import Path
import shutil

import pandas as pd
from PIL import Image, ImageDraw

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_future_review import EXP, ROOT, SELECTION, font, label_coordinates, resolve_geometry, transform
from yoyo.datasets.ma_morphology_redo import read_interval, rows, sha, utc, write_json


def render_prefix(frame: pd.DataFrame, row: dict, actual_box: dict | None, post: int) -> dict:
    """Use open_time/OHLC and HL2 SMA/EMA20/60/120 only through core+post.

    Box geometry is inherited from the old actual TXT, not inferred from later
    prices. Outside-core/MA booleans are descriptive observations, not labels
    or a new signal gate. Direction remains the old retrospective class.
    """
    if type(post) is not int or not 0 <= post <= 5:
        raise ValueError('Diagnostic post must be an integer from zero through five')
    a, b = 1211, 1210 + int(row['core_bars'])
    pre = int(row['variant'][1:])
    step = pd.Timedelta(minutes=int(row['bar_minutes']))
    end = b + post
    prefix = frame.iloc[:end+1].copy()
    if (len(prefix) != end+1 or not prefix.open_time.diff().iloc[1:].eq(step).all()
            or prefix.open_time.iloc[a] != utc(row['core_start_time'])
            or prefix.open_time.iloc[b] != utc(row['core_end_time'])):
        raise ValueError('Incomplete prefix or core clock drift')
    png, _, _ = renderer._window_asset(prefix, core_start_i=a, core_end_i=b,
        pre_bars=pre, post_bars=post, support_start_i=0, price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
    visible = renderer.add_hl2_mas(prefix).iloc[a-pre:].reset_index(drop=True)
    tf = transform(visible, 1280, 742)
    pixels = None
    if actual_box is not None:
        # Continuous coordinates preserve original sub-bar boundaries.
        pixels = [tf.left + actual_box[k]/(tf.n_bars-1)*tf.plot_w for k in ('bar_left','bar_right')]
        y = [tf.top+(tf.price_max-actual_box[k])/(tf.price_max-tf.price_min)*tf.plot_h
             for k in ('price_high','price_low')]
        pixels = [pixels[0], y[0], pixels[1], y[1]]
        if not (pixels[0] < pixels[2] and pixels[1] < pixels[3]):
            raise ValueError('Inherited geometry inverted')
    box_inside = (pixels is None or
                  (0 <= pixels[0] < pixels[2] <= 1280 and 0 <= pixels[1] < pixels[3] <= 742))
    core, last = prefix.iloc[a:b+1], visible.iloc[-1]
    close = float(last.close)
    ma = [float(last[c]) for c in renderer.SIX_MA_COLUMNS]
    return {'png':png, 'pixel_box':pixels, 'box_inside_canvas':box_inside,
        'post_bars':post, 'pre_bars':pre,
        'visible_bars':len(visible), 'bar_x_px':[tf.x_at(i) for i in range(len(visible))],
        'observed_at_utc':(prefix.open_time.iloc[-1]+step).isoformat(),
        'delay_after_core_close_minutes':post*int(row['bar_minutes']),
        'earlier_than_old_input_minutes':(5-post)*int(row['bar_minutes']),
        'close':close, 'close_above_core_high':close>float(core.high.max()),
        'close_below_core_low':close<float(core.low.min()),
        'close_above_all_six_mas':close>max(ma), 'close_below_all_six_mas':close<min(ma),
        'change_from_core_close_pct':(close/float(core.close.iloc[-1])-1)*100,
        'class_at_this_prefix':'unadjudicated', 'training_eligible':False}


def overlay(view: dict, ident: str, target: Path) -> None:
    """Add review-only original-core outline and core-relative candle numbers."""
    image = Image.open(io.BytesIO(view['png'])).convert('RGB')
    draw = ImageDraw.Draw(image)
    if view['pixel_box']:
        draw.rectangle(view['pixel_box'], outline='#e32942', width=4)
    for i, x in enumerate(view['bar_x_px']):
        draw.text((x-9, 705), str(i-view['pre_bars']+1), font=font(17), fill='#61767e')
    banner = Image.new('RGB', (1280, 804), 'white')
    banner.paste(image, (0, 62))
    d = ImageDraw.Draw(banner)
    d.text((20,8), f"{ident} · 核心后 {view['post_bars']} 根 · 早于旧输入 {view['earlier_than_old_input_minutes']} 分钟", font=font(23), fill='#172e36')
    note = ('旧渲染器边缘容纳不下完整框；此图只供诊断，不能直接生成训练标签。'
            if not view['box_inside_canvas'] else '红框沿用原核心；编号 1 为核心首根。当前切片的正负类别尚待核对。')
    d.text((20,37), note, font=font(17), fill='#b3293c' if not view['box_inside_canvas'] else '#61767e')
    banner.save(target)


def gallery(out: Path, records: list[dict]) -> None:
    cards=[]
    for r in records:
        ident=r['review_id']
        buttons=''.join(f'<button data-post="{p}">后 {p} 根</button>' for p in range(6))
        cards.append(f'''<article data-kind="{r['sample_kind']}" id="{ident}"><h2>{ident} · {html.escape(r['asset'])} · {r['bar_minutes']} 分钟</h2>
<p>原标签：{html.escape(r['old_label'])} · 核心 {r['core_bars']} 根 · 早期类别待核对</p>
<nav>{buttons}</nav><p class="facts"></p><a class="full" target="_blank"><img class="chart" loading="lazy" alt="{ident} 逐根观察与原核心框"></a>
<details><summary>原事件后续走势（仅审核）</summary><p>下图蓝线是旧输入后 5 根的截止线；不代表上方所选时点已经看得到右侧走势。</p><img loading="lazy" src="future_review/{ident}.png" alt="{ident} 后续走势"></details></article>''')
    data=json.dumps(records,ensure_ascii=False).replace('</','<\\/')
    document='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>YOLO · 早期窗口核对</title>
<style>body{margin:0;background:#eef3f5;color:#19303a;font:16px system-ui}main{max-width:1320px;margin:auto;padding:24px}header,article{background:white;border:1px solid #dce5e8;border-radius:12px;padding:20px;margin:18px 0}h1{font-size:28px}h2{font-size:22px}p{line-height:1.65}nav{display:flex;gap:8px;flex-wrap:wrap}button{padding:10px 15px;border:1px solid #c9d8dd;border-radius:7px;background:#fff;cursor:pointer;font-size:16px}button.on{background:#187a65;color:white}img{width:100%;height:auto}summary{cursor:pointer;padding:14px 0}.facts{background:#f2f7f6;padding:12px;border-radius:6px}article[hidden]{display:none}details{border-top:1px solid #e2eaed}</style>
<main><header><h1>早期窗口核对 · 原来的 10 正例 + 10 负例</h1><p>检查核心刚结束到旧输入截止期间，逐根增加信息后，形态是否已经可识别。每张图只画到实际观察时点；核心框保持原始边界。这里的六个时点用于诊断，不是训练扩图方案，也没有自动选择统一的框后根数。</p><nav id="filter"><button data-kind="all" class="on">全部 20 例</button><button data-kind="positive">原正例 10</button><button data-kind="negative">原负例 10</button></nav></header>'''+''.join(cards)+'''</main><script>const records='''+data+''';
for(const r of records){const card=document.getElementById(r.review_id);function show(p){const v=r.views[p];const img=card.querySelector('.chart');img.src=v.overlay;card.querySelector('.full').href=v.overlay;for(const b of card.querySelectorAll('nav button'))b.classList.toggle('on',Number(b.dataset.post)===p);const direction=v.close_above_core_high?'高于核心最高价':v.close_below_core_low?'低于核心最低价':'仍在核心高低范围内';const ma=v.close_above_all_six_mas?'高于全部六条均线':v.close_below_all_six_mas?'低于全部六条均线':'在均线之间';card.querySelector('.facts').textContent=`观察时间 ${new Date(v.observed_at_utc).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} 北京时间 · 核心结束后 ${v.delay_after_core_close_minutes} 分钟 · 收盘${direction}，${ma}。这些是可见事实，不是自动正负标签。`;}for(const b of card.querySelectorAll('nav button'))b.onclick=()=>show(Number(b.dataset.post));show(0);}
for(const b of document.querySelectorAll('#filter button'))b.onclick=()=>{for(const x of document.querySelectorAll('#filter button'))x.classList.toggle('on',x===b);for(const c of document.querySelectorAll('article'))c.hidden=b.dataset.kind!=='all'&&c.dataset.kind!==b.dataset.kind;};</script></html>'''
    (out/'index.html').write_text(document,encoding='utf-8')


def build(out: Path) -> dict:
    source_review=EXP/'review_future20_v2'
    prior_path=source_review/'review_manifest.json'
    commit=renderer._committed([Path(__file__),SELECTION,prior_path,
        ROOT/'yoyo/datasets/ma_profit_dataset.py',ROOT/'yoyo/datasets/ma_morphology_future_review.py',ROOT/'yoyo/layers/l1_detection/render.py'])
    selection=json.loads(SELECTION.read_text());prior=json.loads(prior_path.read_text())
    dataset=ROOT/selection['dataset_root']
    if sha(dataset/'manifest.jsonl')!=selection['manifest_sha256']:raise ValueError('Source dataset drift')
    ledger_path=ROOT/selection['parent_ledger']['path']
    if sha(ledger_path)!=selection['parent_ledger']['sha256']:raise ValueError('Source ledger drift')
    ledger={r['event_id']:r for r in rows(ledger_path)}
    previous={r['review_id']:r for r in prior['items']}
    if out.exists():raise FileExistsError(out)
    (out/'observations').mkdir(parents=True);(out/'future_review').mkdir()
    records=[]
    for item in selection['items']:
        ident=item['review_id'];row=resolve_geometry(item['sample'],ledger);old=previous[ident]
        if row['event_id']!=old['event_id']:raise ValueError('Review event identity drift')
        for field,digest in (('image_path','image_sha256'),('label_path','label_sha256')):
            if sha(dataset/row[field])!=row[digest]:raise ValueError('Original asset drift')
        source=ROOT/old['source_path']
        if sha(source)!=old['source_sha256']:raise ValueError('Raw source drift')
        step=pd.Timedelta(minutes=int(row['bar_minutes']))
        frame=read_interval(source,utc(row['core_start_time'])-1211*step,utc(row['decision_at_utc']))
        end=1210+int(row['core_bars']);pre=int(row['variant'][1:])
        visible=renderer.add_hl2_mas(frame).iloc[1211-pre:].reset_index(drop=True)
        actual=label_coordinates((dataset/row['label_path']).read_text(),transform(visible,1280,742))
        if (actual is None)!=(row['class_id'] is None):raise ValueError('Class/box mismatch')
        views=[]
        for post in range(6):
            view=render_prefix(frame,row,actual,post)
            if post==5 and hashlib.sha256(view['png']).hexdigest()!=row['image_sha256']:
                raise ValueError('Old input replay is not byte-identical')
            name=f'observations/{ident}_post{post}.png'
            overlay(view,ident,out/name)
            views.append({k:v for k,v in view.items() if k!='png'}|{'overlay':name,'overlay_sha256':sha(out/name),'raw_input_sha256':hashlib.sha256(view['png']).hexdigest()})
        future=source_review/old['future_image']
        if sha(future)!=old['future_sha256']:raise ValueError('Future review drift')
        shutil.copyfile(future,out/'future_review'/f'{ident}.png')
        records.append({'review_id':ident,'event_id':row['event_id'],'asset':row['canonical_asset'],
            'bar_minutes':row['bar_minutes'],'core_bars':row['core_bars'],'sample_kind':row['sample_kind'],
            'old_label':old['type_name'],'old_variant':row['variant'],'views':views,
            'original_box':actual,'source_path':old['source_path'],'source_sha256':old['source_sha256'],
            'old_input_replayed_exactly':True,'training_eligible':False})
        print(ident+' early observations rendered',flush=True)
    gallery(out,records)
    receipt={'schema':'early-observation-review-v1','source_commit':commit,'selection_sha256':sha(SELECTION),
        'prior_review_sha256':sha(prior_path),'events':len(records),'observations':sum(len(r['views']) for r in records),
        'training_views_generated':0,'labels_generated':0,'new_training_started':False,'items':records,
        'training_eligible':False,'production_eligible':False,'gallery_sha256':sha(out/'index.html')}
    write_json(out/'review_manifest.json',receipt)
    return {k:v for k,v in receipt.items() if k!='items'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(build(parser.parse_args().output),ensure_ascii=False,indent=2))
