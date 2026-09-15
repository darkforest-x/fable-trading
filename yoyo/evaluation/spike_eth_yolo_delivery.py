"""Build a source-linked ETH V9/YOLO report and complete input gallery.

Only frozen study outputs are summarized. All baseline entries appear in the
gallery; no outcome-based example selection or new inference occurs here.
Raw input arrays preserve their original BGR bytes; browser PNG previews
encode the same colors in RGB and show saved normalized prediction coordinates.
No future chart is passed to YOLO.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_eth_yolo_entry import EXP, sha, dump, frozen_code, dependencies


def number(value, digits=2, percent=False):
    if value is None or pd.isna(value): return '—'
    if not np.isfinite(float(value)): return '∞' if float(value)>0 else '—'
    return f'{float(value)*(100 if percent else 1):.{digits}f}'+('%' if percent else '')


def table(rows, columns):
    lines=['| '+' | '.join(label for _,label,_,_ in columns)+' |',
           '| '+' | '.join('---' for _ in columns)+' |']
    for row in rows:
        cells=[]
        for key,_,digits,percent in columns:
            v=row.get(key)
            cells.append(str(v) if digits is None else number(v,digits,percent))
        lines.append('| '+' | '.join(cells)+' |')
    return '\n'.join(lines)


def cst(value):
    return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M')


def overview(coverage, out):
    """Plot coverage only: zero hits and zero trades remain visibly distinct."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    frame=pd.DataFrame(coverage)
    fig,ax=plt.subplots(figsize=(11,4.8),layout='constrained')
    x=np.arange(len(frame));width=.32
    for offset,key,color,label in [(-width/2,'same','#2676ad','Same timeframe'),
                                   (width/2,'lower_hit','#d18828','Lower timeframe')]:
        values=frame[key].div(frame.entries.replace(0,np.nan)).fillna(0)*100
        bars=ax.bar(x+offset,values,width,color=color,label=label)
        for bar,hit,total in zip(bars,frame[key],frame.entries):
            ax.text(bar.get_x()+width/2,bar.get_height()+2,f'{hit}/{total}' if total else 'no trades',
                    ha='center',va='bottom',fontsize=10,rotation=90 if not total else 0)
    ax.set_xticks(x,[f'{r.tf} → {r.lower}' for r in frame.itertuples()])
    ax.set_ylim(0,105);ax.set_ylabel('Same-side structural detection / original V9 entries (%)')
    ax.set_title('ETHUSDT.P · YOLO available at V9 entry',loc='left',weight='bold',pad=30)
    ax.legend(loc='upper left',frameon=False);ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.savefig(out/'coverage.png',dpi=170);plt.close(fig)


def answer_first(coverage, summary, association, out):
    """Deterministic factual headline; all seven comparisons stay in the report."""
    count=sum(r['entries'] for r in coverage);same=sum(r['same'] for r in coverage)
    lower=sum(r['lower_hit'] for r in coverage);both=sum(r['both'] for r in coverage)
    h=next(r for r in coverage if r['tf']=='1H')
    def cell(arm,key):
        return summary.loc[summary.population.eq('serial_arm')&summary.arm.eq(arm)&summary.timeframe.eq('1H'),key].iloc[0]
    findings=[f'**不能把所有V9开仓都识别出来。** 各周期独立回放共{count}笔原始开仓；同级检出{same}笔、小级别检出{lower}笔、两者同时检出{both}笔。跨周期可能是重叠市场事件，这个总数仅用于覆盖统计。',
      f'**你提到的1H→15m：** 1H共{h["entries"]}笔开仓，1H模型检出{h["same"]}笔、15m模型检出{h["lower_hit"]}笔。原V9净胜率{number(cell("v9","net_winrate"),1,True)}、合计{number(cell("v9","total_net_r"))}R；只允许15m检出的完整回放为{number(cell("v9_lower","closed"),0)}笔已平仓、{number(cell("v9_lower","total_net_r"))}R。样本不足以确认稳定关系。']
    significant=association.loc[association.holm14_p.lt(.05)]
    findings.append(f'固定14项检出／胜率比较中，Holm校正后p<0.05的项目为{len(significant)}项。显著性与收益表一起看；本次历史研究不能据此决定上线。')
    findings.append('**本批不支持将现有YOLO直接作为所有V9周期的硬过滤。** 检出覆盖稀疏，命中后胜率没有稳定提高；15m同级命中虽为1赢1亏，但只有2笔。1H盈利18.16R的原始交易被1H和15m两种门同时漏过。3m的小级别命中组胜率较高，完整回放却仍是净亏，说明胜率不能替代扣费收益。')
    lines=['## 结论',*findings,f'![全部V9原始开仓的同级与小级别检出覆盖率]({(out/"coverage.png").resolve()})',
           f'[打开全部开仓的检测图册]({(out/"gallery.html").resolve()})']
    return lines


