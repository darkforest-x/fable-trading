"""Owner-selected BICO exit audit; no training, optimization or live changes.

Inputs are completed OKX bars. MA protection uses the prior effective level,
updated only after each close. New next-open exit conventions are offline
hypotheses, not a claim that Pine's descriptive R box executes orders.
Future bars are read only to evaluate fixed outcomes and retrospective charts.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.data.altcoin_features import build_altcoin_features
from yoyo.evaluation.imacd_formation_research import aggregate, read_prefix

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'analysis/output/bico_exit_case_20260910'
PLAN=ROOT/'experiments/active/exp-bico-exit-case-20260910-v1/PROJECT_PLAN.md'
SOURCE=ROOT/'data/kline_fetched/okx_BICO_USDT_SWAP_15m_41914.csv'
ARMS=('fixed3r','episode','cross','sma20','sma60','sma120')
NAMES=dict(fixed3r='固定3R',episode='主线回零或原箱底失守',cross='主线下穿信号线',
           sma20='SMA20单向保护',sma60='SMA60单向保护',sma120='SMA120单向保护')


def simulate(b,f,i,stop,arm,zone):
    entry=float(b.open.iloc[i+1]); risk=entry-stop
    if not 0<stop<entry: raise ValueError('invalid frozen initial risk')
    level=max(stop,float(f[arm].iloc[i])) if arm.startswith('sma') and f.close.iloc[i]>f[arm].iloc[i] else stop
    pending=False; trigger=None; peak=entry; path=[]
    for j in range(i+1,len(b)):
        o,h,lo,c=b.iloc[j][['open','high','low','close']].to_numpy(float)
        path.append((j,level))
        if o<=stop: price=o; reason='初损跳空'; break
        if pending: price=o; reason='确认后下一开盘'; break
        if arm=='fixed3r' and o>=entry+3*risk:
            price=entry+3*risk; peak=max(peak,price); reason='固定3R开盘跳过'; break
        if lo<=stop: price=stop; reason='初始止损'; break
        if arm=='fixed3r' and h>=entry+3*risk:
            price=entry+3*risk; peak=max(peak,price); reason='固定3R'; break
        peak=max(peak,h)
        if j==len(b)-1: price=c; reason='数据末尾标记'; break
        if arm=='episode': broken=f.md.iloc[j]<=0 or c<zone
        elif arm=='cross': broken=f.md.iloc[j]<f.sb.iloc[j] and f.md.iloc[j-1]>=f.sb.iloc[j-1]
        elif arm.startswith('sma'): broken=c<level
        else: broken=False
        if broken: pending=True; trigger=j
        if arm.startswith('sma') and c>f[arm].iloc[j]: level=max(level,float(f[arm].iloc[j]))
    gross=price/entry-1
    return dict(arm=arm,signal_i=i,entry_i=i+1,exit_i=j,trigger_i=trigger,entry=entry,stop=stop,risk=risk,
        exit_price=float(price),exit_time=str(b.index[j].tz_convert('Asia/Shanghai')),
        trigger_time=str((b.index[trigger]+pd.Timedelta(hours=1)).tz_convert('Asia/Shanghai')) if trigger is not None else None,
        exit_timing='next_open' if reason=='确认后下一开盘' else 'intrabar_unknown' if reason in ('固定3R','初始止损') else 'open' if reason in ('初损跳空','固定3R开盘跳过') else 'boundary_close',
        reason=reason,gross_r=(price-entry)/risk,net_r=(price-entry-.002*entry)/risk,net_pct=(gross-.002)*100,
        known_peak_r=(peak-entry)/risk,known_peak_price=peak,
        peak_to_exit_drop_pct=(1-price/peak)*100,
        held_hours_lower=j-i if reason=='数据末尾标记' else j-i-1,
        held_hours_upper=j-i if reason in ('固定3R','初始止损','数据末尾标记') else j-i-1,
        protection=path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-pass',type=int,default=1,help='Recorded same-configuration holdout consumption ordinal; no rule change.')
    args=parser.parse_args()
    sources=[Path(__file__),PLAN,ROOT/'yoyo/data/altcoin_features.py',ROOT/'yoyo/evaluation/imacd_formation_research.py']
    for p in sources:
        if subprocess.check_output(['git','show','HEAD:'+str(p.relative_to(ROOT))],cwd=ROOT)!=p.read_bytes():
            raise ValueError('Commit builder first: '+str(p))
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!='5a6131c4e8ca7e61094782991a7109bf3d3e98a89147caea1cd909dd758f9c48':
        raise ValueError('Frozen BICO source changed')
    raw=read_prefix(SOURCE,pd.Timestamp('2026-08-12T00:00Z')); b=aggregate(raw,60)
    f=build_altcoin_features(b); f['close']=b.close
    mask=(b.index>=pd.Timestamp('2026-08-02T00:00Z'))&(b.index<pd.Timestamp('2026-08-04T00:00Z'))&f.release_side.eq(1)&np.isclose(b.close,.01219,rtol=0,atol=1e-9)
    anchors=np.flatnonzero(mask)
    if len(anchors)!=1: raise ValueError('Screenshot release not uniquely matched: '+str(anchors))
    i=int(anchors[0]); zone=float(f.release_zone_low.iloc[i]); outcomes=[simulate(b,f,i,.01179,a,zone) for a in ARMS]
    # Outcome-blind, same-month and same volatility-bucket random reference.
    clocks=b.index+pd.Timedelta(hours=1)
    rank=(f.atr/b.close).rolling(240).rank(pct=True).mul(5).apply(np.ceil).clip(1,5)
    eligible=np.flatnonzero((clocks.strftime('%Y-%m')==clocks[i].strftime('%Y-%m'))&rank.eq(rank.iloc[i])&f.md.gt(0)&f.release_side.eq(0)&(np.arange(len(b))<len(b)-1))
    controls=np.sort(np.random.default_rng(20260910).choice(eligible,min(20,len(eligible)),replace=False))
    multiple=(b.open.iloc[i+1]-.01179)/f.atr.iloc[i]; reference=[]
    for k in controls:
        for arm in ARMS:
            r=simulate(b,f,int(k),float(b.open.iloc[k+1]-multiple*f.atr.iloc[k]),arm,float(b.low.iloc[max(0,k-29):k].min()))
            reference.append({k:v for k,v in r.items() if k!='protection'})
    OUT.mkdir(parents=True,exist_ok=True)
    table=pd.DataFrame([{k:v for k,v in r.items() if k!='protection'} for r in outcomes]); table.to_csv(OUT/'exits.csv',index=False)
    control=pd.DataFrame(reference);control.to_csv(OUT/'controls.csv',index=False)
    extra=dict(signal_open_bjt=str(b.index[i].tz_convert('Asia/Shanghai')),signal_close_bjt=str(clocks[i].tz_convert('Asia/Shanghai')),
        signal_close=float(b.close.iloc[i]),near_zero_bars=int(f.near_zero_bars.iloc[i]),atr=float(f.atr.iloc[i]),zone_low=zone,
        screenshot_peak_r=(.08998-.01219)/(.01219-.01179),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),holdout_consumption=args.audit_pass,
        control_count=len(controls),control_target=20,owner_selected_winner=True,
        audit_notes='Pass 1 frozen run; pass 2 independent raw-bar replay; pass 3 same-rule clock/report QA. No threshold selection.')
    (OUT/'manifest.json').write_text(json.dumps(extra,indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Rectangle
    fonts={item.name for item in font_manager.fontManager.ttflist}
    for name in ('Arial Unicode MS','PingFang SC','Heiti TC'):
        if name in fonts: plt.rcParams['font.family']=name; break
    plt.rcParams['axes.unicode_minus']=False
    end=min(len(b),max(r['exit_i'] for r in outcomes)+20); start=i-60
    bb=b.iloc[start:end]; xx=np.arange(len(bb));fig,axes=plt.subplots(2,1,figsize=(16,10),sharex=True,gridspec_kw={'height_ratios':[3,1]})
    for n,row in enumerate(bb.itertuples()):
        color='#159b83' if row.close>=row.open else '#df6578'
        axes[0].vlines(n,row.low,row.high,color=color,lw=.7)
        axes[0].add_patch(Rectangle((n-.35,min(row.open,row.close)),.7,max(abs(row.open-row.close),.000001),facecolor=color))
    for name in ('sma20','sma60','sma120'): axes[0].plot(xx,f[name].iloc[start:end],lw=.9,alpha=.6,label=name.upper())
    protection=np.array(next(r['protection'] for r in outcomes if r['arm']=='sma60'))
    axes[0].plot(protection[:,0]-start,protection[:,1],ls='--',lw=1.4,color='#dd642e',label='SMA60 已生效保护线')
    colors=['#777777','#8738bf','#c28e12','#0c7ba6','#e45e40','#222222']
    labels=dict(fixed3r='固定 3R',episode='IMACD 回零',cross='IMACD 死叉',sma20='SMA20 保护',sma60='SMA60 保护',sma120='SMA120 保护')
    offsets=[(8,18),(24,48),(6,15),(-110,35),(12,28),(-120,-30)]
    for n,(r,col) in enumerate(zip(outcomes,colors)):
        axes[0].scatter(r['exit_i']-start,r['exit_price'],color=col,marker='v',s=90,zorder=9)
        axes[0].annotate(f"{labels[r['arm']]} · {r['net_r']:.1f}R",(r['exit_i']-start,r['exit_price']),xytext=offsets[n],textcoords='offset points',fontsize=10,color=col,arrowprops=dict(arrowstyle='-',color=col,lw=.6))
    axes[0].axhline(.01179,color='#df6578',ls='--',lw=.8,label='冻结初始止损 0.01179')
    axes[0].axvline(i+1-start,color='#333',ls=':',lw=1)
    axes[0].annotate('入场参考 0.01219\n08-02 23:00', (i+1-start,outcomes[0]['entry']),xytext=(-100,35),textcoords='offset points',fontsize=10,arrowprops=dict(arrowstyle='-',color='#555'))
    peak_i=int(np.argmax(bb.high.to_numpy()))
    axes[0].annotate('最高影线 0.08998\n最大浮盈 194.48R', (peak_i,bb.high.iloc[peak_i]),xytext=(-140,-8),textcoords='offset points',fontsize=11,color='#128979',arrowprops=dict(arrowstyle='-',color='#128979'))
    axes[0].legend(loc='upper left',ncol=4);axes[0].set_ylabel('USDT')
    axes[1].plot(xx,f.md.iloc[start:end],color='#4177d0',label='IMACD md');axes[1].plot(xx,f.sb.iloc[start:end],color='#db9437',label='Signal sb')
    axes[1].axhline(0,color='#888',lw=.8);axes[1].legend(loc='upper left')
    for ax in axes: ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    ticks=np.unique(np.linspace(0,len(bb)-1,8).astype(int));axes[1].set_xticks(ticks,[bb.index[x].tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for x in ticks])
    fig.suptitle('BICO · 1 小时｜同一笔启动，六种事先固定的退出规则\n标记为离线退出参考；R 扣入场名义 0.2% 静态成本，未计完整资金费与冲击',fontsize=16)
    axes[1].set_xlabel('北京时间｜横轴为 K 线开盘时刻');fig.tight_layout();fig.savefig(OUT/'exit_paths.png',dpi=150);plt.close(fig)
    report=ROOT/'analysis/p1_bico_194r_exit_case_20260910.md'
    lines=['# BICO 194R：最高浮盈与可执行退出',
        'Owner指定的事后赢家，仅用于退出路径解释，不用本例选最优参数，不代表预测胜率。',
        f"截图数值可对上：1R=0.01219−0.01179=0.00040；(0.08998−0.01219)/0.00040=194.475R。匹配信号开盘北京时间{extra['signal_open_bjt']}，确认收盘{extra['signal_close_bjt']}，近零{extra['near_zero_bars']}根。",
        '当前Pine框记录参考存续期的最大有利影线，保护失守仅改变状态，不会实际卖出，也没有专用退出警报。下面把预定规则转成离线下一open退出；初止损/固定TP在盘中处理。不是Pine已有自动交易。',
        f"所有分支同一次下一open入场{outcomes[0]['entry']:.5f}，同一冻结止损0.01179。R按此实际参考与止损的差计算；往返成本0.2%。",
        f'辅助对照原计划20个，严格匹配实际只有{len(controls)}个。保留缺样，不放宽条件；以下对照列不能证明策略优势。',
        f'|退出规则|触发确认北京时间|退出参考价|毛R|扣成本净R|单笔静态净收益|匹配参考净R（仅{len(controls)}个）|',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in outcomes:
        mean=control.loc[control.arm.eq(r['arm']),'net_r'].mean()
        lines.append(f"|{NAMES[r['arm']]}|{r['trigger_time'] or r['exit_time']+'（盘中时刻未知）'}|{r['exit_price']:.5f}|{r['gross_r']:.2f}|{r['net_r']:.2f}|{r['net_pct']:.2f}%|{mean:.2f}|")
    lines+=['![全局价格与六个退出点](output/bico_exit_case_20260910/exit_paths.png)',
        '## 如何使用这些结果',
        '本例固定3R在8月3日已退出；SMA20保护和IMACD死叉都在8月4日退出，错过后续主升段。SMA60保护直到8月9日才触发，留下98.71R，但已经较0.08998最高影线回落42.54%。等主线回零留下80.31R。本例SMA60最多，不等于其他币和其他阶段也最好。',
        '执行规则必须在开仓前固定：初始止损作为盘中风险底线；持有期间保护线只向盈利方向移动；收盘确认失守才发退出提醒，并按下一开盘估算退出。不能每次回踩临时换更慢的均线，否则退出规则就失效了。当前Pine尚未把保护失守做成专用退出警报。',
        '先选并固定保护速度，之后逐根按已确认条件执行。宽保护会承受更大的浮盈回吐，窄保护可能在大涨前的普通回踩就退出。194R高点无法在入场时知道，更不能在事后把最高影线当成止盈成交。',
        '单向MA保护：收盘跌破上一根有效保护线才触发，下一open参考退出；否则只在close>MA时把保护线上移到max(旧值,当前MA)。初损始终有效。md/sb金死叉在上涨途中可以多次出现；其退出结果在表中单独验证。',
        '## 风险与诚实声明',
        f'只有一个Owner已知赢家，AUC/预测胜率/参数最优性/置换显著性不适用。预定20个匹配参考实际仅{len(controls)}个，同属本币同月，可能处于相同趋势段，不能纠正事后选赢家，也没有足够独立对照进行推断；对照原箱用过去29根低点近似，不是有效蓄势事件。边界状态见controls.csv。',
        '未计完整资金费、点差和市场冲击，下一open是假设参考成交，剧烈跳跃时真实成交可能更差。R不是本金翻倍数。来源历史confirm列未保留；时间闭合与SHA并非当年first-seen验证。',
        f'本配置累计第{args.audit_pass}次消耗holdout，Owner已授权任意历史：首次冻结运行；第二次独立源K复核；第三次相同规则的时钟与报告修正。六种退出的收益和价格不变，不调参、不改Pine、不下单。',
        '真实的追踪止损是一类会触发退出委托的订单，与当前画框不同：[OKX追踪止损说明](https://www.okx.com/en-gb/help/how-to-use-trailing-stop)。',
        '## 复现与证据',
        '```bash\n.venv/bin/python -m yoyo.evaluation.bico_exit_case --audit-pass 3\n```',
        '[退出账本](output/bico_exit_case_20260910/exits.csv) · [随机参考](output/bico_exit_case_20260910/controls.csv)',
        '```json\n'+json.dumps(extra,indent=2,ensure_ascii=False)+'\n```']
    body=''
    for n,line in enumerate(lines):
        body+=('' if n==0 else '\n' if line.startswith('|') and lines[n-1].startswith('|') else '\n\n')+line
    report.write_text(body+'\n');subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/md_to_html.py'),str(report),'--out-dir',str(ROOT/'analysis/html')],check=True)
    print(json.dumps(extra,ensure_ascii=False));print(table[['arm','entry','exit_price','exit_time','gross_r','net_r','net_pct','known_peak_r','reason']].round(5).to_string(index=False))


if __name__=='__main__': main()
