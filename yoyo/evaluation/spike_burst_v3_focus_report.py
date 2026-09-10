"""Read-only reporting of frozen V3/A/B focus outcomes and complete review charts.

This builder never imports/calls a detector, an execution simulator, or a scorer.
It authenticates the fixed prepared/validation receipts before reading result
CSVs or feature pickles, uses saved barwise states, and embeds five review PNGs
in HTML. The two additional examples are selected AFTER outcomes by a fixed
largest-future24-peak rule; they illustrate mechanisms, not prospective proof.
The report is an event-study report, never an account NAV/MDD claim.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-spike-v3-focus-20260910-v1'
OUT = EXP/'results'
MD = ROOT/'analysis/p1_spike_v3_focus_20260910.md'
HTML = ROOT/'analysis/html/p1_spike_v3_focus_20260910.html'
PREPARED_SHA = 'a219b7fe8b25ce165121a988fed1b16b572211de932f74abdea1af4de740eeb4'
VALIDATION_SHA = 'b94367b06517fd4639473dbf6a4806cdf9d12dddbb7cfa59c3a1f98f5f8d121b'
ORIGINAL = ROOT/'experiments/active/exp-spike-burst-launch-recall-20260910-v2/results'
ORIGINAL_RECALL_SHA = '1eca062ab758fb4ea86f0936bfcd459e241c80aad7a97585b2ef74f66f145056'
ORIGINAL_TRADE_SHA = '07762c6f1f50e2ffb03917b3304f908605f27dbef9fd69b10d20aff5138d0d02'
ORIGINAL_SIGNALS_SHA = '308bbe4d2d1f8622aba5b10ee0463d01fbebf54cb48e2aed21341f19924c80bd'
MISS_DIAGNOSTIC_SHA = 'fc7835025892b9105bdeb0a6360fd7c724b9e8f36d793e54fc9002b48b295987'
START, END = pd.Timestamp('2026-07-10T00:00Z'), pd.Timestamp('2026-09-09T00:00Z')
BJT = 'Asia/Shanghai'
NAMES = {'v1':'原版 V1（历史对照）', 'v3':'V3 基准',
         'reference':'A · 参考占用', 'near_box':'B · 完整近零盒'}
STAGES = {'early':'新预警', 'confirmed':'确认升级', 'original':'原版信号'}
COLORS = {'v3':'#B57618', 'reference':'#077E71', 'near_box':'#6653A6'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, digest):
    p = Path(path).resolve()
    if sha(p) != digest:
        raise ValueError('Changed authenticated input: '+str(p))
    return p


def clean(value):
    if isinstance(value, dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [clean(v) for v in value]
    if isinstance(value, (np.integer,np.bool_)): return value.item()
    if isinstance(value, (float,np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA: return None
    if isinstance(value, (pd.Timestamp,Path)): return str(value)
    return value


def put_json(path, data):
    Path(path).write_text(json.dumps(clean(data),ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def authenticate():
    """Pin exact historical inputs; fresh detection or silent source drift fails."""
    prepared_path = checked(OUT/'prepared_manifest.json', PREPARED_SHA)
    validation_path = checked(OUT/'validation_manifest.json', VALIDATION_SHA)
    p, v = json.loads(prepared_path.read_text()), json.loads(validation_path.read_text())
    if p['status'] != 'complete' or v['status'] != 'complete' or v['prepared_sha'] != PREPARED_SHA:
        raise ValueError('Incomplete or unlinked frozen results')
    if p['config'] != v['config'] or p['source_pins'] != v['source_pins']:
        raise ValueError('Prepared and evaluated configurations differ')
    inputs = {str(prepared_path):PREPARED_SHA, str(validation_path):VALIDATION_SHA}
    for a in p['artifacts']+v['artifacts']:
        path = checked(a['path'], a['sha256']); inputs[str(path)] = a['sha256']
    for path, digest in p['sources'].items():
        checked(path,digest)
    for rel,digest in p['source_pins'].items():
        checked(ROOT/rel,digest)
    for name,digest in [('recall_summary.csv',ORIGINAL_RECALL_SHA),('trade_summary.csv',ORIGINAL_TRADE_SHA),('signals.csv.gz',ORIGINAL_SIGNALS_SHA)]:
        path = checked(ORIGINAL/name,digest); inputs[str(path)] = digest
    return p,v,inputs


def read(name):
    return pd.read_csv(OUT/name,float_precision='round_trip')


def number(v, digits=2):
    return '不适用/未知' if pd.isna(v) else f'{float(v):,.{digits}f}'


def pct(v):
    return '不适用/未知' if pd.isna(v) else f'{100*float(v):.2f}%'


def price(v):
    return '未知' if pd.isna(v) else f'{float(v):.8g}'


def stamp(v):
    if pd.isna(v): return '未知'
    t = pd.Timestamp(v)
    if t.tzinfo is None: t=t.tz_localize('UTC')
    return t.tz_convert(BJT).strftime('%m-%d %H:%M')


def markdown_table(frame, specification):
    """Columns are key / Chinese label / formatter; never alter numeric inputs."""
    lines=['| '+' | '.join(label for _,label,_ in specification)+' |',
           '|'+'|'.join('---' for _ in specification)+'|']
    for row in frame.to_dict('records'):
        cells=[]
        for key,_,formatter in specification:
            val=row.get(key,np.nan)
            text=formatter(val) if formatter else str(val)
            cells.append(text.replace('|','/').replace('\n',' '))
        lines.append('| '+' | '.join(cells)+' |')
    return '\n'.join(lines)


def arm_name(v): return NAMES.get(v,str(v))
def integer(v): return '未知' if pd.isna(v) else str(int(v))


def choose_examples(labels, detections):
    """Deterministic POST-HOC illustrations; never used as performance samples."""
    pool=labels[labels.label.eq('positive')&labels.decision_time.ge(START)&labels.decision_time.lt(END)].copy()
    for arm,column in [('v3','base_hit'),('reference','a_hit')]:
        d=detections[detections.arm.eq(arm)&detections.stage.eq('early')][['instrument','event_i','hit_1']]
        pool=pool.merge(d.rename(columns={'hit_1':column}),on=['instrument','event_i'],validate='one_to_one')
    pool=pool[pool.base_hit.eq(True)]
    result=[]
    for kept,name in [(False,'largest_missed_by_a'),(True,'largest_retained_by_a')]:
        candidates=pool[pool.a_hit.eq(kept)].sort_values(
            ['future_peak_return','instrument','event_i'],ascending=[False,True,True],kind='stable')
        if candidates.empty: raise ValueError('Missing required post-hoc illustration: '+name)
        row=candidates.iloc[0].to_dict()
        row.update(key=name,kept_by_a=kept,selection='largest future24 high return among original V3 timely positives; tie instrument/event_i')
        result.append(row)
    return result


def _segments(mask):
    starts=np.flatnonzero(mask & ~np.r_[False,mask[:-1]])
    ends=np.flatnonzero(mask & ~np.r_[mask[1:],False])
    return zip(starts,ends)


def render_case(case, frame, state, events, original_events, destination):
    """Full OHLC review only; saved signal/state columns define every marker."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle,Patch
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter
    plt.rcParams['font.sans-serif']=['Arial Unicode MS','PingFang SC','Heiti TC','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    if case.get('fixed_window'):
        begin=pd.Timestamp('2026-08-18T00:00',tz=BJT).tz_convert('UTC')
        end=begin+pd.Timedelta(hours=96)
        selected=np.flatnonzero((frame.index>=begin)&(frame.index<end))
    else:
        center=int(case['event_i'])
        begin_i=max(0,min(center-48,len(frame)-96))
        selected=np.arange(begin_i,min(begin_i+96,len(frame)))
    if len(selected)!=96: raise ValueError('Review case lacks complete96 bars: '+case['key'])
    f=frame.iloc[selected];s=state.iloc[selected]
    if not np.array_equal(s.decision_i.to_numpy(),selected):
        raise ValueError('Saved state/price positions differ')
    expected=f.index+pd.Timedelta(hours=1)
    if not np.array_equal(pd.DatetimeIndex(pd.to_datetime(s.decision_time,utc=True)).as_unit('ns').asi8, expected.as_unit('ns').asi8):
        raise ValueError('Saved state/price close clocks differ')
    x=np.arange(len(f)); clock=expected.tz_convert(BJT)
    focus=pd.Timestamp(case['focus_time'])
    focus_x=int(np.argmin(np.abs(expected.asi8-focus.value)))
    fig,axes=plt.subplots(5,1,figsize=(16,15),sharex=True,
        gridspec_kw={'height_ratios':[3.1,3.1,3.1,1.4,1.1]},constrained_layout=True)
    fig.patch.set_facecolor('#FFFFFF')
    span=float(f.high.max()-f.low.min())
    refs=(s.reference_reference_active.eq(True)|s.reference_reference_before.eq(True)).to_numpy()
    bars_up=f.close.ge(f.open).to_numpy()
    bar_colors=np.where(bars_up,'#089C84','#D85560')
    ma_colors=['#318B87','#6CA7A0','#456EB1','#8FA4C8','#555966','#9A9DA5']
    rows=[]
    for ax,arm in zip(axes[:3],('v3','reference','near_box')):
        for start,end in _segments(refs):
            ax.axvspan(start-.5,end+.5,color='#9D8FC2',alpha=.13,zorder=0)
        ax.axvspan(focus_x+.5,95.5,color='#74B8D5',alpha=.06,zorder=0)
        ax.axvline(focus_x,color='#516675',ls='--',lw=.9,alpha=.7)
        for j,r in enumerate(f.itertuples()):
            ax.vlines(j,r.low,r.high,color=bar_colors[j],linewidth=.85,zorder=2)
            height=max(abs(r.close-r.open),max(span*0.0007,1e-12))
            ax.add_patch(Rectangle((j-.31,min(r.open,r.close)),.62,height,
                facecolor=bar_colors[j],edgecolor=bar_colors[j],linewidth=.4,zorder=3))
        for col,color in zip(('s20','e20','s60','e60','s120','e120'),ma_colors):
            ax.plot(x,f[col].to_numpy(),lw=.9,color=color,alpha=.72,zorder=1)
        early=s[arm+'_early'].eq(True).to_numpy();child=s[arm+'_confirmed'].eq(True).to_numpy()
        for rank,j in enumerate(np.flatnonzero(early|child)):
            global_i=int(selected[j]);at=events[events.arm.eq(arm)&events.decision_i.eq(global_i)]
            kinds='预警+确认' if early[j] and child[j] else '确认' if child[j] else '预警'
            color='#087E77' if child[j] else '#AF7212'
            marker='D' if child[j] else '^'
            p=float(f.close.iloc[j])
            ax.scatter(j,p,c=color,marker=marker,s=42,edgecolors='white',linewidths=.5,zorder=6)
            label=f'{clock[j]:%m-%d %H:%M}\n{kinds} {price(p)}'
            ax.annotate(label,(j,p),xytext=(0,13+(rank%3)*16),textcoords='offset points',
                ha='center',va='bottom',fontsize=6.5,color=color,
                bbox=dict(boxstyle='round,pad=.22',fc='white',ec='none',alpha=.85),
                arrowprops=dict(arrowstyle='-',color=color,lw=.5),zorder=7)
            for event in at.to_dict('records'):
                rows.append(dict(arm=arm,stage=event['stage'],time=event['decision_time'],
                    signal_close=event['signal_close'],parent_time=event['parent_decision_time'],
                    confirm_age=event['confirm_age']))
        original_count = 0
        if arm=='v3':
            original_slice=original_events[original_events.decision_i.isin(selected)]
            original_count=len(original_slice)
            for old_event in original_slice.to_dict('records'):
                j=int(old_event['decision_i'])-int(selected[0])
                p=float(old_event['signal_close'])
                if p != float(f.close.iloc[j]): raise ValueError('Historical original marker price differs')
                ax.scatter(j,p,c='#555B67',marker='*',s=110,edgecolors='white',linewidths=.6,zorder=8)
                ax.annotate(f'V1 {stamp(old_event["decision_time"])}\n{price(p)}',(j,p),
                    xytext=(0,-22),textcoords='offset points',ha='center',va='top',fontsize=6.5,
                    color='#444A55',bbox=dict(boxstyle='round,pad=.2',fc='white',ec='none',alpha=.9),
                    arrowprops=dict(arrowstyle='-',color='#555B67',lw=.5),zorder=8)
                rows.append(dict(arm='v1',stage='original',time=old_event['decision_time'],
                    signal_close=p,parent_time=pd.NaT,confirm_age=np.nan))
        if arm=='near_box':
            for col,color in [('near_box_boxHigh','#A38B35'),('near_box_boxLow','#A38B35')]:
                ax.plot(x,s[col].to_numpy(),lw=.8,ls=':',color=color,alpha=.8)
        ax.set_title(f'{NAMES[arm]} · {int(early.sum())}次新预警 / {int(child.sum())}次确认升级'+(f' · 原版V1灰星 {original_count}次' if arm=='v3' else ''),
                     loc='left',fontsize=11,fontweight='bold',color=COLORS[arm])
        ax.set_ylim(float(f.low.min())-span*.08,float(f.high.max())+span*.22)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:.6g}'))
        ax.grid(alpha=.14);ax.spines[['top','right']].set_visible(False)
    axes[0].legend(handles=[Line2D([],[],marker='*',color='#555B67',ls='',markersize=9,label='灰星：已保存原版V1真实信号'),Patch(facecolor='#9D8FC2',alpha=.25,label='A的信号收盘参考占用（不是实际成交持仓）'),
        Patch(facecolor='#74B8D5',alpha=.16,label='参考时点之后的走势，仅用于复盘')],loc='upper left',fontsize=8)
    axes[3].plot(x,f.md.to_numpy(),color='#416FBD',lw=1.5,label='IMACD')
    axes[3].plot(x,f.sb.to_numpy(),color='#BF8027',lw=1.4,label='信号线')
    axes[3].axhline(0,color='#697580',lw=.8)
    axes[3].legend(loc='upper left',fontsize=8,ncol=2);axes[3].set_ylabel('IMACD')
    axes[4].bar(x,f.volume.to_numpy(),color=bar_colors,width=.65,alpha=.75)
    axes[4].set_ylabel('成交量')
    for ax in axes[3:]:
        ax.axvline(focus_x,color='#516675',ls='--',lw=.9)
        ax.axvspan(focus_x+.5,95.5,color='#74B8D5',alpha=.06)
        ax.grid(alpha=.14);ax.spines[['top','right']].set_visible(False)
    ticks=np.arange(0,96,12)
    axes[4].set_xticks(ticks,[clock[j].strftime('%m-%d\n%H:%M') for j in ticks],fontsize=9)
    axes[4].set_xlabel('横轴：每根已收盘北京时间；标记与报价均在实际发生根，不回填')
    subtitle='固定日期对照' if case.get('fixed_window') else '事后按未来24根最高涨幅选择，仅作机制说明'
    fig.suptitle(f'{case["asset"]} · 1H · 完整96根前后对照\n{subtitle}；历史复盘图不是检测器输入',fontsize=15,fontweight='bold')
    destination.parent.mkdir(exist_ok=True)
    fig.savefig(destination,dpi=140,facecolor='white')
    plt.close(fig)
    return dict(case,first_open=f.index[0],last_open=f.index[-1],first_close=expected[0],last_close=expected[-1],
                bars=96,image=str(destination),image_sha256=sha(destination),signals=rows)


