"""Build the Chinese, source-backed altcoin study report from frozen outputs.

No evaluation, parameter fitting, network or live mutation occurs here. Figures
and tables are descriptive derivatives of stored research outcomes; source
hashes and the post-selection nature of diagnostics are recorded. Main cost
claims remain the fixed 20bp research convention, not live executable PnL.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-imacd-altcoin-trends-20260909-v1'
LABELS={'base':'34/9基准 · 主线失向退出','exit_chandelier':'3ATR移动保护','exit_fixed3r':'固定3R','exit_sma60':'SMA60退出',
 'signal5':'信号线5','signal13':'信号线13','ma21':'MA21','ma55':'MA55','focus6':'蓄势6根','focus24':'蓄势24根','focus36':'蓄势36根',
 'band005':'近零带0.05ATR','band020':'近零带0.20ATR','band030':'近零带0.30ATR',
 'gate_box_break':'收盘突破原箱体','gate_rvol2':'相对量能≥2','gate_tr15':'振幅扩张≥1.5','gate_close70':'方向收盘位置≥70%','gate_bb20':'BB压缩前20%',
 'deriv_oi24_pos':'OI24H增加','deriv_oi4_pos':'OI4H增加','deriv_oi24_down':'OI24H下降诊断','deriv_taker55':'主动方向占比≥55%',
 'deriv_oi24_available':'OI24H同覆盖基准','deriv_oi4_available':'OI4H同覆盖基准','deriv_taker_available':'主动量同覆盖基准'}
FOLD_LABEL={'development':'2023–24开发','validation':'2025选择','audit_pre':'2026年初–5/4','audit_seen':'5/4–7/12已观察','recent_test':'7/12–9/9近期时间测试','external_recent':'7/12–8/14外部迁移'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_phase(folder, selection=None):
    """Reject stale selection/summary attachments before producing prose."""
    folder=Path(folder)
    completion=json.loads((folder/'completion.json').read_text())
    if completion['summary_sha256']!=digest(folder/'summary.csv'):
        raise ValueError('Completed summary changed: '+str(folder))
    run=json.loads((folder/'run_manifest.json').read_text())
    if selection is not None and run['identity']['selection_sha256']!=digest(selection):
        raise ValueError('Audit used another selection lock: '+str(folder))
    return run


def table(frame,columns):
    if frame.empty:return '没有符合本表口径的样本。'
    f=frame.loc[:,list(columns)].rename(columns=columns).copy()
    for c in f:
        if pd.api.types.is_numeric_dtype(f[c]):
            f[c]=f[c].map(lambda v:'—' if pd.isna(v) else f'{v:.3f}' if ('p' in c or '超额' in c) else f'{v:.2f}')
        else:f[c]=f[c].fillna('—').astype(str)
    return f.to_markdown(index=False)


def friendly(frame):
    f=frame.copy()
    if 'arm' in f:f['方案']=f.arm.map(LABELS).fillna(f.arm)
    if 'fold' in f:f['时期']=f.fold.map(FOLD_LABEL).fillna(f.fold)
    if 'minutes' in f:f['周期']=f.minutes.map({15:'15m',60:'1H',240:'4H'})
    return f


def case_exit_rows(events, examples):
    """Compare unchanged entries at fixed baseline risk; flag boundary marks."""
    selected=[]
    for example in examples:
        if not any('本组净R最高' in role for role in example['roles']):continue
        g=events.loc[events.symbol.eq(example['symbol']) & events.minutes.eq(example['minutes']) &
                     events.fold.eq(example['fold']) & events.signal_i.eq(example['signal_i']) &
                     events.side.eq(example['side']) & events.cohort.eq('owner_illustration') &
                     events.arm.isin(['base','exit_fixed3r','exit_chandelier','exit_sma60'])].copy()
        if len(g) and (g.entry_price.max()-g.entry_price.min()>1e-10*g.entry_price.max() or
                       g.initial_risk.max()-g.initial_risk.min()>1e-10*g.initial_risk.max()):
            raise ValueError('Exit comparisons do not share the same entry and initial risk')
        if len(g):
            g['状态']=np.where(g.censored,'边界盯市 · 尚非自然退出','规则已退出')
            g['启动UTC']=g.decision_close_time
            selected.append(g)
    return friendly(pd.concat(selected,ignore_index=True)) if selected else pd.DataFrame()


def side_rows(events):
    """Descriptive selected-event means, explicitly not a side portfolio."""
    f=events.loc[events.cohort.eq('high_vol') & events.arm.eq('base') & events.portfolio_selected.eq(True)]
    rows=[]
    for (fold,minutes,side),g in f.groupby(['fold','minutes','side']):
        rows.append(dict(fold=fold,minutes=minutes,方向='多头' if side==1 else '空头',
                         n=len(g),mean_net_bp=g.net_bp.mean(),win_pct=g.net_bp.gt(0).mean()*100,
                         censored_n=int(g.censored.sum()),median_mfe_r=g.mfe_r.median()))
    return friendly(pd.DataFrame(rows))


def volatility_retention(events):
    """Outcome-labelled diagnostic of the predeclared prior-week top quartile.

    Excludes BTC/ETH and Owner examples. The 5R cutoff is a descriptive tail
    label only; it never selects a parameter or changes live membership.
    """
    identity=['symbol','minutes','fold','signal_i','side']
    f=events.loc[events.arm.eq('base')&~events.symbol.isin(['BTC','ETH','SOPH','USELESS'])]
    rows=[]
    for (fold,minutes),g in f.groupby(['fold','minutes']):
        all_rows=g.loc[g.cohort.eq('all_core')].set_index(identity)
        kept=g.loc[g.cohort.eq('high_vol')].set_index(identity)
        if all_rows.index.has_duplicates or kept.index.has_duplicates or not kept.index.isin(all_rows.index).all():
            raise ValueError('Volatility cohort is not a unique subset of the altcoin candidates')
        if len(kept) and not np.allclose(all_rows.loc[kept.index,'net_r'],kept.net_r,rtol=1e-10,atol=1e-10):
            raise ValueError('Same candidate has different outcomes across cohorts')
        tail=all_rows.net_r.ge(5)
        rows.append(dict(fold=fold,minutes=minutes,n_all=len(all_rows),n_kept=len(kept),
                         tail_n=int(tail.sum()),tail_kept=int(kept.net_r.ge(5).sum()),
                         tail_censored=int((tail & all_rows.censored.eq(True)).sum()),
                         mean_all_bp=all_rows.net_bp.mean(),mean_kept_bp=kept.net_bp.mean(),
                         largest_omitted_r=all_rows.loc[~all_rows.index.isin(kept.index),'net_r'].max()))
    return friendly(pd.DataFrame(rows))


def major_symbol_rows(results,selections):
    """Keep BTC and ETH separate; transfer altcoin nominations without refitting."""
    rows=[]
    for phase,fold in [('development','validation'),('audit','recent_test')]:
        for choice in selections:
            minutes=int(choice['minutes'])
            folder=results/(phase+'15' if minutes==15 else phase)
            for symbol in ('BTC','ETH'):
                source=folder/f'{symbol}_{minutes}'
                events=pd.read_csv(source/'events.csv.gz')
                curves=pd.read_pickle(source/'curves.pkl.gz',compression='gzip')
                for arm in dict.fromkeys(['base',choice['selected_arm']]):
                    if arm is None:continue
                    key=f'{fold}|majors|{arm}'
                    equity=curves[key].dropna()
                    if equity.empty:raise ValueError('Missing major-symbol equity')
                    g=events.loc[events.fold.eq(fold)&events.cohort.eq('majors')&events.arm.eq(arm)]
                    rows.append(dict(symbol=symbol,fold=fold,minutes=minutes,arm=arm,n=len(g),
                                     selected_n=int(g.portfolio_selected.sum()),mean_net_bp=g.net_bp.mean(),
                                     portfolio_net_pct=(equity.iloc[-1]-1)*100,
                                     mdd_pct=(equity/equity.cummax().clip(lower=1)-1).min()*100))
    return friendly(pd.DataFrame(rows))


def adverse_summary(results,summary):
    """Aggregate stored adverse envelopes, never call them a realized path."""
    rows=[]
    for phase,fold in [('development','validation'),('audit','recent_test')]:
        close=pd.read_pickle(results/phase/'portfolio_curves.pkl.gz',compression='gzip')
        accum={}
        for path in sorted((results/phase).glob('*/curves.pkl.gz')):
            minutes=int(path.parent.name.rsplit('_',1)[1])
            frame=pd.read_pickle(path,compression='gzip')
            for arm in ('base','exit_fixed3r','exit_chandelier','exit_sma60'):
                key=f'{fold}|high_vol|{arm}|adverse'
                fullkey=f'{fold}|{minutes}|high_vol|{arm}'
                if key not in frame or fullkey not in close:continue
                calendar=close[fullkey].dropna().index
                contribution=frame[key].dropna().reindex(calendar).ffill().fillna(1)-1
                accum[fullkey]=accum.get(fullkey,pd.Series(0.,index=calendar))+contribution/54
        for key,value in accum.items():
            _,minutes,_,arm=key.split('|');equity=close[key].dropna()
            if (value+1>equity+1e-9).any():raise ValueError('Adverse envelope exceeds close equity')
            row=summary.loc[summary.fold.eq(fold)&summary.minutes.eq(int(minutes))&summary.cohort.eq('high_vol')&summary.arm.eq(arm)].iloc[0]
            rows.append(dict(fold=fold,minutes=int(minutes),arm=arm,mdd_pct=row.mdd_pct,
                             stress_mdd_pct=((value+1)/equity.cummax().clip(lower=1)-1).min()*100))
    return friendly(pd.DataFrame(rows))


def curves_plot(out,phase,name,lock,periods=(60,240)):
    paths={15:out/('development15' if phase=='development' else 'audit15'),60:out/phase,240:out/phase}
    fig,axes=plt.subplots(1,len(periods),figsize=(14,4.5),squeeze=False)
    palette={'base':'#64748b','exit_fixed3r':'#d3942c','exit_chandelier':'#109c89','exit_sma60':'#9569c7','signal5':'#3a75cd','gate_box_break':'#3a75cd'}
    for ax,m in zip(axes[0],periods):
        p=paths[m]/'portfolio_curves.pkl.gz'
        if not p.exists():ax.text(.5,.5,'No completed output',ha='center');continue
        f=pd.read_pickle(p,compression='gzip')
        fold='validation' if phase=='development' else 'recent_test'
        choice=next((x['selected_arm'] for x in lock['selections'] if x['minutes']==m),None)
        arms=list(dict.fromkeys(['base','exit_fixed3r','exit_chandelier',choice]))
        for arm in arms:
            if arm is None:continue
            key=f'{fold}|{m}|high_vol|{arm}'
            if key not in f:continue
            s=f[key].dropna();ax.plot(s.index,(s-1)*100,label=arm,color=palette.get(arm,'#3a75cd'),lw=1.5)
        ax.axhline(0,color='#a1aab5',lw=.7);ax.grid(alpha=.18);ax.set_title(f'{m//60}H · {fold} · 54 cash sleeves')
        ax.set_ylabel('Static-cost portfolio return (%)');ax.legend(frameon=False,fontsize=8);ax.tick_params(axis='x',rotation=20)
    fig.suptitle('Fixed entry sizing; initial risk 2 ATR; 20 bp round trip; funding excluded',fontsize=10,color='#566273')
    fig.tight_layout();fig.savefig(name,dpi=160);plt.close(fig)


def generate(args):
    results=Path(args.results);audit=results/'audit';dev=results/'development'
    for p in (audit/'completion.json',dev/'completion.json',results/'development15/completion.json',results/'audit15/completion.json'):
        if not p.exists():raise ValueError('Required completed phase missing: '+str(p))
    lock=json.loads((dev/'selection_lock.json').read_text())
    if lock['summary_sha256']!=digest(dev/'summary.csv'):raise ValueError('Changed selection evidence')
    audit_run=verify_phase(audit,dev/'selection_lock.json');verify_phase(dev)
    frames=[];source_hashes={str(dev/'selection_lock.json'):digest(dev/'selection_lock.json')}
    selections=list(lock['selections'])
    lock15=results/'development15/selection_lock.json'
    if lock15.exists() and (lock15.parent/'completion.json').exists():
        extra=json.loads(lock15.read_text())
        if extra['summary_sha256']!=digest(lock15.parent/'summary.csv'):raise ValueError('Changed 15m selection evidence')
        selections+=extra['selections'];source_hashes[str(lock15)]=digest(lock15)
    for phase in ('development','development15','audit','audit15'):
        p=results/phase/'summary.csv'
        if p.exists() and (p.parent/'completion.json').exists():
            select=results/('development15' if phase=='audit15' else 'development')/'selection_lock.json' if phase.startswith('audit') else None
            verify_phase(p.parent,select)
            frames.append(pd.read_csv(p));source_hashes[str(p)]=digest(p)
            for name in ('completion.json','run_manifest.json','events_all.csv.gz','portfolio_curves.pkl.gz','rank_diagnostics.csv'):
                source_hashes[str(p.parent/name)]=digest(p.parent/name)
    summary=pd.concat(frames,ignore_index=True)
    if summary.duplicated(['fold','minutes','cohort','arm']).any():raise ValueError('Duplicate summary identity')
    e=pd.read_csv(audit/'events_all.csv.gz')
    plots=EXP/'report_assets';plots.mkdir(exist_ok=True)
    curves_plot(results,'development',plots/'validation_curves.png',lock)
    curves_plot(results,'audit',plots/'recent_curves.png',lock)
    summary=friendly(summary)
    columns={'时期':'时期','周期':'周期','方案':'方案','n':'事件','matched_n':'匹配事件','mean_net_bp':'全事件均净bp','control_net_bp':'随机对照bp','excess_bp':'匹配超额bp','portfolio_net_pct':'组合净%','mdd_pct':'收盘回撤%','win_pct':'胜率%'}
    primary=summary.loc[summary.cohort=='high_vol']
    focus_arms={int(x['minutes']):set(x['allowed_audit_arms']) for x in selections}
    def focus_mask(frame):
        return frame.apply(lambda r:r.arm in focus_arms.get(int(r.minutes),set()),axis=1)
    nominated={x['minutes']:x['selected_arm'] for x in selections}
    nominees='，'.join(f'{m//60}H提名{LABELS.get(a,a) if a else "无通过候选"}' for m,a in nominated.items() if m in (60,240))
    recent=primary.loc[(primary.fold=='recent_test')&~primary.arm.str.startswith('deriv_')]
    best_recent=recent.loc[recent.apply(lambda x:x.arm==nominated.get(x.minutes),axis=1)]
    findings=EXP/'FINDINGS.md'
    if not findings.exists():raise ValueError('Finish the source-backed interpretation before report generation')
    source_hashes[str(findings)]=digest(findings)
    lines=['# Spike｜高波动山寨启动与趋势持有研究',
           '2026-09-09 · 原54币研究池＋SOPH/USELESS展示组 · 1H/4H主检验、15m迁移/成本诊断。',
           '**当前表格的“净”只扣统一20bp交易成本，未计完整资金费、盘口冲击和清算。以下是历史研究，不是已验证的实盘收益。**',
           findings.read_text(),
           '## 样本与覆盖',
           table(primary.loc[primary.arm.eq('base')],{'时期':'时期','周期':'周期','n':'有效基准事件','symbols':'有事件币数','matched_n':'已匹配事件','selected_n':'单仓选中','censored_n':'边界盯市','win_pct':'正净收益占比%'}),
           '开发2023–2024，选择2025，2026年初–5/4、5/4–7/12、7/12–9/9分段回放；每段结束处强制盯市并显式标记。K线周期来自完整15m聚合，事件与对照共同550根预热。',
           '## 先看锁定候选在近期的结果',
           '候选仅用2023–2024开发和2025选择期提名，然后固定后检查2026。'+nominees+'；其他固定退出作为预先约定基准。提名不等于统计验收。2026日期在此前研究中已被观察，因此称时间测试，不能冒称全新盲测。',
           table(best_recent,columns),
           '同一币只有一仓，54个等资本袖套，不活跃资金留现金；高波动名单每周固定。各臂使用相同资本分配规则，实际持仓利用率随退出路径不同；不能把该收益乘54冒充高集中仓位实绩。组合净收益与“每笔平均收益”不是同一个量。',
           '表中“全事件均净”包括重叠候选；组合只使用单仓选中事件。随机对照和超额只使用匹配子集，所以全事件均净减对照不一定等于超额。匹配案例均净可以由对照+超额还原。',
           '![近期固定候选与退出基准](../experiments/active/exp-imacd-altcoin-trends-20260909-v1/report_assets/recent_curves.png)',
           '## 参数到底从哪里来',
           '| 参数 | 来源/含义 | 当前证据边界 |\n|---|---|---|\n'
           '| MA34 / Signal9 | 用户原始LazyBear代码 | 继承值，不是针对ETH/BTC/山寨选出的最优值 |\n'
           '| 蓄势12根 / 近零0.10ATR | 后续显示默认，资格成立后冻结近零带 | 不是历史收益优化结果；本轮首次按预定候选比较 |\n'
           '| SMA/EMA20、60、120六线 | 用户已有均线密集方法 | 可表达结构，不能据线密集直接推断胜率 |\n'
           '| SMA20回踩/保护 | 对应用户原先关注的深色线 | 是结构参考，没有证明对所有币最优 |\n'
           '| 高周期许可、密集阈值 | 旧系统背景/普通启动逻辑 | 当前重点focus释放标签并非都由这些输入过滤 |\n'
           '| 3R、颜色、延伸长度、保留框数 | 风险展示与样式 | 不等于回测实际止盈，也不影响全部信号 |',
           '0.10ATR是相对本币波动的尺度，已经比固定ETH/BTC点数更容易迁移；但相同12根在15m=3小时、1H=12小时、4H=48小时，含义差别很大。需要检验跨年、跨币和邻近参数，而不是每币挑一个历史冠军。Mac监控参数不会自动跟随TradingView设置同步。',
           '本轮复现的是Pine可见focus释放候选，未把YOLO二次确认加进每个离线事件。当前前端/TG仍用既有IMACD→YOLO协议；这些表格不能直接冒充当前通知组合的绩效。',
           '## 旧年一因素参数比较',
           '基准34/9/12/0.10，每次仅改一项：MA21/55，signal5/13，蓄势6/24/36，近零带0.05/0.20/0.30。共同禁止550根前入场，但不重写指标自己的状态。表中2025参与选择，属于样本内选择证据。',
           table(primary.loc[primary.fold.eq('validation')&primary.family.isin(['baseline','parameter'])],columns),
           '## 是否真的需要把3R放开',
           '所有退出使用同一下一根开盘入场、2×信号ATR初始风险。对比3R、中性md退出、3ATR移动保护、SMA60退出。移动保护只在下一根生效；先执行已有止损，再依据已收盘K线更新。不能先拿当根最高价抬保护线、再利用同根最低价假设成交。',
           table(primary.loc[primary.arm.isin(['base','exit_fixed3r','exit_chandelier','exit_sma60'])],columns),
           '![2025不同退出资金曲线](../experiments/active/exp-imacd-altcoin-trends-20260909-v1/report_assets/validation_curves.png)',
           '低胜率并不自动否定趋势策略，但需同时看损益均值、对照和最大回撤。MFE只是持有路径中的有利运动，不等于可以兑现的收益；盘中退出K线未知先后的极值没有被加入已证实MFE。',
           '## 启动质量与真正的增量信息',
           table(primary.loc[primary.arm.isin(['base','gate_rvol2','gate_tr15','gate_close70','gate_box_break','gate_bb20'])],columns),
           '每一项单独与默认信号比较，未把获胜的条件临时叠起来。尤其需要区分“删掉噪音”与“删掉未来的大赢家”。均线/箱体是定义形态的工具，只有同币同时段随机对照才能判断是否超出市场本身的上涨/下跌。',
           '## 持仓量和主动成交量',
           '使用OKX逐合约4H历史，BTC/ETH与SOPH/USELESS均为各自合约。OI以币本位变化为主，USD OI另列；涨价本身就能推高USD值。OI增加不是净做多，OI下降也不能直接叫做空头回补。',
           '历史bucket没有confirm和可证明的首次可见时钟：本轮按桶开始+8小时才可用，超过8小时陈旧则缺失。1H/4H共用该保守4H背景；并未拿今天的快照补过去。每个门与该特征同覆盖的base对比，controls也必须在其当时可用。',
           table(primary.loc[primary.fold.eq('recent_test')&primary.arm.str.startswith('deriv_')],columns),
           '以上为短历史增量诊断。OI24H下降行包含双方向，只能称下降组；是否空头回补需要同时看价格/成交方向。未验证清算热图、盘口深度或社交热度的历史因果优势。',
           '**对照口径：**各可用域有独立固定随机种子，所以同样候选也可能得到不同对照均值。不能把available相对全域base的超额变化叫做信息增益；过滤臂应对应同域available。p检验相对随机入场的超额，不是加过滤相对不加过滤的增量显著性。',
           '## 多头与空头分别看',
           '下表仅为原组合实际选中事件的描述均值，未重新生成多空资金曲线。高波动不代表只能做多；价格上涨且OI下降也不等于已确认空头回补。',
           table(side_rows(e),{'时期':'时期','周期':'周期','方向':'方向','n':'选中事件','mean_net_bp':'每笔净bp','win_pct':'胜率%','censored_n':'边界盯市数'}),
           '## BTC / ETH 与山寨不能混成一个结论',
           '逐币列出默认值与山寨旧年提名候选，未在BTC/ETH上重新选参。此处净收益、回撤是该币单独一个资本袖套，不是54币组合；不能与主高波动组合绝对收益直接比较。',
           table(major_symbol_rows(results,selections),{'symbol':'币种','时期':'时期','周期':'周期','方案':'方案','n':'事件','selected_n':'单仓选中','mean_net_bp':'事件均净bp','portfolio_net_pct':'单币净%','mdd_pct':'收盘回撤%'}),
           '主高波动组每周一按此前完整7日ATR/close均值选出可用山寨前25%，BTC/ETH单列，SOPH/USELESS不参与排名或选参。全部名单依然来自便利/近期存续池，不能宣称历史全市场无偏。',
           '### 过去已经很波动，是否反而漏掉刚启动的币',
           '这里只比较原研究池中52个山寨的同一基准事件，BTC/ETH与用户展示币剔除。每周前25%名单在交易前已固定；事后净R≥5只用于描述大赢家保留率，不用于选参。它与“这根K线波动正在放大”是两个不同问题。≥5R统计包含边界盯市，不能全部当作自然退出已兑现利润；近期4H四笔中有三笔属于这一情况。',
           table(volatility_retention(e),{'时期':'时期','周期':'周期','n_all':'全池事件','n_kept':'高波动保留','tail_n':'事后≥5R事件','tail_censored':'其中边界盯市','tail_kept':'其中保留','mean_all_bp':'全池事件均净bp','mean_kept_bp':'保留均净bp','largest_omitted_r':'遗漏最大净R'}),
           '## SOPH / USELESS：成功、失败都看',
           table(summary.loc[(summary.cohort=='owner_illustration')&focus_mask(summary)&summary.fold.eq('recent_test')],columns),
           '这两币是用户事后提供的案例组，不能用它们反推参数再称样本外。全景图按事后收益确定性选择最好、最差和中位案例，供解释形态；不是成功率抽样。前文100根，之后至少目标72根并尽量延伸到实际退出；遇500根上限会显式标记退出是否在图外。',
          ]
    if args.gallery:
        gallery=Path(args.gallery)
        lines.append(f'[打开全景案例图库]({gallery.resolve()})')
        manifest_path=gallery.parent/'manifest.json'
        if manifest_path.exists():
            source_hashes[str(manifest_path)]=digest(manifest_path)
            gallery_meta=json.loads(manifest_path.read_text())
            if gallery_meta['input_sha256']['events']!=digest(audit/'events_all.csv.gz') or gallery_meta['input_sha256']['selection']!=digest(dev/'selection_lock.json'):
                raise ValueError('Gallery comes from another event ledger or selection')
            if gallery_meta['input_sha256']['history_manifest']!=audit_run['identity']['history_sha256']:
                raise ValueError('Gallery uses another history manifest')
            examples=gallery_meta['examples']
            for ex in examples:
                if digest(gallery.parent/ex['path'])!=ex['png_sha256']:raise ValueError('Gallery image changed')
            case_table=case_exit_rows(e,examples)
            lines += ['### 同一启动、不同退出的对照',
                      '只选上面事后案例，同一入场与同一2ATR初始风险。逐事件反事实未必被每种退出组合实际选中；边界盯市收益必须和规则实际退出分开看。',
                      table(case_table,{'symbol':'币种','周期':'周期','启动UTC':'启动UTC','方案':'退出','net_r':'净R','mfe_r':'MFE R','状态':'状态'})]
            for symbol in ('USELESS','SOPH'):
                ex=next((x for x in examples if x['symbol']==symbol and any('本组净R最高' in r for r in x['roles'])),None)
                if ex:lines.append(f'![{symbol}事后选例，含决策当时看不到的后续行情]({(gallery.parent/ex["path"]).resolve()})')
    lines += ['## 大赢家依赖与统计强度',
              table(primary.loc[focus_mask(primary)&primary.fold.isin(['validation','recent_test'])],
                    {'时期':'时期','周期':'周期','方案':'方案','n':'事件','mean_net_ex_top3_bp':'去掉前三赢家净bp','top3_positive_profit_pct':'前三占正利润%','median_net_r':'中位净R','median_mfe_r':'中位MFE R','p_holm_primary':'主家族p'}),
              '置换与bootstrap以日历月份为块；同月币种和重叠事件不是独立样本。主1H/4H家族固定36个比较，缺数据不缩小分母；近期只有少量月份，p的分辨率有限。较高收益或漂亮曲线不能替代对照和独立前向验证。']
    ranks=friendly(pd.concat([pd.read_csv(results/phase/'rank_diagnostics.csv') for phase in ('development','audit')],ignore_index=True))
    ranks['指标']=ranks.score.map({'relative_volume':'相对成交量','tr_expansion':'振幅扩张','near_zero_bars':'蓄势根数'})
    lines += ['### 预定单特征的排序诊断',
              '基准高波动组，以单特征从高到低排序，标签为统一20bp后收益大于零。Top10%在各表内事后描述，不是可直接实时应用的阈值；AUC并非生产验收标准。',
              table(ranks.loc[ranks.cohort.eq('high_vol')&ranks.arm.eq('base')&ranks.fold.isin(['validation','recent_test'])],
                    {'时期':'时期','周期':'周期','指标':'指标','n':'有效事件','auc':'AUC','top_n':'Top10%事件','top_gross_bp':'毛bp','top_net_bp':'净bp','top_win_pct':'胜率%','top_excess_bp':'匹配超额bp'})]
    if args.external:
        path=Path(args.external)/'summary.csv'
        if path.exists():
            external_run=verify_phase(path.parent)
            if external_run['identity']['selection_sha256']!=digest(dev/'selection_lock.json'):raise ValueError('External study uses another selection')
            x=friendly(pd.read_csv(path));source_hashes[str(path)]=digest(path)
            lines += ['## 原54币之外的跨币迁移诊断',
                      '使用此前冻结的加密品种身份清单与已有本地文件交集，剔除原54及SOPH/USELESS；参数直接继承旧年锁定结果，没有在外部池重新挑选。仍有近期存续与数据覆盖偏差，近期右端早于主研究，不混称同一段测试。',
                      table(x.loc[(x.cohort=='high_vol')&focus_mask(x)],{k:v for k,v in columns.items() if k in x.columns})]
    if args.costs:
        path=Path(args.costs)/'summary.csv'
        if path.exists():
            cost_meta=json.loads((path.parent/'manifest.json').read_text())
            if cost_meta['output_sha256']['summary.csv']!=digest(path):raise ValueError('Cost summary changed')
            cost_inputs={str(Path(p).resolve()):h for p,h in cost_meta['input_sha256'].items()}
            for event_path in audit.glob('*/events.csv.gz'):
                if cost_inputs.get(str(event_path.resolve()))!=digest(event_path):raise ValueError('Costs use another event ledger')
            cost=friendly(pd.read_csv(path));source_hashes[str(path)]=digest(path)
            cost=cost.loc[cost.cohort.eq('high_vol')&cost.fold.eq('recent_test')&cost.scope.eq('portfolio_selected')&focus_mask(cost)]
            lines += ['## 费用、资金费与可执行性',
                      '40/60bp与按成交名义计费是锁定事件路径的额外敏感性诊断，不是重新选参。资金费区间仅依据观察到的实际结算表、用成交开盘代理mark，并显式处理一分钟评估窗口与盘中退出；不表示完整资金费后复利回放。',
                      f'[完整成本敏感性表]({path.resolve()})',
                      table(cost,{'周期':'周期','方案':'方案','case_count':'事件','case_mean_net_20bp':'20bp后均净bp','case_mean_net_40bp':'40bp后均净bp','case_mean_net_60bp':'60bp后均净bp','case_mean_net_notional_fee_bp':'按成交名义均净bp'}),
                      table(cost,{'周期':'周期','方案':'方案','funding_common_case_count':'资金费共同覆盖事件','funding_common_case_mean_low':'含观测资金费下界bp','funding_common_case_mean_high':'上界bp','funding_common_control_mean_low':'对照下界bp','funding_common_control_mean_high':'对照上界bp'}),
                      '资金费表仅用案例与其全部原有效对照共同有资料的子集；不能与上一表全部事件均值直接相减作为资金费影响。首尾覆盖不保证结算表无遗漏，因此区间只针对已观察结算，不是总费用的完整界。']
    lines += ['## 回撤口径：收盘与极值压力',
              table(adverse_summary(results,summary),{'时期':'时期','周期':'周期','方案':'方案','mdd_pct':'收盘回撤%','stress_mdd_pct':'极值压力%'}),
              '主表回撤来自收盘盯市。压力列为相对收盘历史峰值的不利极值跌幅：把同根各币不利极值同时施加，通常不会真实同步；未计盘中更高峰值，因此不代表真实路径，也不保证覆盖盘中峰到谷最大回撤。']
    lines += ['## 风险与诚实声明',
              '- **预注册偏差：实时1倍上限未实现。**实际为入场前权益100%名义、持有固定数量、不再平衡；实时名义/权益会变化，亏损空头会超1倍。本文只对应此模型。',
              '- 收益为统一20bp静态成本后；没有完整资金费、普通滑点、价差、市场冲击或清算模拟。小币种更不能直接按图中点位假设可成交。',
              '- 同根止损/止盈先后不明时取止损；止损跳空按更差open。盘中退出的时间是所在K线收盘上界，并已标intrabar_unknown。',
              '- 旧CSV没有保留confirm字段。近期确认来自fetcher行为及收盘截断，并非完整逐请求确认档案。BTC/FIL两处volume冲突以独立重取核验，原值与裁决均保留；没有改canonical文件。',
              '- 2025是参数选择期；2026日期在此前研究中已经见过。SOPH/USELESS是事后展示币，案例图含未来，禁止作为训练输入。',
              '- 本报告使用周度名单按确认收盘切换的修正版。旧实现周日末根晚切一根，原结果保留但不作主结论；1H/4H正式holdout为本配置第二次收益访问，15m单列首次。参数、阈值、费用与退出没有依据旧审计结果改动。',
              '- AUC只用于预定连续分数的正净收益排序诊断，不是交易成功标准。其top-decile毛/净收益与匹配超额另附完整表；未进行YOLO或LGB新训练。',
              '- 没有修改Pine、Mac当前信号通知、ACTIVE、实盘执行或凭据。本轮结论需要按证据判断能否进入独立观察；不以美观图片或低胜率/高R叙事代替验证。',
              '## 下一步如何使用',
              '先依据锁定候选的2026表现、跨币迁移和成本压力决定是否进入独立前向观察；未通过的项留作否定证据。若改实时仓位上限、用更细执行数据、接入实盘或调整当前通知协议，需要单独明确合同。没有自动把研究冠军切到线上。',
              '## 复现与原始证据',
              f'[预注册计划]({(EXP/"PROJECT_PLAN.md").resolve()}) · [合同偏差说明]({(EXP/"CONTRACT_CLARIFICATIONS.md").resolve()}) · [外部资料]({(EXP/"EXTERNAL_RESEARCH.md").resolve()}) · [holdout访问记录]({(EXP/"exposure_ledger.json").resolve()})',
              f'[开发/选择完整表]({(dev/"summary.csv").resolve()}) · [2026完整表]({(audit/"summary.csv").resolve()}) · [排序/AUC/top-decile诊断]({(audit/"rank_diagnostics.csv").resolve()})',
              '```bash\n'+(EXP/'REPRODUCE.sh').read_text()+'\n```' if (EXP/'REPRODUCE.sh').exists() else '完整复现命令见各阶段run_manifest；构建器必须先提交。',
              '公开指标监控思路参照[CoinAnk官方工具说明](https://www.coinank.com/zh/tool)、[Coinalyze提醒](https://coinalyze.net/alerts/)；数据语义来自[OKX官方API](https://www.okx.com/docs-v5/en/)。这些来源提供定义，不构成收益证据。']
    native=EXP/'NATIVE_1H_FEASIBILITY.md'
    if native.exists():
        source_hashes[str(native)]=digest(native)
        lines += ['## 已保留的更及时数据入口',
                  '除本轮4H背景外，已检查原生1H OI/taker接口，并另行冻结滚动保留的近期历史。它与4H聚合不保证逐字节等价，历史首次发布时间仍未知；1H数据采集不是收益验收，也没有被悄悄替换进上述4H背景结果。',
                  f'[原生1H接口、首尾探针和聚合差异记录]({native.resolve()})']
    target=Path(args.report)
    target.write_text('\n\n'.join(lines)+'\n')
    subprocess.run(['python3',str(ROOT/'scripts/md_to_html.py'),str(target),'--out-dir',str(ROOT/'analysis/html')],check=True,cwd=ROOT)
    receipt=dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),inputs=source_hashes,
                 report_sha256=digest(target),production_eligible=False,training_eligible=False)
    (EXP/'report_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',default=str(EXP/'results_clock_v2'))
    p.add_argument('--report',default=str(ROOT/'analysis/p1_imacd_altcoin_trends_20260909.md'))
    p.add_argument('--gallery');p.add_argument('--external');p.add_argument('--costs')
    args=p.parse_args()
    relative=str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(['git','show','HEAD:'+relative],cwd=ROOT)!=Path(__file__).read_bytes():
        p.error('Commit the report builder before generating the report')
    generate(args)


if __name__=='__main__':main()