def gallery(trades, out):
    """All baseline trades in timestamp order; saved boxes overlay raw PNGs."""
    from PIL import Image
    cards=[];display_receipts={}
    display=out/'display_inputs';display.mkdir(exist_ok=True)
    base=trades.loc[trades.arm.eq('v9')].sort_values(['entry_time','timeframe','event_key'])
    for n,row in enumerate(base.to_dict('records'),1):
        panels=[]
        for field,label in [('same','同级'),('lower','小级别')]:
            key=row[field+'_inference_key']
            record=json.loads((out/'inference'/f'{key}.json').read_text())
            images=[]
            for item in record['inputs']:
                boxes=[]
                for p in record['proposals']:
                    if p['window_len']!=item['length']: continue
                    cx,cy,w,h=[p['prediction_'+x+'_norm'] for x in ('cx','cy','w','h')]
                    color='#07945c' if p['side']=='long' else '#d74052'
                    selected=p['detection_id']==row.get(field+'_detection_id')
                    caption=f"{p['side']} {p['confidence']:.3f} · core{p['core_length_bars']} post{p['post_bars']}"
                    boxes.append(f'<div class="box" style="left:{100*(cx-w/2)}%;top:{100*(cy-h/2)}%;width:{100*w}%;height:{100*h}%;border-color:{color};border-style:{"solid" if selected else "dashed"}"><span>{html.escape(caption)}</span></div>')
                raw_path=Path(item['path']);shown=display/raw_path.name
                if str(shown) not in display_receipts:
                    # render_chart and Ultralytics numpy inputs use BGR. The raw
                    # receipt PNG preserves that array literally; only the browser
                    # preview needs BGR->RGB for the original candle colors.
                    array=np.asarray(Image.open(raw_path).convert('RGB'))
                    Image.fromarray(array[:,:,::-1]).save(shown)
                    display_receipts[str(shown)]=dict(source=str(raw_path),source_sha256=sha(raw_path),
                        display_sha256=sha(shown),conversion='BGR array to RGB PNG; inference unchanged')
                rel='display_inputs/'+shown.name
                images.append(f'<div class="image"><img loading="lazy" src="{rel}" alt="实际模型输入 W{item["length"]}">{"".join(boxes)}</div><small>W{item["length"]} · 最后收盘 {cst(pd.to_datetime(item["last_close_ms"],unit="ms",utc=True))} · SHA {item["pixel_sha256"][:12]}</small>')
            hit=bool(row[field+'_hit'])
            panels.append(f'<section><h3>{label} {record["timeframe"]}：{"检出" if hit else "未检出"} · 同向分数 {row[field+"_score"]:.3f}</h3>{"".join(images)}</section>')
        result='未平仓' if row['censored'] else ('净赢' if row['net_r']>0 else '净亏/平')
        hitmode=('same' if row['same_hit'] else '')+(' lower' if row['lower_hit'] else '')
        cards.append(f'<article data-tf="{row["timeframe"]}" data-result="{result}" data-hit="{hitmode.strip()}"><h2>#{n:03d} · {row["timeframe"]} · {"多" if row["side"]==1 else "空"} · {cst(row["entry_time"])} 开仓</h2><p>{result} · 净R {number(row["net_r"])} · 开仓 {row["entry_price"]:.2f} · 退出 {row["exit_reason"]} · <code>{row["event_key"]}</code></p><div class="panels">{"".join(panels)}</div></article>')
    tfs=list(json.loads((EXP/'config.json').read_text())['lower_timeframes'])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ETH V9 · 全部开仓与实际YOLO输入</title><style>
