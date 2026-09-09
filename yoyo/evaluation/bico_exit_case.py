"""Owner-selected BICO exit audit; no training, optimization or live changes.

Inputs are completed OKX bars. MA protection uses the prior effective level,
updated only after each close. New next-open exit conventions are offline
hypotheses, not a claim that Pine's descriptive R box executes orders.
Future bars are read only to evaluate fixed outcomes and retrospective charts.
"""
from pathlib import Path
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
        exit_timing='next_open' if reason=='确认后下一开盘' else 'intrabar_unknown' if reason in ('固定3R','初始止损') else 'open' if reason=='初损跳空' else 'boundary_close',
        reason=reason,gross_r=(price-entry)/risk,net_r=(price-entry-.002*entry)/risk,net_pct=(gross-.002)*100,
        known_peak_r=(peak-entry)/risk,known_peak_price=peak,held_hours=j-i,protection=path)


def main():
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
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),holdout_consumption=1,
        control_count=len(controls),owner_selected_winner=True)
    (OUT/'manifest.json').write_text(json.dumps(extra,indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    end=min(len(b),max(r['exit_i'] for r in outcomes)+20); start=i-60
    bb=b.iloc[start:end]; xx=np.arange(len(bb));fig,axes=plt.subplots(2,1,figsize=(16,10),sharex=True,gridspec_kw={'height_ratios':[3,1]})
    for n,row in enumerate(bb.itertuples()):
        color='#159b83' if row.close>=row.open else '#df6578'
        axes[0].vlines(n,row.low,row.high,color=color,lw=.7)
        axes[0].add_patch(Rectangle((n-.35,min(row.open,row.close)),.7,max(abs(row.open-row.close),.000001),facecolor=color))
    for name in ('sma20','sma60','sma120'): axes[0].plot(xx,f[name].iloc[start:end],lw=.9,alpha=.6,label=name.upper())
    colors=['#777777','#8738bf','#c28e12','#0c7ba6','#e45e40','#222222']
    for n,(r,col) in enumerate(zip(outcomes,colors)):
        axes[0].scatter(r['exit_i']-start,r['exit_price'],color=col,marker='v',s=90,zorder=9)
        axes[0].annotate(f"{r['arm']} {r['net_r']:.1f}R",(r['exit_i']-start,r['exit_price']),xytext=(8,18+18*(n%2)),textcoords='offset points',fontsize=9,color=col)
    axes[0].axhline(.01179,color='#df6578',ls='--',lw=.8,label='Frozen initial stop')
    axes[0].axvline(i+1-start,color='#333',ls=':',lw=1)
    axes[0].legend(loc='upper left',ncol=4);axes[0].set_ylabel('USDT')
    axes[1].plot(xx,f.md.iloc[start:end],color='#4177d0',label='IMACD md');axes[1].plot(xx,f.sb.iloc[start:end],color='#db9437',label='Signal sb')
    axes[1].axhline(0,color='#888',lw=.8);axes[1].legend(loc='upper left')
    for ax in axes: ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    ticks=np.unique(np.linspace(0,len(bb)-1,8).astype(int));axes[1].set_xticks(ticks,[bb.index[x].tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for x in ticks])
    fig.suptitle('BICO 1H | Same entry, six fixed exit conventions\n194.48R is the observed high, not a filled exit; prices exclude full funding/market impact',fontsize=15)
    axes[1].set_xlabel('Beijing time | bar open');fig.tight_layout();fig.savefig(OUT/'exit_paths.png',dpi=150);plt.close(fig)
    report=ROOT/'analysis/p1_bico_194r_exit_case_20260910.md'
    lines=['# BICO 194R：最高浮盈与可执行退出',
        'Owner指定的事后赢家，仅用于退出路径解释，不用本例选最优参数，不代表预测胜率。',
        f"截图数值可对上：1R=0.01219−0.01179=0.00040；(0.08998−0.01219)/0.00040=194.475R。匹配信号开盘北京时间{extra['signal_open_bjt']}，确认收盘{extra['signal_close_bjt']}，近零{extra['near_zero_bars']}根。",
        '当前Pine框记录参考存续期的最大有利影线，保护失守仅改变状态，不会实际卖出，也没有专用退出警报。下面把预定规则转成离线下一open退出；初止损/固定TP在盘中处理。不是Pine已有自动交易。',
        f"所有分支同一次下一open入场{outcomes[0]['entry']:.5f}，同一冻结止损0.01179。R按此实际参考与止损的差计算；往返成本0.2%。",
        '|退出规则|触发确认北京时间|退出参考价|毛R|扣成本净R|单笔静态净收益|随机对照平均净R|',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in outcomes:
        mean=control.loc[control.arm.eq(r['arm']),'net_r'].mean()
        lines.append(f"|{NAMES[r['arm']]}|{r['trigger_time'] or r['exit_time']+'（盘中时刻未知）'}|{r['exit_price']:.5f}|{r['gross_r']:.2f}|{r['net_r']:.2f}|{r['net_pct']:.2f}%|{mean:.2f}|")
    lines+=['![全局价格与六个退出点](output/bico_exit_case_20260910/exit_paths.png)',
        '## 如何使用这些结果',
        '先选并固定保护速度，之后逐根按已确认条件执行。宽保护会承受更大的浮盈回吐，窄保护可能在大涨前的普通回踩就退出。194R高点无法在入场时知道，更不能在事后把最高影线当成止盈成交。',
        '单向MA保护：收盘跌破上一根有效保护线才触发，下一open参考退出；否则只在close>MA时把保护线上移到max(旧值,当前MA)。初损始终有效。md/sb金死叉在上涨途中可以多次出现；其退出结果在表中单独验证。',
        '## 风险与诚实声明',
        '只有一个Owner已知赢家，AUC/预测胜率/参数最优性/置换显著性不适用。20个同月同事前波动桶随机参考说明结果与持仓起点关系，不能纠正事后选赢家；对照原箱用过去29根低点近似，不是有效蓄势事件。对照含相同末尾标记，边界状态见controls.csv。',
        '未计完整资金费、点差和市场冲击，下一open是假设参考成交，剧烈跳跃时真实成交可能更差。R不是本金翻倍数。来源历史confirm列未保留；时间闭合与SHA并非当年first-seen验证。',
        '本配置第1次消耗holdout，Owner已授权任意历史。仅描述性核验，不调参、不改Pine、不下单。',
        '真实的追踪止损是一类会触发退出委托的订单，与当前画框不同：[OKX追踪止损说明](https://www.okx.com/en-gb/help/how-to-use-trailing-stop)。',
        '## 复现与证据',
        '```bash\n.venv/bin/python -m yoyo.evaluation.bico_exit_case\n```',
        '[退出账本](output/bico_exit_case_20260910/exits.csv) · [随机参考](output/bico_exit_case_20260910/controls.csv)',
        '```json\n'+json.dumps(extra,indent=2,ensure_ascii=False)+'\n```']
    report.write_text('\n\n'.join(lines));subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/md_to_html.py'),str(report),'--out-dir',str(ROOT/'analysis/html')],check=True)
    print(json.dumps(extra,ensure_ascii=False));print(table[['arm','entry','exit_price','exit_time','gross_r','net_r','net_pct','known_peak_r','reason']].round(5).to_string(index=False))


if __name__=='__main__': main()
