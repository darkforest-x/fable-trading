"""Render original-rule hits and explicitly rejected examples from clipped inputs.

All figures end at the candidate's five-bar confirmation close, never past the
owner's 2026-08-19 22:00 Beijing ceiling. WIF overview figures end at the latest
fully closed candle by that ceiling and are diagnostic, not asserted hits.
Close-based SMA/EMA20/60/120 are the original discovery feature definition.
"""
from __future__ import annotations
import argparse,hashlib,html,json
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection,PolyCollection
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
from yoyo.datasets.ma_launch_followup50 import configure_chinese_font,ensure_committed,candle_body_polygons
from yoyo.datasets.ma_rope_filter import add_six_mas
ROOT=Path(__file__).resolve().parents[2]
MA_COLS=['sma20','ema20','sma60','ema60','sma120','ema120']
COLORS=['#858995','#b0b3ba','#638cff','#a3b9ff','#ca7fe0','#e3b2ed']
REASONS={'six_ma_end_bandwidth_atr':'核心末端六条均线仍过散','six_ma_core_envelope_atr':'核心期间均线覆盖范围过大','candle_bundle_touch_rate':'K线贴近均线的比例不足','pre_body_q90_atr':'启动前K线实体过大','pre_abs_path_atr':'启动前累计波动过大','pre_last3_directional_progress_atr':'核心前已经沿方向走得太远','pre_max_favourable_excursion_atr':'核心前已有明显同向运动','post_retrace_atr':'确认期间回撤过大','positive_post_steps':'确认期间同向推进不足','core_reverse_body_count':'核心反向实体过多','post_reverse_body_count':'确认期间反向实体过多','core_max_body_atr':'核心实体过大','good_combined_distance':'与认可参考形态差距过大','good_lockstep_distance':'与认可参考的节奏差距过大','accepted_family_distance':'与认可形态家族差距过大','aligned_ma_slope_atr_per_bar':'均线顺向斜率不足','close_to_bundle_q75_atr':'价格离均线过远','density_topology':'均线收拢或交叉形态不足','core_directional_progress_atr':'核心涨跌幅不符合范围','max_opposite_post_body_atr':'确认期反向实体过大'}