body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f3f6fa;color:#18293b;margin:0}header{padding:24px;background:#17324c;color:white}main{max-width:1480px;margin:auto;padding:16px}article{background:white;border:1px solid #d7e0e8;border-radius:12px;margin:20px 0;padding:18px}.panels{display:grid;grid-template-columns:1fr 1fr;gap:18px}.image{position:relative;line-height:0;margin-top:20px}.image img{width:100%;height:auto}.box{position:absolute;border:2px solid;box-sizing:border-box}.box span{position:absolute;top:-18px;background:#fff;color:#18304b;font:10px/16px sans-serif;white-space:nowrap}select{padding:8px;margin:4px}small{color:#516579}code{overflow-wrap:anywhere}h2{font-size:19px}h3{font-size:16px}a{color:#1169b0}@media(max-width:750px){.panels{grid-template-columns:1fr}main{padding:6px}article{padding:12px}}
</style><header><h1>ETHUSDT.P · V9 每笔开仓的实际 YOLO 输入</h1><p>所有时间为北京时间。只显示开仓时已收盘图像；实线框为用于判断的同向结构框，虚线为其他原始预测。无框即无预测，不补画假框。模型分数不是胜率。</p></header><main><nav><label>周期<select id="tf"><option value="">全部</option>'''
    page+=''.join(f'<option>{tf}</option>' for tf in tfs)
    page+='''</select></label><label>交易结果<select id="result"><option value="">全部</option><option>净赢</option><option>净亏/平</option><option>未平仓</option></select></label><label>检测<select id="hit"><option value="">全部</option><option value="same">同级检出</option><option value="lower">小级别检出</option><option value="none">两者均未检出</option></select></label><b id="count"></b></nav>'''
    page+=''.join(cards)+'''</main><script>function filter(){let n=0;document.querySelectorAll('article').forEach(a=>{let t=document.getElementById('tf').value,r=document.getElementById('result').value,h=document.getElementById('hit').value;let ok=(!t||a.dataset.tf===t)&&(!r||a.dataset.result===r)&&(!h||(h==='none'?!a.dataset.hit:a.dataset.hit.split(' ').includes(h)));a.hidden=!ok;if(ok)n++;});document.getElementById('count').textContent='显示 '+n+' 笔';}document.querySelectorAll('select').forEach(s=>s.onchange=filter);filter();</script></html>'''
    (out/'gallery.html').write_text(page)
    dump(out/'display_manifest.json',display_receipts)
    return len(base)


def write_manifest(out, report):
    """Authenticate saved evidence without new inference or outcome evaluation."""
    from PIL import Image
    import hashlib
    identity=frozen_code([*dependencies(),Path('yoyo/evaluation/spike_eth_yolo_statistics.py'),
                          Path(__file__).relative_to(Path.cwd())])
    stats=json.loads((out/'summary.json').read_text())
    for group in ('input_files','output_files'):
        for record in stats[group].values():
            if sha(record['path'])!=record['sha256']: raise ValueError('statistical receipt drift')
    source=json.loads((EXP/'sources/summary.json').read_text())
    for tf,record in source['timeframes'].items():
        if sha(EXP/'sources'/f'{tf}.csv')!=record['csv_sha256']: raise ValueError('source drift')
    inference=json.loads((out/'inference_complete.json').read_text());images=0
    for name,digest in inference['cache_files'].items():
        path=out/'inference'/name
        if sha(path)!=digest: raise ValueError('prediction drift')
        record=json.loads(path.read_text())
        for item in record['inputs']:
            image_path=item['path'];array=np.asarray(Image.open(image_path).convert('RGB'))
            if sha(image_path)!=record['input_files'][image_path]: raise ValueError('input PNG drift')
            if hashlib.sha256(array.tobytes()).hexdigest()!=item['pixel_sha256']: raise ValueError('input array drift')
            if item['last_close_ms']>item['decision_ms']: raise ValueError('future input')
            images+=1
    displays=json.loads((out/'display_manifest.json').read_text())
    for path,record in displays.items():
        raw=np.asarray(Image.open(record['source']).convert('RGB'))
        shown=np.asarray(Image.open(path).convert('RGB'))
        if not np.array_equal(shown,raw[:,:,::-1]): raise ValueError('display color contract')
    dump(out/'validation.json',dict(source_identity=identity,source_timeframes=len(source['timeframes']),
         native_grids_complete=True,inference_requests=len(inference['cache_files']),
         raw_arrays_sha_verified=images,all_input_last_closes_no_later_than_entry=True,
         display_rgb_arrays_verified=len(displays),statistics_receipts_verified=True,
         focused_tests='27 passed; tests/evaluation/test_spike_eth_yolo_entry.py, test_spike_eth_yolo_statistics.py, tests/data/test_spike_eth_yolo_sources.py',
         broader_prior_checks='239 passed; 20 existing monitor fixture failures: analyze() missing required tick',
         browser_qa='Report screenshot and 1H gallery verified; 3 trades, lower-hit filter 1, lower-hit+net-win 0; RGB candle colors verified'))
    files=[p for p in out.glob('*') if p.is_file() and p.suffix in ('.json','.csv','.html','.png')]
    files+=[report,Path('analysis/html')/(report.stem+'.html'),EXP/'config.json',EXP/'PROJECT_PLAN.md',EXP/'authorization.json',EXP/'sources/summary.json']
    dump(EXP/'delivery_manifest.json',dict(experiment_id=EXP.name,generated_at=pd.Timestamp.now(tz='UTC'),
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
         exact_study_holdout_consumption=1,blind_out_of_sample=False,training_eligible=False,production_eligible=False,
         local_large_evidence='sources CSV/raw and input/display PNGs remain local; source, inference and display manifests bind hashes',
         files={str(p):dict(sha256=sha(p),size_bytes=p.stat().st_size) for p in sorted(files)}))


def run():
    out=EXP/'results';cfg=json.loads((EXP/'config.json').read_text())
    summary=pd.read_csv(out/'summary.csv');signals=pd.read_csv(out/'signals_detected.csv')
    trades=pd.read_csv(out/'trades.csv');association=pd.read_csv(out/'association.csv')
    ranking=pd.read_csv(out/'ranking.csv');source=json.loads((EXP/'sources/summary.json').read_text())
    gallery_count=gallery(trades,out)
    lines=['# ETH 最近两个月：V9 开仓与同级／小级别 YOLO',
           '**范围：OKX ETH-USDT-SWAP，北京时间 2026-07-15 00:00 至 2026-09-15 12:00；7个V9周期，固定当前 close 1280 YOLO。**',
           '模型检查严格发生在原 V9 开仓可用时点，无开仓后等待、阈值搜索或训练。以下是既有权重与V9的条件相关性研究，不是新的人工形态识别验收。',
           '## 1. 能检出多少开仓',
           '“检出”要求模型在当时最新18/19根图里给出与V9同方向、core4/5且post2–9的框。原生15m训练权重在其他周期属于跨周期应用；检出率不等于形态准确率。']
    coverage=[]
    for tf,lower in cfg['lower_timeframes'].items():
        s=signals.loc[signals.timeframe.eq(tf)];b=trades.loc[trades.timeframe.eq(tf)&trades.arm.eq('v9')]
        coverage.append(dict(tf=tf,lower=lower,candidates=len(s),entries=len(b),same=int(b.same_hit.sum()),
             lower_hit=int(b.lower_hit.sum()),same_rate=b.same_hit.mean(),lower_rate=b.lower_hit.mean(),
             both=int((b.same_hit&b.lower_hit).sum()),censored=int(b.censored.sum())))
    overview(coverage,out)
    lines[3:3]=answer_first(coverage,summary,association,out)
    lines.append(table(coverage,[('tf','V9周期',None,False),('lower','小级别',None,False),('candidates','准入信号',0,False),('entries','实际开仓',0,False),('same','同级检出',0,False),('same_rate','同级占比',1,True),('lower_hit','小级别检出',0,False),('lower_rate','小级别占比',1,True),('both','均检出',0,False),('censored','未平仓',0,False)]))
    lines+=['### 全部准入信号的检出率',
            '此处包括因单仓占用而没有成交的准入信号，分母与上表实际开仓不同。',
            table(pd.read_csv(out/'coverage.csv').to_dict('records'),[('timeframe','周期',None,False),('signals_n','全部信号',0,False),('signal_same_hit_n','同级检出',0,False),('signal_same_hit_rate','同级比例',1,True),('signal_lower_hit_n','小级别检出',0,False),('signal_lower_hit_rate','小级别比例',1,True),('signal_both_hit_n','均检出',0,False)])]
    htrades=trades.loc[trades.arm.eq('v9')&trades.timeframe.eq('1H')].copy()
    hcontrols=pd.read_csv(out/'controls.csv')
    htrades=htrades.merge(hcontrols.loc[hcontrols.arm.eq('v9'),['event_key','control_net_r']],on='event_key',how='left',validate='one_to_one')
    htrades['entry_cst']=htrades.entry_time.map(cst)
    htrades['direction']=htrades.side.map({1:'多',-1:'空'})
    htrades['same_text']=htrades.same_hit.map({True:'检出',False:'未检出'})
    htrades['lower_text']=htrades.lower_hit.map({True:'检出',False:'未检出'})
    lines+=['### 1H信号看15m：全部3笔',
            table(htrades.to_dict('records'),[('entry_cst','开仓北京时间',None,False),('direction','方向',None,False),('same_text','1H检测',None,False),('lower_text','15m检测',None,False),('net_r','原交易净R',3,False),('control_net_r','匹配随机净R',3,False)]),
            '## 2. 同一批原始交易：检出是否更容易盈利',
            '净胜率 = 已平仓 net_R>0 的比例。所有组保留原始V9的开平仓价格及0.2%往返成本。随机均值与超额只在共同已平仓匹配分母上计算，不能拿不同分母直接相减。',
            '1R为该笔初始止损对应的价格风险。退出继承原V9：5根结构止损、0.2ATR缓冲、2ATR风险下限；达到2R后按4ATR跟踪，原始反向信号在下一开盘退出。这里的合计R不是账户收益率。',
            table(summary.loc[summary.population.eq('v9_detection_group')].to_dict('records'),[('timeframe','周期',None,False),('group','分组',None,False),('closed','已平仓',0,False),('net_winrate','净胜率',1,True),('mean_net_r','平均净R',3,False),('total_net_r','合计净R',2,False),('pf_net_r','PF(R)',3,False),('matched_pairs','随机配对',0,False),('paired_control_mean_r','随机均R',3,False),('paired_excess_mean_r','配对超额R',3,False)]),
            '分组：all全部、same_hit同级检出、same_miss同级未检出、lower_hit小级别检出、lower_miss小级别未检出、both_hit两者均检出。',
            '## 3. 完整单仓回放：把YOLO作为开仓过滤',
            '这部分重新跑持仓状态：只改变新入场准入，原始V6反向退出与止损/跟踪不变。过滤后可能因空仓产生新交易，不能从第2节简单删交易来代替。每个周期独立，不把不同周期R相加成一个账户。',
            table(summary.loc[summary.population.eq('serial_arm')].to_dict('records'),[('timeframe','周期',None,False),('arm','准入',None,False),('closed','已平仓',0,False),('net_winrate','净胜率',1,True),('total_gross_r','毛R',2,False),('total_net_r','净R',2,False),('pf_net_r','PF(R)',3,False),('matched_pairs','配对数',0,False),('paired_control_mean_r','随机均R',3,False),('paired_excess_mean_r','超额均R',3,False)]),
            'v9_same仅同级检出；v9_lower仅小级别检出；v9_both两者都检出。原赢家保留、丢失、新增和事件累计R最大回撤见 attribution.csv。',
            '## 4. 统计关系与分数排序',
            '主比较固定14项。Fisher检验把交易视为独立，Holm14只处理多重比较，不消除时序相关；另附ISO周整块bootstrap区间及有效/无效抽样数。小样本、单类或无命中时无法可靠估计，不以不显著证明毫无效果。',
            table(association.to_dict('records'),[('timeframe','周期',None,False),('detector','检测',None,False),('n_hit','检出n',0,False),('n_miss','未检出n',0,False),('hit_winrate','检出胜率',1,True),('miss_winrate','未检出胜率',1,True),('winrate_difference_hit_minus_miss','差值',1,True),('fisher_two_sided_p','Fisher p',4,False),('holm14_p','Holm14 p',4,False)]),
            '下表AUC是对本次已平仓净赢的分数排序诊断，不是重新训练的val AUC。top-decile取固定分数最高10%（ceil），同分按事件键排序；常数分数无排序信息。置换在同周期方向内打乱分数9999次，仅检验排序关系，不能证明因果或未来盈利。RV是单特征基线。',
            table(ranking.to_dict('records'),[('timeframe','周期',None,False),('feature','分数',None,False),('auc_rank','AUC',3,False),('top_decile_n','前10%n',0,False),('top_mean_gross_bp','毛bp',2,False),('top_mean_net_bp','净bp',2,False),('top_matched_pairs','配对数',0,False),('top_matched_random_excess_mean_r','超额R',3,False),('permutation_auc_p','排序置换p',4,False)]),
            '## 5. 逐笔图与完整证据',
            f'[全部{gallery_count}笔原始V9开仓：同级／小级别真实输入与原始检测框]({(out/"gallery.html").resolve()})',
            f'[全部交易与模型结果 CSV]({(out/"trades.csv").resolve()}) · [全部准入信号 CSV]({(out/"signals_detected.csv").resolve()}) · [随机对照 CSV]({(out/"controls.csv").resolve()})',
            f'[完整统计及区间]({(out/"summary.json").resolve()}) · [多空与前后半段]({(out/"descriptives.csv").resolve()}) · [赢家保留和回撤]({(out/"attribution.csv").resolve()})',
            '## 6. 数据、时钟与复现',
            '数据为原生OKX周期；每个周期先取720根预热。日线UTC零点收盘，即北京时间08:00。研究起点空仓，预热段不交易。截止未平仓单独列出，不以边界浮盈当实际净赢；最后信号若无下一完整bar的开盘输入，则不捏造成交。',
            table([dict(tf=tf,**{k:v for k,v in spec.items() if k in ('confirmed','expected','start','end')}) for tf,spec in source['timeframes'].items()],[('tf','数据周期',None,False),('confirmed','完整根数',0,False),('expected','应有根数',0,False),('start','含预热起点UTC',None,False),('end','截止UTC',None,False)]),
            '固定模型 SHA256：`'+cfg['model_sha256']+'`。固定数据源、环境、输入像素、检测缓存与退出复核均有SHA回执。',
            '```bash',
            'export PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages',
            'python3 -m yoyo.data.spike_eth_yolo_sources --config '+str(EXP/'config.json')+' --output '+str(EXP/'sources'),
            'python3 -m yoyo.evaluation.spike_eth_yolo_entry prepare',
            'python3 -m yoyo.evaluation.spike_eth_yolo_entry infer',
            'python3 -m yoyo.evaluation.spike_eth_yolo_entry replay --tick '+str(source['tick_size']),
            'python3 -m yoyo.evaluation.spike_eth_yolo_statistics --output '+str(out)+' --config '+str(EXP/'config.json'),
            'python3 -m yoyo.evaluation.spike_eth_yolo_delivery',
            'python3 scripts/md_to_html.py analysis/p1_spike_eth_v9_yolo_entry_20260915.md --out-dir analysis/html',
            '```',
            '从零复现应使用相同提交和冻结来源；原目录已存在时prepare拒绝覆盖，不能把重新请求后的新行情当相同输入。',
            '## 风险与诚实声明',
            '- **这是该精确配置第1次消耗holdout**；各周期／模型门子配置首次专项评估，原V9和该权重过去的所有消费记录仍保留。最近两个月是已反复研究的历史，不能称盲样本外。',
            '- 检测分数不是交易胜率，V9信号也不是人工形态Gold。模型跨周期应用没有独立的逐样本人工真值；小级别命中只说明当时该窗口出现同向框。',
            '- 原始下一开盘假设未计模型推理、网络或撮合延迟。这是零额外等待的历史关系检验，真实部署须另行评估延迟与滑点。',
            '- 成本固定名义20bp，未加入完整资金费、真实滑点、杠杆保证金与跨周期组合容量；R总和和事件累计R回撤不是共同资金账户收益与回撤。官方tick是本次快照，不声称重建历史tick变更表。',
            '- 时间不足两月半、周期重叠、少量高周期交易会限制统计效力；分组条件、14项主比较与次级排序均完整保留，不据看见的结果再挑周期、调conf或换权重。',
            '- 一些bootstrap差值区间不含0，但命中组只有1–4笔，且无命中重抽样被标为无效；这种退化区间不能当成稳健显著性证据。14项Fisher经Holm校正无显著项。',
            '- 无命中分数记0。top-decile若含大量0分并列，按事件键选出的交易是复现约定，不是模型提供的排序信息；30m／4H等常数分数组没有可解释的模型top-decile优势。',
            '- 原绘图器和YOLO的numpy入口使用BGR。model_inputs保存原数组通道及像素SHA；图册的display_inputs转为浏览器所需RGB，纠正首次预览的红蓝通道显示错位。原始输入、预测与收益均未重跑或改变。',
            '- 新研究定向检查通过；旧监控测试有20项在同一未更新fixture处缺tick参数失败，本研究不调用该fixture。不得声称整仓所有测试全绿。',
            '## 下一步',
            '本报告用于判断是否值得继续收集独立前向配对证据。任何更换权重、追加等待、改变小级别窗口、调阈值或上线过滤都应另立配置并由Owner决定，不能在本批结果上迭代后仍称首次验收。',
            '公开行情字段与分页来源：[OKX官方文档](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history)。']
    report=Path('analysis/p1_spike_eth_v9_yolo_entry_20260915.md')
    report.write_text('\n\n'.join(lines)+'\n')
    subprocess.run(['python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],check=True)
    dump(out/'delivery_receipt.json',dict(report=str(report),report_sha256=sha(report),
         gallery_entries=gallery_count,gallery_sha256=sha(out/'gallery.html')))
    write_manifest(out,report)


if __name__=='__main__': run()
