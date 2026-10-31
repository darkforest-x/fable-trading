"""Owner review pack: frozen original charts for all automatically clean samples.

Sort by future-path label quality,not model score. Original images are copied
unchanged. Each contact-sheet caption distinguishes outcome quality from
chronological model score; no score exists for the initial training period.
The editable human-label column starts blank: these are candidates,not gold.
"""
from pathlib import Path
import json
import shutil
from zipfile import ZipFile,ZIP_DEFLATED
import numpy as np
import pandas as pd
from PIL import Image,ImageDraw,ImageFont
from yoyo.evaluation.clean_reversal_labels import ROOT,EXP,dump
from yoyo.evaluation.btc_1618_early_marks_charts import FONT,BG,FG

OUT=ROOT/'artifacts/clean-reversal-review-20261101'


def original_images():
    mapping={}
    btc=ROOT/'artifacts/btc-1618-short-excursions-43-20261031'
    for r in json.loads((btc/'delivery.json').read_text())['rows']:
        key='BTC|'+r['signal_open']+'|'+r['side'];mapping[key]=btc/'png'/r['filename']
    alt=ROOT/'artifacts/top20-1618-short-excursions-20261031'
    for r in pd.read_csv(alt/'index.csv').to_dict('records'):
        key=r['symbol']+'|'+r['signal_open']+'|'+r['side'];mapping[key]=alt/r['symbol']/'png'/r['filename']
    assert len(mapping)==644;return mapping


def public_table(frame):
    out=frame[['event_id','symbol','side','signal_open','quality','net_progress_tr','efficiency','sustained_blocks_fraction','max_close_drawdown_tr','adverse_wick_tr','model_score','score_basis','outcome_group']].copy()
    out['side']=out.side.map({'upper':'红叉/看回落','lower':'绿勾/看反弹'})
    out['score_basis']=out.score_basis.map({'walkforward_unseen_at_fit':'按时间滚动/训练未见','initial_history_or_boundary_no_oos_score':'初始历史或跨界周/无历史外推分'})
    out['outcome_group']=out.outcome_group.map({'clean_candidate':'干净反转候选','reverse_failed':'期末未反向推进','ambiguous':'模糊/不够干净'})
    out=out.rename(columns=dict(event_id='样本ID',symbol='币种',side='方向',signal_open='信号时间_北京时间',quality='事后干净度',net_progress_tr='净反向推进_平均波幅',efficiency='收盘路径效率',sustained_blocks_fraction='持续推进区间比例',max_close_drawdown_tr='最大途中回撤_平均波幅',adverse_wick_tr='最大逆向影线_平均波幅',model_score='模型评分_0到100_不是胜率',score_basis='模型评分来源',outcome_group='自动分组'))
    out['人工确认']='';return out


def run():
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'png').mkdir(exist_ok=True)
    gallery=pd.read_csv(EXP/'gallery_644_labels_and_scores.csv');clean=pd.read_csv(EXP/'clean_gallery_candidates.csv')
    paths=original_images();rows=[]
    for n,r in enumerate(clean.to_dict('records'),1):
        source=paths[r['event_id']];assert source.exists()
        name=f'{n:03d}_{source.name}';target=OUT/'png'/name;shutil.copy2(source,target)
        rows.append(dict(r,number=n,filename=name,original=str(source)))
    table=public_table(clean);table.insert(0,'候选编号',range(1,len(table)+1));table['图片文件']=[r['filename'] for r in rows];table.to_csv(OUT/'干净反转候选及模型评分.csv',index=False,encoding='utf-8-sig')
    public_table(gallery).to_csv(OUT/'全部644张自动分组及模型评分.csv',index=False,encoding='utf-8-sig')
    titlefont=ImageFont.truetype(FONT,38);captionfont=ImageFont.truetype(FONT,23);sheets=[]
    for page,a in enumerate(range(0,len(rows),8),1):
        batch=rows[a:a+8];sheet=Image.new('RGB',(2400,105+720*((len(batch)+1)//2)),BG);d=ImageDraw.Draw(sheet)
        d.text((30,18),f'干净反转候选 {a+1}–{a+len(batch)} / {len(rows)} · 按事后干净度排序，待人工确认',font=titlefont,fill=FG)
        d.text((30,67),'事后干净度用于标注；模型分仅用信号时已知特征，不是胜率。初始训练期不冒充历史外推。',font=captionfont,fill='#aab3bf')
        for j,r in enumerate(batch):
            x=(j%2)*1200;y=105+(j//2)*720
            score=f'{r["model_score"]:.1f}' if np.isfinite(r['model_score']) else '训练期无外推分'
            caption=f'候选{r["number"]:03d} · 事后干净度 {r["quality"]:.1f} · 模型分 {score}'
            d.text((x+25,y+9),caption,font=captionfont,fill='#e6bd6a')
            with Image.open(OUT/'png'/r['filename']) as im:
                assert im.size==(2400,1350);sheet.paste(im.convert('RGB').resize((1200,675),Image.Resampling.LANCZOS),(x,y+45))
        p=OUT/f'候选总览_{page:02d}.jpg';sheet.save(p,quality=93,subsampling=0);sheets.append(p.name)
    zipname=f'干净反转候选_{len(rows)}张_及全部644评分.zip'
    with ZipFile(OUT/zipname,'w',ZIP_DEFLATED) as z:
        for p in list((OUT/'png').glob('*.png'))+[OUT/n for n in sheets]+list(OUT.glob('*.csv')):z.write(p,p.relative_to(OUT))
    with ZipFile(OUT/zipname) as z:assert z.testzip() is None and sum(n.startswith('png/') for n in z.namelist())==len(rows)
    dump(OUT/'delivery.json',dict(candidates=len(rows),gallery_samples=len(gallery),by_symbol=clean.symbol.value_counts().to_dict(),candidate_scores_available=int(clean.model_score.notna().sum()),candidate_scores_unavailable_initial_training=int(clean.model_score.isna().sum()),archive=zipname,sheets=sheets,selection='Future-path quality above frozen initial-training80th percentile;automatic candidates,not gold. All selected source charts copied unchanged.',rows=[dict(event_id=r['event_id'],number=r['number'],filename=r['filename'],quality=r['quality'],model_score=r['model_score'] if np.isfinite(r['model_score']) else None,original=r['original']) for r in rows]))
    print('Review pack',len(rows),'candidates',len(sheets),'sheets',OUT/zipname,flush=True)


if __name__=='__main__':run()