def readl(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def bj(v):return pd.Timestamp(v).tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M')
def labeltf(m):return f'{m//60}小时' if m>=60 else f'{m}分'

def figure(frame,row,dest,kind,cutoff):
    minutes=int(row['bar_minutes']);end=int(row.get('confirm_i',len(frame)-1));begin=max(0,end-119)
    visible=frame.iloc[begin:end+1].copy();closes=visible.open_time+pd.Timedelta(minutes=minutes)
    if closes.max()>cutoff:raise ValueError('plot exceeds user cutoff')
    x=np.arange(len(visible));up=(visible.close>=visible.open).to_numpy();colors=np.where(up,'#2d76fa','#6c3ac0')
    fig,ax=plt.subplots(figsize=(16,8),dpi=120);fig.patch.set_facecolor('#f7f8fb');ax.set_facecolor('white')
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,l,h in zip(x,visible.low,visible.high)],colors=colors,linewidths=.85))
    eps=max(float(visible.high.max()-visible.low.min())*.0007,1e-12)
    polygons=candle_body_polygons(x,visible.open.to_numpy(),visible.close.to_numpy(),minimum_height=eps)
    ax.add_collection(PolyCollection(polygons,facecolors=colors,edgecolors=colors,linewidths=.5))
    for col,color in zip(MA_COLS,COLORS):ax.plot(x,visible[col],color=color,lw=1.3,label=col.upper())
    if 'source_core_start_i' in row:
        a=int(row['source_core_start_i'])-begin;b=int(row['source_core_end_i'])-begin
        core=visible.iloc[max(0,a):b+1];low=core[['low',*MA_COLS]].min().min();high=core[['high',*MA_COLS]].max().max();pad=(visible.high.max()-visible.low.min())*.02
        ax.add_patch(Rectangle((a-.6,low-pad),b-a+1.2,high-low+pad*2,fill=False,ec='#e44a47',lw=1.8,zorder=8))
        ax.axvspan(b+.5,len(visible)-.5,color='#e5ad21',alpha=.09)
        ax.text(.012,.97,'红框：4/5根核心  ·  淡黄：随后5根已完成的确认K线',transform=ax.transAxes,ha='left',va='top',fontsize=10,color='#596170')
    ticks=np.linspace(0,len(visible)-1,9,dtype=int);ax.set_xticks(ticks);ax.set_xticklabels([bj(visible.open_time.iloc[i]) for i in ticks],fontsize=9)
    lower=visible[['low',*MA_COLS]].min().min();upper=visible[['high',*MA_COLS]].max().max();pad=(upper-lower)*.08
    ax.set_ylim(lower-pad,upper+pad);ax.set_xlim(-1,len(visible)+1);ax.yaxis.tick_right();ax.grid(color='#c6ccd6',linestyle=(0,(1,4)),alpha=.7)
    for spine in ax.spines.values():spine.set_color('#dce1e8')
    direction={'LONG':'多','SHORT':'空'}.get(row.get('direction'),'全貌')
    title=f"{row['symbol']}  ·  {labeltf(minutes)}  ·  {direction}  |  {kind}"
    fig.text(.075,.945,title,fontsize=19,fontweight='bold',color='#17243b')
    close_text=bj(closes.max());fig.text(.075,.897,f"{row['venue'].upper()}  |  确认可见至 {close_text}（北京时间）  |  均线：CLOSE · 20/60/120",fontsize=11,color='#596170')
    reasons=row.get('hard_gate_failures',[])+row.get('reference_gate_failures',[])
    reasons=[REASONS.get(v,v) for v in reasons]
    if kind=='未通过原规则' and not reasons:reasons=['总分未达到原Grade-A门槛']
    foot='未通过：'+'；'.join(reasons) if reasons else ('达到原 Grade-A 条件；不代表此后会涨跌。' if kind=='原规则命中' else '仅显示截至22:00已收盘K线；此图不是命中声明。')
    fig.text(.075,.045,foot[:180],fontsize=9,color='#707888')
    ax.legend(loc='upper center',bbox_to_anchor=(.5,-.085),ncol=6,frameon=False,fontsize=9)
    fig.subplots_adjust(left=.075,right=.94,top=.85,bottom=.16);fig.savefig(dest);plt.close(fig)
    return {'visible_start_open_utc':visible.open_time.iloc[0].isoformat(),'visible_end_close_utc':closes.max().isoformat(),'visible_bars':len(visible),'image_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}

def run(planpath):
    plan=json.loads(planpath.read_text());exp=ROOT/plan['experiment_dir'];results=exp/'results';out=exp/'review'
    commit=ensure_committed([Path(__file__)])
    if out.exists():raise FileExistsError(out)
    out.mkdir();(out/'charts').mkdir();configure_chinese_font();cutoff=pd.Timestamp(plan['scan_end_utc'])
    matches=readl(results/'grade_a_matches.jsonl');scored=readl(results/'scored_candidates.jsonl')
    match_ids={r['sample_id'] for r in matches}
    jobs=[(r,'原规则命中','hit') for r in matches]
    # The first two rejected profiles for each TF ranked only by morphology evidence.
    for m in plan['timeframes_minutes']:
        rejected=[r for r in scored if r['bar_minutes']==m and r['sample_id'] not in match_ids and r.get('quality_tier')!='PERFECT_CANDIDATE']
        rejected.sort(key=lambda r:(len(r.get('hard_gate_failures',[])),-float(r.get('quality_score') or 0),r['sample_id']))
        seen=set()
        for r in rejected:
            key=r['symbol']
            if key in seen:continue
            jobs.append((r,'未通过原规则','rejected'));seen.add(key)
            if len(seen)==2:break
    specs={(s['symbol'],s['bar_minutes']):s for s in plan['sources']}
    for m in plan['timeframes_minutes']:
        if ('WIF',m) in specs:
            s=specs[('WIF',m)];jobs.append(({**s,'source_path':s['path']},'WIF 截止时刻全貌','context'))
    cache={};cards=[]
    for idx,(row,kind,status) in enumerate(jobs,1):
        path=row['source_path']
        if path not in cache:
            f=pd.read_csv(ROOT/path);f['open_time']=pd.to_datetime(f.open_time,utc=True)
            if (f.open_time+pd.Timedelta(minutes=row['bar_minutes'])).max()>cutoff:raise ValueError('unclipped source')
            cache[path]=add_six_mas(f)
        filename=f'{idx:03d}_{row["symbol"]}_{row["bar_minutes"]}m_{status}.png';meta=figure(cache[path],row,out/'charts'/filename,kind,cutoff)
        cards.append({'number':idx,'symbol':row['symbol'],'minutes':row['bar_minutes'],'venue':row['venue'],'direction':row.get('direction'),'status':status,'kind':kind,'image':'charts/'+filename,'time':bj(meta['visible_end_close_utc']),'sample_id':row.get('sample_id'),**meta})
    summary=json.loads((results/'summary.json').read_text());coverage=json.loads((results/'source_coverage.json').read_text());source_errors=json.loads((exp/'source_errors.json').read_text())
    table=[]
    for m in plan['timeframes_minutes']:
        cov=[s for s in coverage if s['bar_minutes']==m];raw=[r for r in scored if r['bar_minutes']==m];hit=[r for r in matches if r['bar_minutes']==m]
        table.append({'minutes':m,'source_count':len(cov),'covered_symbols':len({s['symbol'] for s in cov if s.get('confirmation_endpoints',0)>0}),'scored_candidates':len(raw),'matches':len(hit),'long':sum(r['direction']=='LONG' for r in hit),'short':sum(r['direction']=='SHORT' for r in hit)})
    def esc(v):return html.escape(str(v))
    rows=''.join(f'<tr><td>{labeltf(t["minutes"])}</td><td>{t["covered_symbols"]}</td><td>{t["scored_candidates"]}</td><td>{t["matches"]}</td><td>{t["long"]} / {t["short"]}</td></tr>' for t in table)
    cards_html=''.join(f'<article data-tf="{c["minutes"]}" data-status="{c["status"]}"><div class="caption"><b>{c["number"]:02d} · {esc(c["symbol"])} · {labeltf(c["minutes"])}</b><span class="badge {c["status"]}">{c["kind"]}</span><span>{c["time"]} 北京</span></div><a href="{c["image"]}" target="_blank"><img src="{c["image"]}" loading="lazy" alt="{esc(c["symbol"])} {c["kind"]}"></a></article>' for c in cards)
    doc='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>8月19日 · 双均线密集启动规则扫描</title><style>body{margin:0;background:#eef1f6;color:#17243b;font:16px system-ui}main{max-width:1400px;margin:auto;padding:32px 24px}h1{font-size:30px;margin:8px 0}.sub{color:#637085;line-height:1.8}.panel,article{background:white;border:1px solid #dae0e9;border-radius:14px;padding:20px;margin:20px 0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #e8edf3}.filters{position:sticky;top:0;background:#eef1f6ed;backdrop-filter:blur(12px);padding:14px 0;z-index:20;display:flex;gap:12px;align-items:center}select{font:inherit;padding:10px;border-radius:8px;border:1px solid #cbd3df}.caption{display:flex;align-items:center;gap:20px;margin-bottom:16px}img{width:100%;height:auto;display:block}.badge{padding:5px 10px;border-radius:7px;font-size:13px;background:#edf0f5}.hit{background:#ddf3e7;color:#17623f}.rejected{background:#fff0da;color:#966115}a{color:#2863b6}.count{color:#627083;font-size:14px}article[hidden]{display:none}</style><main><div class="sub">原 Grade-A 规则 · 历史时点回放</div><h1>2026年8月19日，18:00–22:00</h1><p class="sub">北京时间。检查此期间完成五根确认K线的信号；全部输入与图片不超过22:00。更早K线只供均线预热和形态上下文。使用原发现规则的 CLOSE 均线，不调整阈值。</p>'''
    doc+=f'<div class="panel"><h2>{len(matches)} 个去重命中</h2><table><thead><tr><th>周期</th><th>实际覆盖币种</th><th>进入严格评分</th><th>去重命中</th><th>多 / 空</th></tr></thead><tbody>{rows}</tbody></table><p class="sub">覆盖池为本地 Binance / OKX USDT 永续历史数据；同币优先最新可见覆盖、再优先 Binance。不是历史所有已退市币种全集。原规则来自15分数据，其他周期为保持参数不变的探索性回放。<br>本地曾发现{len(source_errors)}项源问题：0G已补回；US100、US500、XOM在目标日期后才上市。“未通过”和WIF全貌均不计入命中。短历史与覆盖详情见下载文件。</p><a href="results.csv">下载完整严格评分结果 CSV</a> · <a href="coverage.json">数据覆盖</a> · <a href="source_errors.json">缺失与错误</a></div>'
    doc+='<div class="filters"><select id="tf" aria-label="周期"><option value="all">全部周期</option>'+''.join(f'<option value="{m}">{labeltf(m)}</option>' for m in plan['timeframes_minutes'])+'</select><select id="status" aria-label="结果类型"><option value="all">全部图</option><option value="hit">仅原规则命中</option><option value="rejected">未通过示例</option><option value="context">WIF 全貌</option></select><span id="count" class="count"></span></div>'+cards_html
    doc+='''<p class="sub">每张图在标题所示时刻停止；未展示或评估后续收益。点击图可打开原始PNG。</p></main><script>function filter(){let n=0;document.querySelectorAll('article').forEach(e=>{e.hidden=!((tf.value==='all'||tf.value===e.dataset.tf)&&(status.value==='all'||status.value===e.dataset.status));if(!e.hidden)n++});document.getElementById('count').textContent=`显示 ${n} 张图`}const tf=document.getElementById('tf'),status=document.getElementById('status');tf.addEventListener('change',filter);status.addEventListener('change',filter);filter();</script></html>'''
    (out/'index.html').write_text(doc)
    pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in scored]).to_csv(out/'results.csv',index=False)
    for name,value in [('coverage.json',coverage),('source_errors.json',source_errors),('source_error_resolution.json',json.loads((exp/'source_error_resolution.json').read_text())),('manifest.json',{'builder_commit':commit,'cards':cards,'table':table,'summary':summary})]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'charts':len(cards),'matches':len(matches),'table':table},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);run(p.parse_args().plan)
