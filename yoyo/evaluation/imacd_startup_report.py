"""Render saved startup-quality ledgers without new price/outcome evaluation.

Source: the frozen experiment manifest and CSV ledgers from
imacd_startup_research. Tables are mechanically derived from saved results;
the report exposes adverse results and no-go decisions. No live API calls,
signal mutations or notifications occur. Charts are standard matplotlib
exports and markdown is immediately converted using the repository tool.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .imacd_startup_research import ROOT, EXP, DATA, POLICIES, PERIODS, FOLDS, digest, aggregate
from .imacd_startup_quality import build_features

NAMES = {'P00': '原始箭头', 'P01': '仅均线收拢', 'P02': '收拢＋价格靠近',
         'P03': '再加定向分离', 'P04': '旧密集规则'}
FOLD_NAMES = {'development': '2023–2024 开发', 'validation': '2025 时间外验证',
              'audit_pre': '2026-01-01 至 05-04 复核',
              'holdout_review': '2026-05-04 至 07-12 最终复核'}
REPORT = ROOT/'analysis/p1_imacd_startup_quality_20260908.md'


def fmt(v, digits=2):
    return '—' if pd.isna(v) else f'{float(v):,.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']
                     + ['| '+' | '.join(str(x) for x in row)+' |' for row in rows])


def decisions(summary):
    """P02 was nominated before data access; no switching after holdout."""
    rows = []
    for minutes in PERIODS:
        reasons = []
        for fold in ('validation', 'holdout_review'):
            q = summary.loc[(summary.minutes==minutes)&(summary.fold==fold)].set_index('policy')
            base, candidate = q.loc['P00'], q.loc['P02']
            tests = dict(
                portfolio_improves=candidate.portfolio_net_pct > base.portfolio_net_pct,
                drawdown_not_worse=candidate.portfolio_mdd_pct <= base.portfolio_mdd_pct,
                matched_excess_improves=candidate.excess_bp > base.excess_bp,
                right_tail_retained=candidate.tail_profit_pct >= 80,
            )
            if fold == 'validation':
                tests['increment_significant_after_holm'] = candidate.incremental_p_holm_validation < .01
            reasons.extend(f'{fold}:{name}' for name, good in tests.items() if not bool(good))
        rows.append(dict(minutes=minutes, policy='P02', arithmetic_gates_pass=not reasons,
                         failed_checks=reasons, deployment_approved=False))
    return rows


def render_equity(equity, out):
    plt.rcParams.update({'font.family':['Arial Unicode MS', 'DejaVu Sans'], 'axes.unicode_minus':False})
    colors = {'P00':'#8d9bb4','P01':'#b59ae7','P02':'#28bfa5','P03':'#d9ac67','P04':'#ef858c'}
    for fold in ('validation', 'holdout_review'):
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.3), facecolor='#111820')
        for ax, minutes in zip(axes, PERIODS):
            ax.set_facecolor('#111820')
            for p in POLICIES:
                q = equity.loc[(equity.fold==fold)&(equity.minutes==minutes)&(equity.policy==p)]
                ax.plot(pd.to_datetime(q.time,utc=True), (q.equity-1)*100,
                        color=colors[p], lw=2.2 if p in ('P00','P02') else 1,
                        alpha=1 if p in ('P00','P02') else .6, label=f'{p} {NAMES[p]}')
            ax.axhline(0,color='#586270',lw=.7)
            ax.set_title({15:'15 分钟',60:'1 小时',240:'4 小时'}[minutes],color='#ecf1f7')
            ax.tick_params(colors='#a8b3c4',labelsize=8)
            ax.grid(alpha=.12)
            ax.spines[['top','right']].set_visible(False)
            ax.set_ylabel('等初始资金组合净收益 %',color='#a8b3c4',fontsize=9)
            ax.tick_params(axis='x',rotation=25)
        axes[0].legend(fontsize=8,facecolor='#111820',labelcolor='#d7e0ed',edgecolor='#354151')
        fig.suptitle(f'SPIKE · {FOLD_NAMES[fold]} · 收盘盯市，成本 20bp',color='#ecf1f7',fontsize=14)
        fig.tight_layout()
        fig.savefig(out/f'equity_{fold}.png',dpi=150,facecolor=fig.get_facecolor())
        plt.close(fig)


def render_case(bars, f, i, title, target):
    """Plot recorded event context; future section is explicitly segregated."""
    start = max(0, i-100)
    end = min(len(bars), i+37)
    b, g = bars.iloc[start:end], f.iloc[start:end]
    x = np.arange(len(b))
    split = i-start
    fig, axes = plt.subplots(3,1,figsize=(13,8),sharex=True,
                              gridspec_kw={'height_ratios':[3.3,1.3,1]},facecolor='#111820')
    for ax in axes:
        ax.set_facecolor('#111820')
        ax.tick_params(colors='#a8b3c4',labelsize=8)
        ax.grid(alpha=.12)
        ax.spines[['top','right']].set_visible(False)
        ax.axvline(split+.5,color='#dce3ec',lw=1,ls='--')
        ax.axvspan(split+.5,len(b),color='#8d9bb4',alpha=.07)
    for j,r in enumerate(b.itertuples()):
        color = '#31c9af' if r.close>=r.open else '#ed7f8a'
        axes[0].vlines(j,r.low,r.high,color=color,lw=.8)
        axes[0].add_patch(Rectangle((j-.32,min(r.open,r.close)),.64,
                                    max(abs(r.close-r.open),r.close*1e-6),color=color,lw=0))
    mas = ['sma20','ema20','sma60','ema60','sma120','ema120']
    colors = ['#4edac5','#77b9ad','#5078bd','#86a0c7','#8c8e94','#bcc2cd']
    for col,color in zip(mas,colors):
        axes[0].plot(x,g[col],color=color,lw=.9,label=col)
    axes[0].scatter([split],[b.close.iloc[split]],marker='D',s=48,color='#f1c477',zorder=5)
    axes[0].annotate(f'信号收盘 {b.close.iloc[split]:.9g}',(split,b.close.iloc[split]),
                     xytext=(-90,35),textcoords='offset points',color='#f1c477',fontsize=9,
                     arrowprops=dict(arrowstyle='-',color='#f1c477'))
    axes[0].legend(ncol=6,fontsize=7,facecolor='#111820',labelcolor='#cdd6e4',loc='upper left')
    axes[1].plot(x,g.md,color='#709bfa',lw=1.5,label='IMACD')
    axes[1].plot(x,g.sb,color='#e7aa54',lw=1.4,label='Signal')
    axes[1].axhline(0,color='#c5cad3',lw=.8)
    width = g.rope_high-g.rope_low
    axes[2].plot(x,width,color='#c4a6ef',lw=1.3)
    axes[2].set_ylabel('六MA原始带宽',color='#a8b3c4',fontsize=8)
    origin = int(f.focus_start_i.iloc[i])-start
    for ax in axes:
        ax.axvspan(max(0,origin),split-.5,color='#e7aa54',alpha=.06)
    ticks = np.linspace(0,len(b)-1,7,dtype=int)
    axes[2].set_xticks(ticks,[b.index[j].tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M') for j in ticks])
    axes[2].set_xlabel('北京时间 · 虚线左侧：截至信号收盘可见；右侧：后续，仅用于复盘',color='#a8b3c4',fontsize=9)
    fig.suptitle(title,color='#e8eef7',fontsize=13)
    fig.tight_layout()
    fig.savefig(target,dpi=150,facecolor=fig.get_facecolor())
    plt.close(fig)


def verify_ledgers(events, summary, equity):
    """Independent ledger invariants, no replay and no new outcomes."""
    checks = {}
    checks['unique_event_identity'] = not events.event_id.duplicated().any()
    checks['all_qualified_runs'] = bool(events.near_zero_bars.ge(12).all())
    checks['entry_after_signal_index'] = bool(events.entry_i.eq(events.signal_i+1).all())
    checks['entry_after_confirmation_clock'] = bool(pd.to_datetime(events.entry_open_time,utc=True).eq(
        pd.to_datetime(events.signal_decision_close_time,utc=True)).all())
    checks['fixed_20bp_cost'] = bool(np.allclose(events.gross_bp-events.net_bp,20))
    checks['p01_contraction'] = bool(events.P01.eq(events.contraction_ratio.le(1)).all())
    checks['p02_single_added_gate'] = bool(events.P02.eq(events.P01 & events.proximity_atr.le(1)).all())
    checks['p03_single_added_gate'] = bool(events.P03.eq(events.P02 & events.separation_delta.gt(0)).all())
    bounds = {fold:(pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC')) for fold,start,end in FOLDS}
    checks['no_cross_fold_outcome'] = all(
        pd.to_datetime(g.signal_open_time,utc=True).ge(bounds[fold][0]).all()
        and pd.to_datetime(g.exit_time,utc=True).le(bounds[fold][1]).all()
        for fold,g in events.groupby('fold'))
    controls = pd.read_csv(DATA/'controls.csv.gz')
    checks['controls_not_reused'] = not controls.duplicated(['symbol','minutes','fold','control_i']).any()
    release_keys = set(zip(events.symbol,events.minutes,events.signal_open_time))
    checks['controls_exclude_all_releases'] = not any(
        x in release_keys for x in zip(controls.symbol,controls.minutes,controls.signal_open_time))
    means = controls.groupby('event_id').net_bp.agg(['mean','count']).reindex(events.event_id)
    expected = means['mean'].where(means['count'].eq(3)).to_numpy()
    checks['matched_means_exact'] = bool(np.allclose(events.control_mean_net_bp,expected,equal_nan=True))
    checks['matched_excess_exact'] = bool(np.allclose(events.excess_bp,events.net_bp-expected,equal_nan=True))
    checks['summary_event_counts'] = all(
        r.n == int(events.loc[(events.fold==r.fold)&(events.minutes==r.minutes),r.policy].sum())
        for r in summary.itertuples())
    checks['portfolio_final_nav_matches_table'] = all(
        np.isclose((g.iloc[-1].equity-1)*100,
                   summary.loc[(summary.fold==fold)&(summary.minutes==minutes)&(summary.policy==policy),'portfolio_net_pct'].iloc[0])
        for (fold,minutes,policy),g in equity.groupby(['fold','minutes','policy']))
    checks['full_mdd_not_below_daily_mdd'] = all(
        summary.loc[(summary.fold==fold)&(summary.minutes==minutes)&(summary.policy==policy),'portfolio_mdd_pct'].iloc[0]+1e-9
        >= (1-np.r_[1.,g.equity.to_numpy()]/np.maximum.accumulate(np.r_[1.,g.equity.to_numpy()])).max()*100
        for (fold,minutes,policy),g in equity.groupby(['fold','minutes','policy']))
    audit = dict(checks=checks, passed=all(checks.values()), event_count=len(events), control_count=len(controls),
                 no_new_price_evaluation=True)
    if not audit['passed']:
        raise ValueError(f'Ledger verification failed: {audit}')
    return audit


def run():
    result = EXP/'results'
    manifest = json.loads((result/'manifest.json').read_text())
    for record in manifest['files']:
        if digest(ROOT/record['path']) != record['sha256']:
            raise ValueError(f"Ledger changed: {record['path']}")
    summary = pd.read_csv(result/'summary.csv')
    ranks = pd.read_csv(result/'rankings.csv')
    coverage = pd.read_csv(result/'coverage.csv')
    tails = pd.read_csv(result/'right_tail.csv')
    portfolios = pd.read_csv(result/'portfolios.csv')
    events = pd.read_csv(DATA/'events.csv.gz')
    equity = pd.read_csv(result/'equity_daily.csv')
    audit = verify_ledgers(events, summary, equity)
    (result/'ledger_verification.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    outcome = decisions(summary)
    (result/'decision.json').write_text(json.dumps(dict(candidate='P02',decisions=outcome,
        live_changes=False, threshold_search=False),ensure_ascii=False,indent=2)+'\n')
    render_equity(equity, result)
    passed = [str(r['minutes']) for r in outcome if r['arithmetic_gates_pass']]
    lead = ('预先指定的P02没有周期通过全部上线条件。当前Pine、spike监控和通知条件保持原样。'
            if not passed else 'P02在以下周期通过数值门：'+', '.join(passed)+'分钟。仍需核对贡献集中度与案例，不自动部署。')
    text = [
        '# SPIKE · IMACD 启动质量实证\n',
        f'**{lead}**\n',
        '本轮检验“同一蓄势段的均线收拢＋价格围绕均线整理”能否改进当前可见箭头。信号变少不等于过滤有效；同时检查亏损减少、大赢家保留、等资金净收益和最大回撤。\n',
        f'54个既有OKX USDT永续数据集；{len(events):,}个可评估原始箭头；15m／1H／4H。时间范围2023-01-01至2026-07-12，分4段独立记账。所有参数在源码提交 `{manifest["builder_commit"]}` 后一次运行。\n',
        '## 先看最终复核\n',
        '下表为2026-05-04至2026-07-12，期末不跨段持仓。净收益和回撤来自同一个54袖套组合；其余指标来自原始事件账本，不把重叠箭头的收益相加当成组合收益。\n',
    ]
    q = summary.loc[(summary.fold=='holdout_review') & summary.policy.isin(['P00','P02'])]
    text.append(table(['周期','规则','箭头数','每笔净bp','组合净收益','收盘最大回撤','大赢家正收益保留'],
                     [[f'{int(r.minutes)}m',NAMES[r.policy],int(r.n),fmt(r.mean_net_bp),fmt(r.portfolio_net_pct)+'%',fmt(r.portfolio_mdd_pct)+'%',fmt(r.tail_profit_pct)+'%'] for r in q.itertuples()]))
    text += ['\n![最终复核净值](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/equity_holdout_review.png)\n',
             '“大赢家”是同周期同时间段基线真实净收益最高10%，数量取ceil、并列按event_id；保留率是其中原有正收益的保留份额，是事后诊断，不是选股规则。\n',
             '## 为什么不把通知数量下降直接当成成功\n']
    rows = []
    for r in summary.loc[(summary.fold=='holdout_review')&summary.policy.eq('P02')].itertuples():
        rows.append([f'{int(r.minutes)}m',fmt(100-r.retained_pct)+'%',fmt(r.loss_removed_pct)+'%',
                     fmt(r.winner_retained_pct)+'%',fmt(r.tail_profit_pct)+'%'])
    text.append(table(['周期','全部信号减少','亏损箭头减少','盈利箭头保留','大赢家正收益保留'],rows))
    text += ['\n亏损箭头指在本轮固定入退规则与20bp成本下净收益≤0，并非Owner逐图确认的“形态噪音”。过滤收益变化还受单仓阻挡、资金复利与机会缺失影响。\n',
             '## 固定政策与判定结果\n',
             table(['政策','唯一变化'],[
                 ['P00 原始箭头','当前可见focusRelease，34/9、连续近零至少12根、冻结0.10×ATR[1]容差'],
                 ['P01 仅收拢','本近零段末6根六MA带宽中位数≤首6根中位数'],
                 ['P02 主候选','P01＋前12根价格到六MA带的外侧距离中位数≤释放前ATR'],
                 ['P03 分离诊断','P02＋20组相对60/120组在最近3根向信号方向分离'],
                 ['P04 旧密集对照','基线＋前12根带宽/ATR均值≤3且均线交织≥2次']]),
             '\nP02在开跑前已指定为唯一候选。P01/P03/P04即使某一段更好，也不从最终复核中改选成上线方案。\n']
    labels = {'portfolio_improves':'组合净收益未提升','drawdown_not_worse':'回撤变大',
              'matched_excess_improves':'匹配超额未提升','right_tail_retained':'大赢家保留不足80%',
              'increment_significant_after_holm':'相对基线增益未通过校正显著性'}
    for r in outcome:
        failures = ['{}：{}'.format(FOLD_NAMES[x.split(':')[0]],labels[x.split(':')[1]]) for x in r['failed_checks']]
        text.append(f'\n- **{r["minutes"]}m**：'+('；'.join(failures) if failures else '数值门通过，尚未部署')+'。')
    text += ['\n## 所有时间段和对照，包含负面结果\n']
    for fold in FOLD_NAMES:
        q = summary.loc[summary.fold.eq(fold)]
        text += [f'\n### {FOLD_NAMES[fold]}\n',
                 table(['周期','政策','n','净bp/笔','随机净bp','匹配超额bp','alpha p','增益p校正','组合净%','回撤%','右尾保留%'],
                       [[int(r.minutes),r.policy,int(r.n),fmt(r.mean_net_bp),fmt(r.control_net_bp),fmt(r.excess_bp),
                         fmt(r.p,4),fmt(r.incremental_p_holm_validation,4),fmt(r.portfolio_net_pct),fmt(r.portfolio_mdd_pct),fmt(r.tail_profit_pct)] for r in q.itertuples()])]
    text += ['\n![2025验证净值](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/equity_validation.png)\n',
             '## 独立信息与统计边界\n',
             '匹配随机入场固定同品种×周期×月份×因果波动五分位×md方向，排除原始箭头且不复用对照时点。三名对照不足时不强配，计算匹配超额时只用完整配对。各政策复用同一映射，因此随机alpha与过滤本身的增益是两回事。\n',
             '增益检验对统一初始资金组合的月净值变化做候选−P00，再进行月块符号置换，2025验证的4候选×3周期共12项做Holm校正。币种同月共用市场块；这依赖月份符号可交换假设，不是随机干预实验。收盘为月初00:00的最后一根归入之前的月份，避免凭空增加统计块。\n',
             '最终复核只有约3个月块；即使每月差额同向，精确符号置换p最小也只有1/8=0.125。大量币种不能把3个月变成大量独立市场状态。历史也已用于其他研究，时间外复核不等于从未被看过的盲测。\n',
             table(['周期','政策','验证匹配覆盖','复核匹配覆盖','验证增益p','验证增益p校正'],
                   [[m,p,fmt(summary.loc[(summary.fold=='validation')&(summary.minutes==m)&(summary.policy==p),'matched_pct'].iloc[0])+'%',
                     fmt(summary.loc[(summary.fold=='holdout_review')&(summary.minutes==m)&(summary.policy==p),'matched_pct'].iloc[0])+'%',
                     fmt(summary.loc[(summary.fold=='validation')&(summary.minutes==m)&(summary.policy==p),'incremental_p'].iloc[0],4),
                     fmt(summary.loc[(summary.fold=='validation')&(summary.minutes==m)&(summary.policy==p),'incremental_p_holm_validation'].iloc[0],4)] for m in PERIODS for p in POLICIES]),
             '\n## 单特征基线：事前特征的事后排序诊断\n',
             '下表只使用原始箭头，展示2025验证段。AUC标签是该笔扣成本后是否盈利；分数在信号收盘可知，但最高10%的数量和分位是在整段事件收齐后确定。这是离线诊断，不是可在线直接执行的top10%门槛。没有训练模型，没有因为AUC高就宣告成功。\n']
    q = ranks.loc[(ranks.fold=='validation')&ranks.policy.eq('P00')]
    text.append(table(['周期','事前分数','AUC','最高10%毛bp','最高10%净bp','胜率%','匹配超额bp'],
                      [[int(r.minutes),r.score,fmt(r.auc,3),fmt(r.top_gross_bp),fmt(r.top_net_bp),fmt(r.top_win_pct),fmt(r.top_excess_bp)] for r in q.itertuples()]))
    text.append('\n### 多空分别看（补充描述，不作新选优）\n')
    side_rows = []
    for (minutes,side),g in events.loc[events.fold.eq('holdout_review')].groupby(['minutes','side']):
        kept = g.loc[g.P02]
        side_rows.append([minutes,'多' if side==1 else '空',len(g),len(kept),fmt(g.net_bp.mean()),fmt(kept.net_bp.mean())])
    text.append(table(['周期','方向','原箭头数','保留数','原净bp/笔','P02净bp/笔'],side_rows))
    text.append('\n四张参考图都是上涨案例；多空结果不得用一个合并均值互相代替，也不根据这个补充分组改成只做某方向。\n')
    text += ['\n## 被删掉的大赢家\n',
             '以下按最终复核原有净收益排序，仅作失败解释。不能把它们的后续结果再用于本轮调阈值。\n']
    q = tails.loc[(tails.fold=='holdout_review') & tails.policy.eq('P02') & ~tails.kept].sort_values('net_bp',ascending=False).head(12)
    text.append(table(['品种','周期','原净收益bp','近零根数','末/首宽度','距带/ATR','定向分离'],
                     [[r.symbol,int(r.minutes),fmt(r.net_bp),int(r.near_zero_bars),fmt(r.contraction_ratio,3),fmt(r.proximity_atr,3),fmt(r.separation_delta,3)] for r in q.itertuples()]))
    text += ['\n## 被保留的亏损箭头\n']
    q = events.loc[events.fold.eq('holdout_review')&events.P02&events.net_bp.lt(0)].sort_values('net_bp').head(12)
    text.append(table(['品种','周期','信号确认UTC','净收益bp','近零根数','末/首宽度','距带/ATR'],
                     [[r.symbol,int(r.minutes),r.signal_close_time,fmt(r.net_bp),int(r.near_zero_bars),fmt(r.contraction_ratio,3),fmt(r.proximity_atr,3)] for r in q.itertuples()]))
    text += ['\n## 四张截图案例\n']
    cases_manifest = result/'cases/manifest.json'
    if cases_manifest.exists():
        text.append('案例使用独立的OKX原生周期行情复核，全部结果保存于cases/manifest.json。截图已暴露，不混入上面的统计样本，也不按截图价格筛掉不一致事件。\n')
        cases = json.loads(cases_manifest.read_text())
        case_rows = []
        for case in cases['cases']:
            if not case.get('releases'):
                case_rows.append([case['symbol'],case['timeframe'],case['status'],'—','—','—','—'])
                continue
            for release in case['releases']:
                good = release['keep_contraction'] and release['keep_proximity']
                case_rows.append([case['symbol'],case['timeframe'],release['bar_close_utc'],
                                  str(release['close']),release['near_zero_bars'],
                                  fmt(release['reference_price_delta'],8),'保留' if good else '过滤'])
            path = ROOT/case['source_path']
            if digest(path) != case['source_sha256']:
                raise ValueError('case source changed')
            b = pd.read_csv(path)
            b.index = pd.DatetimeIndex(pd.to_datetime(b.ts,unit='ms',utc=True))
            f = build_features(b)
            for release in case['releases']:
                i = b.index.get_loc(pd.Timestamp(release['bar_open_utc']))
                if f.release_side.iloc[i] == 0:
                    raise ValueError('saved case release not reproduced')
                name = f'case_{case["symbol"]}_{case["timeframe"]}_{i}.png'
                render_case(b,f,i,f'{case["symbol"]} · {case["timeframe"]} · 原箭头与截图价格/根数对照',result/name)
                text.append(f'\n![{case["symbol"]}案例](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/{name})\n')
        text.append(table(['品种','周期','原生数据确认UTC','收盘价','近零根数','与截图价格差','P02'],case_rows))
        text.append('\n价格与根数一致是案例匹配证据；截图没有逐根导出的确切时间，本记录仍不冒称完成TradingView数据窗口逐字段验收。有限1200根递推种子也可能影响临界信号。\n')
        text.append('完整逐案例记录：[案例JSON](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/cases/manifest.json)。\n')
    else:
        text.append('截图案例尚未取得完整复核记录，不能声称四例已被新条件保留。当前54品种主池截至7月12日，不能覆盖截图的8–9月时点。\n')
    text.append('\n## 首尾收拢条件误删的实际结构\n')
    examples = events.loc[events.minutes.eq(240)&events.fold.eq('validation')&~events.P02].sort_values(['net_bp','event_id'],ascending=[False,True]).head(2)
    for event in examples.itertuples():
        source = next(s for s in manifest['sources'] if s['symbol']==event.symbol)
        path = ROOT/source['path']
        if digest(path) != source['sha256']:
            raise ValueError('example source changed')
        b = pd.read_csv(path)
        b.index = pd.DatetimeIndex(pd.to_datetime(b.ts,unit='ms',utc=True))
        b = aggregate(b[['open','high','low','close','volume']],240)
        f = build_features(b)
        i = int(event.signal_i)
        if b.index[i] != pd.Timestamp(event.signal_open_time) or f.release_side.iloc[i] != event.side:
            raise ValueError('example identity mismatch')
        name = f'rejected_{event.event_id}.png'
        render_case(b,f,i,f'被P02过滤：{event.symbol} 4H · 后续净收益{event.net_bp/100:.2f}%（固定规则、事后）',result/name)
        start_i = int(event.focus_start_i)
        widths = f.rope_high-f.rope_low
        pre = widths.iloc[max(0,start_i-12):start_i].median()
        early = f.formation_early_width.iloc[i]
        late = f.formation_late_width.iloc[i]
        text.append(f'\n**{event.symbol} 4H**：末/首均线宽度比{event.contraction_ratio:.3f}，近零{event.near_zero_bars}根，因此首尾收拢门拒绝。近零段开始前12根带宽中位数{pre:.8g}，段首6根{early:.8g}，释放前6根{late:.8g}。这是已保存失败案例的解释量，不是本轮新增筛选条件。\n')
        text.append(f'![被过滤的{event.symbol}](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/{name})\n')
    insolvent = portfolios.loc[portfolios.insolvent.eq(True)]
    text.append('\n这里需要区分“均线此前已经收拢”和“在IMACD近零段内还必须继续收拢”。首尾比较只检验后者。PENGU图显示进入近零段前带宽已下降，随后维持较窄却略有波动，机械首尾门仍会拒绝它。本轮结果否定的是该机械定义足以改进通知，不是否定Owner的视觉形态。\n')
    text += ['\n## 数据统计与风险、诚实声明\n',
             f'- 54个固定历史品种，{len(events):,}个原始箭头，{len(events.loc[events.fold.eq("validation")]):,}个2025验证箭头；净正类率{events.net_bp.gt(0).mean()*100:.2f}%。各币上市/数据起点不同，覆盖表完整记录。',
             f'- 可评估范围缺失或预热不足的品种×周期×时间段：{int(coverage.status.ne("ok").sum())}。缺数据时对应初始资本留在现金，不用后来的行情回填。',
             f'- 出现开盘/收盘/费用后权益非正的单币政策袖套：{len(insolvent)}。一旦出现即锁存并拒绝续开仓；未捏造爆仓成交。',
             '- 当前规则下的盈利不等于实盘可实现收益：20bp是固定成本代理，未读取资金费、盘口、滑点、真实成交、保证金或盘中爆仓。',
             '- 组合MDD为逐K收盘盯市；单币明细包含开盘与费用跳点。图仅显示每日最后净值，不能从图形反推完整回撤。',
             '- 每个时间段独立从相同资金开始，边界持仓按末根收盘计价；报告包含强制记账笔数。没有止损保护，3R绘图未参与交易规则。',
             '- 大赢家保留是事件级诊断，已过滤后的真实单仓可用资金与可成交事件可能变化；不把两类指标混为收益贡献归因。',
             '- 既有54品种可能包含数据可得性/幸存者偏差，不能外推为OKX全部473个合约的结果。多空同时评估，四张截图仅确认了多头视觉例子。',
             '- 这是本配置P00/P01/P02/P03/P04各第1次消耗holdout。依据Owner“任何时间段数据都可以使用不要有任何限制”和本轮“你去做吧”；没有使用holdout调参或换候选。',
             '\n## 复现与下一步\n',
             '先核对manifest内源文件SHA与冻结源码。以下构建命令在没有既有输出的工作目录执行；程序拒绝覆盖本轮结果。报告重渲染只读取保存账本，不重新评估行情。\n',
             '```bash\n.venv/bin/python -m pytest -q tests/test_imacd_startup_quality.py tests/test_imacd_startup_accounting.py tests/test_imacd_startup_research.py\n.venv/bin/python -m yoyo.evaluation.imacd_startup_research\n.venv/bin/python -m yoyo.evaluation.imacd_startup_cases\n.venv/bin/python -m yoyo.evaluation.imacd_startup_report\n```\n',
             '下一步只在新的研究版本中提出更接近Owner视觉过程的定义，并先用开发段或新的前向样本验证。未经新证据，不把本轮某个好看的子结果接入通知。Pine外观、当前箭头、spike通知及TG/Bark均未更改。\n',
             '[实验计划](../experiments/active/exp-imacd-startup-quality-20260908-v1/PROJECT_PLAN.md) · [完整对照CSV](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/summary.csv) · [单特征CSV](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/rankings.csv) · [数据与源码清单](../experiments/active/exp-imacd-startup-quality-20260908-v1/results/manifest.json)\n',
    ]
    REPORT.write_text('\n'.join(text))
    subprocess.run(['python3',str(ROOT/'scripts/md_to_html.py'),str(REPORT),'--out-dir',str(ROOT/'analysis/html')],check=True,cwd=ROOT)
    rendered = ROOT/'analysis/html'/REPORT.with_suffix('.html').name
    # The canonical HTML lives one directory below the source Markdown.
    # Embedded images need no relocation; ordinary links need the extra ../.
    rendered.write_text(rendered.read_text().replace('href="../experiments/', 'href="../../experiments/'))
    print(str(ROOT/'analysis/html'/REPORT.with_suffix('.html').name))


if __name__ == '__main__':
    run()