def run(native_qa=None, miss_diagnostic=None):
    builder=Path(__file__).resolve();rel=str(builder.relative_to(ROOT))
    if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=builder.read_bytes():
        raise ValueError('Commit this exact report builder before generating artifacts')
    if MD.exists() or HTML.exists() or (OUT/'focus_report_manifest.json').exists():
        raise ValueError('Refusing to overwrite the completed report')
    prepared,validation,inputs=authenticate()
    matching=json.loads((OUT/'matching.json').read_text())
    signals=read('signals.csv.gz');labels=read('labels.csv.gz');det=read('detections.csv.gz')
    original_signals=pd.read_csv(checked(ORIGINAL/'signals.csv.gz',ORIGINAL_SIGNALS_SHA),float_precision='round_trip')
    original_signals=original_signals[original_signals.arm.eq('v1')]
    for df in (signals,labels): df['decision_time']=pd.to_datetime(df.decision_time,utc=True)
    retention,trade,scores=read('retention_summary.csv'),read('trade_summary.csv'),read('score_summary.csv')
    full=retention[retention.period.eq('full')&retention.stage.eq('early')].set_index('arm')
    fixed_labels=labels[labels.decision_time.ge(START)&labels.decision_time.lt(END)]
    if len(fixed_labels)!=8046 or fixed_labels.label.eq('positive').sum()!=1660:
        raise ValueError('Report denominator drift')
    if int(full.loc['v3','large_positive_events'])!=947:
        raise ValueError('Large-move subgroup drift')
    cases=[]
    for asset in ('HYPE','NEAR','PEPE'):
        jobs=[j for j in matching['jobs'] if j['asset']==asset]
        if len(jobs)!=1:raise ValueError('Ambiguous fixed illustration source '+asset)
        focus=pd.Timestamp('2026-08-20T00:00' if asset=='HYPE' else '2026-08-19T23:00',tz=BJT).tz_convert('UTC')
        cases.append(dict(key=asset.lower(),asset=asset,instrument=jobs[0]['instrument'],
                          focus_time=focus,fixed_window=True,selection='fixed owner date, no outcome selection'))
    for c in choose_examples(labels,det):
        job=next(j for j in matching['jobs'] if j['instrument']==c['instrument'])
        c.update(asset=job['asset'],focus_time=c['decision_time'],fixed_window=False)
        cases.append(c)
    images=[]
    state_map={j['instrument']:a for j,a in zip(matching['jobs'],matching['state_artifacts'])}
    jobs_by_inst={j['instrument']:j for j in matching['jobs']}
    for case in cases:
        job=jobs_by_inst[case['instrument']]
        feature=checked(job['features_path'],job['features_sha256'])
        inputs[str(feature)]=job['features_sha256']
        frame=pd.read_pickle(feature)
        frame=frame[(frame.index+pd.Timedelta(hours=1))<=END]
        artifact=state_map[case['instrument']]
        state=read(Path(artifact['path']))
        checked(artifact['path'],artifact['sha256'])
        local=signals[signals.instrument.eq(case['instrument'])]
        images.append(render_case(case,frame,state,local,original_signals[original_signals.instrument.eq(case['instrument'])],OUT/'figures'/f'{case["key"]}.png'))
    original_recall=pd.read_csv(ORIGINAL/'recall_summary.csv',float_precision='round_trip')
    original_trade=pd.read_csv(ORIGINAL/'trade_summary.csv',float_precision='round_trip')
    v1=original_recall[original_recall.arm.eq('v1')&original_recall.period.eq('full')].iloc[0]
    v1trade=original_trade[original_trade.arm.eq('v1')&original_trade.period.eq('full')].iloc[0]
    a,b=full.loc['reference'],full.loc['near_box']
    overview=[]
    overview.append(dict(arm='v1',early=v1.signals,children=np.nan,labels=v1.signals,
        drop=np.nan,recall=v1.recall_1,large=v1.large_recall_1,retention=np.nan))
    for arm in ('v3','reference','near_box'):
        r=full.loc[arm];q=retention[retention.arm.eq(arm)&retention.period.eq('full')&retention.stage.eq('confirmed')].iloc[0]
        overview.append(dict(arm=arm,early=r.signals,children=q.signals,labels=r.distinct_labels,
            drop=r.label_drop,recall=r.recall_1,large=r.large_recall_1,retention=r.retention))
    body=['# SPIKE：减少提示之后，真正的启动还留住了吗',
        f'**两种结构改动都没有达到“少提示且保留正确启动”的固定目标。** A将总标签从12,042减到{int(a.distinct_labels):,}（减少{pct(a.label_drop)}），却只保留V3原先及时捕获正例的{pct(a.retention)}。B减到{int(b.distinct_labels):,}（减少{pct(b.label_drop)}），保留率只有{pct(b.retention)}。不能把安静误称为识别更准，也不能宣称已完成“不丢正确启动”。',
        '本报告只比较原版V1、V3及其两个独立干预；A/B没有组合。Pine原生验证状态在文末单列；本报告不会修改图表、通知或实盘。',
        '## 固定目标与直接结果',
        '评分前目标：同根合并总标签减少≥50%；947个≥8%峰值子组的及时召回≥80%；V3原先命中的正例保留≥90%。经济条件另要求扣费后优于匹配随机，两个候选全期置换检验Holm p<0.01。各门独立，不能以旧参考占用填补新预警的漏报。',
        markdown_table(pd.DataFrame(overview),[('arm','版本',arm_name),('early','新预警',integer),('children','确认升级',integer),('labels','同根合并标签',integer),('drop','标签减少',pct),('recall','1660正例及时召回',pct),('large','947大幅行情召回',pct),('retention','V3既有正例保留',pct)]),
        'V1是固定历史对照，不具有本轮父子升级机制；其标签数就是历史箭头数。V3基准、A/B的同根预警+确认只计一个可见标签。原V1的随机对照来自其历史实验，不能视作本轮三组共同随机样本。',
        '## 数据和“正确”的定义',
        '278个历史OKX 1H合约；UTC [2026-07-10,2026-09-09)，61天，BTC/ETH沿用原排除。原始标签文件含预热行，研究窗内恰为8046个锚点：1660正例、6240负例、146未知；正例率20.63%，已知事件中21.01%。947个未来最高涨幅≥8%的正例子组保持不变。它不是全市场样本，也不是主流币/三年验证。',
        '正例沿用首次收盘突破此前12根高点、先因果去重24根，再看后24根收盘是否先到+4冻结ATR而未先到−2ATR。及时新预警必须在事件t或t+1实际出现。标签未要求均线密集，也不是盈利交易标签。V3预警本身与标签共享突破锚点，因此高覆盖部分来自定义相近，不等于预测能力。未知尾部不补成失败。',
        f'A已有信号收盘参考覆盖到{int(a.tracking_positive_events):,}个正事件，**单列为参考覆盖，绝不加进{int(a.hits_1)}个及时新预警分子**。覆盖与新预警可能有交集，不能直接相加；参考也不代表用户实际持仓。',
        '每个A/B固定配置本次均为第1次消耗holdout，边界仍为≥2026-05-04；用户已授权所有日期。这些日期已被研究，不能称盲测或独立样本外。没有训练，训练val样本、val AUC均不适用。',
        '## 两个独立结构干预到底做了什么',
        'A保留V3价格资格和上升沿，只让旧信号收盘参考占用期间/退出当根不再接纳父预警。前根保护决定当前退出，收盘更新保护仅次根生效；拒绝的候选仍更新raw边沿，冷却只由真正接纳预警更新。确认升级仍可在原父0–3根内发生，不复活参考、不创建第二笔交易。',
        'B用完整近零盒替换预警资格，重新生成事件而非过滤旧V3信号：MD/SB同时≤0.1前ATR连续12根，第12根冻结近零带宽；箱体仍更新至最后近零根。离开后冻结整盒，仅release age0–5内收盘突破整盒上沿且站上SMA20/EMA20可报，一盒一次；age6过期。新near序列使旧盒失效，需重新连续12根。ready不足只重置结构，缺口重置全部。父子质量和0–3根确认保留，父边界采用完整盒。',
        '因此A/B的“减少提示”均有真实漏报代价，不能用重建后的更小分母掩盖。被占用挡住不代表信号本身错误；不形成完整近零盒也不代表不会上涨。',
        '## 五张完整走势与实际信号时间',
        '三张固定图显示8月18日至21日完整96根K线（以开盘日计）；另外两张仍为完整96根。每图三层价格分别显示V3、A、B，原版V1的已保存实际信号以灰色星号叠在第一层，图例和标题单列数量；没有灰星即该96根没有原版V1信号，不补画。再显示IMACD双线/零轴与成交量。六均线相同。紫色背景是A的信号收盘参考区间；浅蓝只表示参考时点之后的复盘走势。这些完整图没有作为因果模型输入或检测特征。']
    miss_receipt=None
    if miss_diagnostic is not None:
        directory=Path(miss_diagnostic).resolve()
        if directory!= (EXP/'miss_diagnostic').resolve():
            raise ValueError('Diagnostic must be the registered frozen experiment directory')
        mp=checked(directory/'manifest.json',MISS_DIAGNOSTIC_SHA)
        miss_receipt=json.loads(mp.read_text())
        if miss_receipt.get('status')!='complete' or not miss_receipt['config'].get('fresh_misses_unchanged'):
            raise ValueError('Missing frozen-miss diagnostic contract')
        refs=miss_receipt['source_refs']
        if refs.get(str((OUT/'prepared_manifest.json').resolve()))!=PREPARED_SHA or refs.get(str((OUT/'validation_manifest.json').resolve()))!=VALIDATION_SHA:
            raise ValueError('Diagnostic refers to a different frozen study')
        for source,digest in refs.items():checked(source,digest)
        inputs[str(mp)]=MISS_DIAGNOSTIC_SHA
        for artifact in miss_receipt['artifacts']:
            path=checked(artifact['path'],artifact['sha256']);inputs[str(path)]=artifact['sha256']
        summary=json.loads((directory/'summary.json').read_text())
        if (summary['fresh_misses'],summary['retained_timely_positive_events'],summary['baseline_timely_positive_events'])!=(800,663,1463):
            raise ValueError('Diagnostic changed fresh-miss accounting')
        statuses=pd.read_csv(directory/'status_summary.csv',float_precision='round_trip')
        top=pd.read_csv(directory/'top10_future_high_peaks.csv',float_precision='round_trip')
        status_names={'actual_live':'旧父交易尚在场','unscored_warmup_parent':'预热期父交易未评分/未知','exited_before_event':'旧父交易已在事件前退出','natural_exit_same_bar':'同根自然退出/不算覆盖','no_a_parent':'没有旧父','invalid_parent_trade':'父交易无效','not_entered_yet':'父交易尚未进场','boundary_mark_same_bar':'同根边界估值/不算覆盖'}
        miss_body=['## A漏掉的800次新预警：旧父参考与交易路径仅作描述',
            f'**800次新预警漏报仍全部保留，没有用下列描述修正召回率或保留率。** 在这800个固定漏报事件中，{summary["reference_covered_events"]}个有旧信号收盘参考覆盖；按该A父信号真实次根开盘规则计算的模拟交易，{summary["strict_actual_live_events"]}个在事件时仍在场，{summary["unscored_warmup_parent_events"]}个父信号发生于预热期、交易未评分，{summary["pure_miss_no_live_actual_trade"]}个没有存活旧父交易。最后一项本次为旧父早已退出。这不是用户真实账户下单记录，也不是新增成功率。',
            '严格交易覆盖要求 entry_i≤事件_i<exit_i，同根退出不算覆盖；只连接A自己接纳父信号的交易，不借用V3交易或填补未知。事件时浮动净值按事件收盘/实际入场价−1−固定20bp计算，是未兑现的假想平仓值。一个旧父可能对应多个漏报事件；最终自然退出结果、未来峰值均为事后描述，不能送回检测，也不能直接把719等覆盖数加到663及时新预警上。',
            markdown_table(statuses,[('group','旧父交易状态',lambda x:status_names.get(x,x)),('missed_events','漏报事件',integer),('unique_old_parents','不同旧父',integer),('reference_covered_events','参考覆盖',integer),('actual_live_events','严格交易覆盖',integer),('owner_age_median_bars','父信号年龄中位根数',number),('live_mark_net_bp_median','在场浮动净bp中位',number),('posterior_unique_natural_parents','事后自然退出父数',integer),('posterior_parent_net_win_rate','事后父净胜率',pct)]),
            '### 漏报集合事后最高涨幅10例',
            '按固定未来24根最高high涨幅排序，只为展示漏报类型；不是预先选币、不是额外10笔独立验证。各组父数量在组内去重，跨组不能直接相加。',
            markdown_table(top,[('posterior_peak_rank','事后排序',integer),('asset','合约',None),('event_time','事件收盘北京时间',stamp),('missed_v3_arrow_time','漏掉的V3预警时间',stamp),('future24_high_peak_return','未来24根峰值',pct),('owner_signal_time','A旧父收盘时间',stamp),('owner_age_bars','父年龄根数',integer),('actual_status','父交易状态',lambda x:status_names.get(x,x)),('current_mark_net_bp','事件时未兑现净bp',number),('owner_natural_net_bp','事后自然退出净bp',number)])]
        # Keep this descriptive population separate from headline acceptance metrics.
        insert_at=body.index('## 五张完整走势与实际信号时间')
        body[insert_at:insert_at]=miss_body
    event_spec=[('arm','模式',arm_name),('stage','事件',lambda x:STAGES.get(x,x)),('time','实际收盘北京时间',stamp),('signal_close','当根报价',price),('parent_time','父预警收盘',stamp),('confirm_age','确认等待根数',integer)]
    for image in images:
        kind='固定日期案例' if image['fixed_window'] else '事后选取：A保留' if image['kept_by_a'] else '事后选取：A漏掉'
        body += [f'### {image["asset"]} · {kind}',
                 f'完整96根：开盘北京时间{stamp(image["first_open"])}至{stamp(image["last_open"])}。图中时间轴和信号标注使用每根收盘时间。']
        if not image['fixed_window']:
            body.append(f'固定挑图算法：在原V3已及时命中的正事件中，分别挑A未及时命中/仍命中集合里未来24根最高high相对锚点收盘涨幅最大者，并以instrument/event_i打破并列。本例事后峰值{pct(image["future_peak_return"])}；这只是说明图，不能拿这两张成功走势估计胜率或阈值。')
        body += [f'![{image["asset"]}完整96根](../experiments/active/exp-spike-v3-focus-20260910-v1/results/figures/{image["key"]}.png)',
                 markdown_table(pd.DataFrame(image['signals']),event_spec) if image['signals'] else '完整图内没有实际新信号。']
    body += ['## 各时段：提示负担、保留与漏报',
        '以下保留率以同一时期原V3及时命中的正例为分母。旧参考覆盖分开；阶段为确认时，所有时间采用实际确认根。逐周及案例夜是描述，不能当多个独立验证集。',
        markdown_table(retention,[('arm','模式',arm_name),('stage','阶段',lambda x:STAGES.get(x,x)),('period','时期',None),('signals','实际事件',integer),('distinct_labels','合并标签',integer),('label_drop','标签减少',pct),('hits_1','及时命中',integer),('positive_events','原正例分母',integer),('recall_1','及时召回',pct),('retained_hit_events','保留V3命中',integer),('baseline_hit_events','V3原命中',integer),('retention','保留率',pct),('tracking_positive_events','旧参考覆盖(另列)',integer)]),
        '### 大幅行情与原时点变化（所有时期）',
        markdown_table(retention,[('arm','模式',arm_name),('stage','阶段',lambda x:STAGES.get(x,x)),('period','时期',None),('large_positive_events','大幅行情分母',integer),('large_hits_1','及时命中',integer),('large_recall_1','大幅行情召回',pct),('large_retained_hits','保留旧命中',integer),('large_baseline_hits','V3旧命中',integer),('same_time_kept','同时间保留',integer),('same_time_removed','旧时点删除',integer),('new_times','新增时点',integer),('newly_caught','新捕获原漏例',integer)]),
        '### 匹配误差和未知',
        markdown_table(retention,[('arm','模式',arm_name),('stage','阶段',lambda x:STAGES.get(x,x)),('period','时期',None),('matched','+6内匹配',integer),('unmatched','未匹配',integer),('duplicates','同锚点重复',integer),('unknown','未知',integer),('labels_per_100_asset_days','每100有效合约日标签',number)]),
        '未匹配不是亏损的同义词，可能是24根锚点去重或+6匹配窗以外的上涨。与既有锚点匹配成功也不是可执行净胜率。',
        '## 收益：只评估父预警，不把升级再算一次开仓',
        'A检测占用是信号收盘价参考，下面的收益统一为次根真实开盘成交；两条路径不能混用。初始5根结构低点减0.2ATR与2ATR下限取更宽者；收盘达到2R后启用4ATR跟踪，保护次根有效；无固定止盈。固定20bp入场名义往返成本。下面是重叠的独立事件结果，不是账户收益曲线；账户NAV与最大回撤未计算。',
        markdown_table(pd.concat([pd.DataFrame([v1trade]),trade[trade.period.eq('full')]],ignore_index=True),[
            ('arm','模式',arm_name),('valid','有效事件',integer),('win_rate','全部事件净胜率',pct),('natural_exits','自然退出',integer),('natural_win_rate','自然退出净胜率',pct),('censored','截止截尾',integer),('mean_gross_bp','毛均值bp',number),('mean_net_bp','净均值bp',number),('paired_actual_net_bp','匹配事件净bp',number),('paired_random_net_bp','匹配随机净bp',number),('mean_excess_bp','匹配超额bp',number),('permutation_p','置换p',lambda x:number(x,5)),('holm_p','Holm p',lambda x:number(x,5))]),
        'V3/A/B本轮对照统一为同币×同UTC周×因果ATR百分比桶，最多3个随机入场；统一排除三组当前/过去12根父及确认信号，不排除未来信号、不按未来盈亏筛选。相同decision共用随机索引，不同事件/模式可复用控制，不是独立交易组合。V3实际事件沿用原执行契约；随机匹配重建后其超额与旧报告可能不同。V1为历史匹配对照，未加入本轮共同匹配。',
        '两候选全期Holm校正均未满足经济门槛；不能根据后30天或手选上涨夜较好的均值宣称普遍有效。行情同涨带来的收益必须与随机对照区分。',
        '### 全部时段收益',
        markdown_table(trade,[('arm','模式',arm_name),('period','时期',None),('valid','有效',integer),('matched','有对照',integer),('natural_exits','自然退出',integer),('censored','截尾',integer),('win_rate','全部净胜率',pct),('natural_win_rate','自然退出净胜率',pct),('mean_net_bp','净均值bp',number),('paired_random_net_bp','随机净bp',number),('mean_excess_bp','匹配超额bp',number),('asset_balanced_excess_bp','资产/周平衡超额bp',number),('permutation_p','置换p',lambda x:number(x,5)),('holm_p','Holm p',lambda x:number(x,5))]),
        '## 单特征描述对照：量比与TR扩张',
        'AUC只是事后描述“该特征能否排序净盈利”，不是训练val AUC。top10%按特征值最高选取，不按利润挑选；本轮未用这些结果调阈值。没有训练，因此val AUC和训练val样本不适用；置换检验与匹配随机承担本轮效果对照。',
        markdown_table(scores,[('arm','模式',arm_name),('feature','单特征',None),('n','样本',integer),('descriptive_auc','描述AUC',lambda x:number(x,4)),('top_n','最高10%数量',integer),('top_gross_bp','毛bp',number),('top_net_bp','净bp',number),('top_win_rate','净胜率',pct),('top_matched_actual_bp','匹配事件bp',number),('top_random_bp','随机bp',number),('top_excess_bp','匹配超额bp',number)]),
        '## 原生Pine验证与复现',
        '报告读取已评分Python状态，不重新生成信号。Pine静态校验和原生TradingView编译是不同证据；本报告默认原生编译/保存/可见一致性待单独验收，不能因源码生成就称已替换当前图表。']
    native=None
    if native_qa is not None:
        path=Path(native_qa).resolve()
        if EXP.resolve() not in path.parents: raise ValueError('Native QA must belong to this experiment')
        native=json.loads(path.read_text());inputs[str(path)]=sha(path)
        native_source=checked(ROOT/native['source_path'],native['source_sha256'])
        inputs[str(native_source)]=native['source_sha256']
        if (native.get('saved') is True and native.get('mode_a_rendered') is True
                and native.get('mode_b_rendered') is True
                and 'Add to chart succeeded' in native.get('native_compilation','')):
            body[-1]='Pine已保存到TradingView私有脚本“'+native['private_saved_name']+'”，Add to chart成功，PEPE 1H上的A/B模式及零轴、双线可见。这是原生UI冒烟验证，不是全序列Pine/Python数值一致性验证；文件复制过程可见，未独立取得服务器端源字节哈希。临时图层已移除，旧指标未替换，没有保存布局或创建报警。'
        body.append(f'原生QA回执：{path.name}，SHA256 {sha(path)}。原始结论及适用限制随报告manifest保存；这份原生验证不改变本轮Python评分，也不是生产上线。')
    body += ['```bash',
        '.venv/bin/python -m pytest -q tests/test_spike_burst_v3_reference_gate.py tests/test_spike_burst_v3_structural.py',
        '.venv/bin/python -m yoyo.evaluation.spike_burst_v3_focus_study prepare',
        '.venv/bin/python -m yoyo.evaluation.spike_burst_v3_focus_study evaluate',
        *(['.venv/bin/python -m yoyo.evaluation.spike_burst_v3_focus_misses'] if miss_diagnostic else []),
        '.venv/bin/python -m yoyo.evaluation.spike_burst_v3_focus_report'+(' --native-qa '+str(Path(native_qa).resolve().relative_to(ROOT)) if native_qa else '')+(' --miss-diagnostic '+str(Path(miss_diagnostic).resolve().relative_to(ROOT)) if miss_diagnostic else ''),
        '```',
        '须先提交准确builder与预登记计划。prepare/evaluate拒绝覆盖或重复评分；这组已完成产物应核验SHA而非删除重跑。报告builder也必须先提交，并拒绝覆盖已有报告；依赖的原始认证缓存若缺失，不能用新数据静默替换。',
        '## 风险与诚实声明',
        '- 降低提醒已经实现，但A/B都未达到保留目标，也未建立匹配随机之上的经济优势。本次不是最优参数，不能保证未来不漏掉正确启动。',
        '- 此次历史评分只验证278个OKX合约的多头1H；不推及空头、其他周期、其他交易所或未来市场。最新用户界面和所有图表设置不能由报告推断。',
        '- 同币及同晚行情高度相关；选出的最高涨幅保留/漏报图经过事后选择，不是额外无偏测试。未来区域只用于标签和复盘，没有送回因果特征。',
        '- 保留率、召回率、匹配率、自然退出净胜率分别回答不同问题，不能互换。旧参考覆盖不是实际持仓，信号报价不是成交价。',
        '- 固定20bp没有完整覆盖资金费、滑点与冲击；没有账户仓位模拟，因此NAV、账户收益、最大回撤不适用。未计算的指标不填零。',
        '- 没有训练或promote，不改线上监控、Bark/TG、ACTIVE、订单或其他研究。已见日期每配置首次消费仍不等于独立样本外。',
        '## 下一步',
        '这两项固定假设应按失败保留。接下来按已经冻结的保留率、覆盖率与提示负担目标继续独立研究首发质量或真正趋势分段；不能把旧参考“仍在场”算成新抓到，不能缩分母或反复调整同段历史门槛来追达标。任何线上切换需独立验收，当前成果是研究候选。',
        '## 证据与构建记录',
        f'固定prepared SHA256：{PREPARED_SHA}。固定validation SHA256：{VALIDATION_SHA}。完整输入、代码、图片和输出哈希随results/focus_report_manifest.json保存。',
        '评分完成后曾收到延迟的纯遍历性能优化指令；该优化被撤回到评分时固定源码字节，未修改任何历史manifest或重新评分。']
    text='\n\n'.join(body)+'\n'
    MD.write_text(text)
    from scripts.md_to_html import convert, CSS
    HTML.parent.mkdir(exist_ok=True)
    title='SPIKE：减少提示之后，真正的启动还留住了吗'
    document=(f'<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
        +convert(text,asset_base=MD.parent,embed_images=True)+'</body></html>')
    HTML.write_text(document)
    for path,digest in inputs.items():checked(path,digest)
    authenticate()
    if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=builder.read_bytes():
        raise ValueError('Report builder changed during rendering')
    manifest=dict(status='complete',prepared_sha256=PREPARED_SHA,validation_sha256=VALIDATION_SHA,
        report_builder=str(builder),report_builder_sha256=sha(builder),
        report_code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_pins=prepared['source_pins'],inputs=[dict(path=k,sha256=v) for k,v in sorted(inputs.items())],
        figures=images,outputs=[dict(path=str(p),sha256=sha(p)) for p in (MD,HTML)],
        posthoc_selection='fixed largest future24 peak among V3 timely positives; A missed/kept separately',
        miss_diagnostic=miss_receipt,
        native_qa=native,native_status='native UI smoke receipt attached; not full-series parity' if native else 'pending independent native receipt',
        no_detection_or_rescoring=True,no_online_changes=True)
    put_json(OUT/'focus_report_manifest.json',manifest)
    print(json.dumps(dict(md=str(MD),html=str(HTML),figures=len(images),report_manifest_sha256=sha(OUT/'focus_report_manifest.json')),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-qa',type=Path)
    parser.add_argument('--miss-diagnostic',type=Path)
    args=parser.parse_args()
    run(args.native_qa,args.miss_diagnostic)
