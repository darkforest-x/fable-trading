"""Finalize the existing receipt-verified ETH result without new backtesting."""
from pathlib import Path
import json
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_cost_be2_study import ROOT, EXP, sha, save
from yoyo.evaluation import spike_v9_cost_be2_report as report


def finalize():
    report.run(['eth'])
    results=EXP/'results/eth'
    summary=pd.read_csv(results/'summary.csv');pairs=pd.read_csv(results/'paired_summary.csv')
    common=summary.loc[summary.period.eq('common')].set_index(['group','arm'])
    available=summary.loc[summary.period.eq('available')].set_index(['group','arm'])
    order=[3,5,15,30,60,240]
    old=np.array([common.loc[(f'ETH {m}m','v9_original'),'total_r'] for m in order])
    new=np.array([common.loc[(f'ETH {m}m','v9_cost_be2'),'total_r'] for m in order])
    wide_old=np.array([available.loc[(f'ETH {m}m','v9_original'),'total_r'] for m in order])
    wide_new=np.array([available.loc[(f'ETH {m}m','v9_cost_be2'),'total_r'] for m in order])
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(1,2,figsize=(12,4.2),layout='constrained')
    x=np.arange(6);labels=['3m','5m','15m','30m','1H','4H']
    ax[0].bar(x-.18,old,.36,label='Original V9',color='#64748b')
    ax[0].bar(x+.18,new,.36,label='V9 net-2R cost BE',color='#0f766e')
    ax[0].axhline(0,color='#334155',linewidth=.8);ax[0].set_xticks(x,labels)
    ax[0].set_title('OKX ETH | 2026-01-01 to 2026-05-01 UTC');ax[0].set_ylabel('Net R, closed trades');ax[0].legend(fontsize=8)
    changes=wide_new-wide_old
    ax[1].bar(x,changes,color=['#b91c1c' if v<0 else '#0f766e' for v in changes])
    ax[1].axhline(0,color='#334155',linewidth=.8);ax[1].set_xticks(x,labels)
    ax[1].set_title('Available history | Change in net R');ax[1].set_ylabel('BE version minus original V9')
    ax[1].margins(y=.20)
    ax[1].set_xlabel('Windows differ; OKX 5m begins in Dec 2025.',fontsize=8)
    for i,v in enumerate(changes):ax[1].annotate(f'{v:+.2f}',(i,v),xytext=(0,4 if v>=0 else -12),textcoords='offset points',ha='center',fontsize=9)
    fig.suptitle('A tighter profit-protection rule does not improve every timeframe',fontsize=12)
    plot=results/'exit_comparison.png';fig.savefig(plot,dpi=170);plt.close(fig)
    notes=[]
    notes.append('## 本轮结论\n\n**规则已实现，效果分化，不能称为统一改进。** 以下均为OKX ETH、未使用holdout；共同区间是2026-01-01至2026-05-01 UTC。')
    notes.append(f'- 3m：共同区间净{old[0]:.2f}R→{new[0]:.2f}R；较长可用历史{wide_old[0]:.2f}R→{wide_new[0]:.2f}R。提高净正率没有弥补被截断的趋势收益。')
    notes.append(f'- 5m：共同区间净{old[1]:.2f}R→{new[1]:.2f}R；原生OKX历史仅从2025年12月起，不能据此称跨年稳定。')
    notes.append(f'- 15m：共同区间净{old[2]:.2f}R→{new[2]:.2f}R，结算回撤{common.loc[("ETH 15m","v9_original"),"event_drawdown_r"]:.2f}R→{common.loc[("ETH 15m","v9_cost_be2"),"event_drawdown_r"]:.2f}R；较长历史却{wide_old[2]:.2f}R→{wide_new[2]:.2f}R。近期改善不能覆盖长期反例。')
    notes.append(f'- 30m共同区间无收益变化；1H只改善约{new[4]-old[4]:.4f}R，属取整级变化；4H改善{new[5]-old[5]:.2f}R，但共同区间只有4笔自然平仓，仍净亏。')
    p=pairs.set_index(['group','period']).loc[('ETH 3m','available')]
    notes.append(f'- 固定原3m入场：{int(p.improved_losers)}笔原亏单改善、{int(p.harmed_winners)}笔赢家变差，合计净{p.paired_delta_r:+.2f}R；串行总变化{changes[0]:+.2f}R，差额来自后续交易顺序变化。原实际净10R交易保留{int(p.retained_original_ge10)}/{int(p.original_ge10)}。')
    notes.append('- 所有分组的匹配随机超额经Holm校正后均未达到0.05。结果只用于研究，未挑选“最佳周期”，未扩大仓位或启用倍投。')
    notes.append(f'\n![退出对照]({plot})\n')
    notes.append('## 已执行核验与失败记录\n\n39项不同的定向合成/原引擎回归检查通过（新引擎13、研究守门8、原BE05 10、tier-lock 8）。每个回放窗关闭新规则逐笔对齐原引擎，固定原入场也逐笔对齐；所有已平仓fills的数量、两侧成本、毛净收益与R换算独立核对。12个窗口完成收据包含来源身份。\n\n首次54b2b393db在1H期末持仓分支停止，原因是固定回放缺少终态返回；已修复并新增强制存活到期末测试。e8f490afd1完成回放后，独立静态复核提出输入续跑、报告代码/授权、归档parity和holdout次数守门问题；515e50ea6b加固并从头完成最终回放，交易参数未变。旧尝试目录和日志保留，最终表只使用515e50ea6b结果。')
    md=ROOT/'analysis/p1_spike_v9_cost_be2_20260915.md'
    body=md.read_text();first,rest=body.split('\n',1)
    body=first+'\n\n'+'\n\n'.join(notes)+'\n'+rest
    body+='\n最终结论和图表复现：\n\n```bash\nPYTHONPATH="$PWD/.venv/lib/python3.9/site-packages:$PWD" /usr/bin/python3 experiments/active/exp-spike-v9-cost-be2-20260915-v1/finalize.py\n```\n'
    md.write_text(body)
    subprocess.run(['/usr/bin/python3','scripts/md_to_html.py',str(md),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    rm=EXP/'report_manifest.json';manifest=json.loads(rm.read_text())
    manifest.update(report_sha256=sha(md),html_sha256=sha(ROOT/'analysis/html/p1_spike_v9_cost_be2_20260915.html'),
        finalizer=str(Path(__file__).relative_to(ROOT)),finalizer_sha256=sha(Path(__file__)),figure_sha256=sha(plot))
    save(rm,manifest)
    final_paths=[p for p in results.rglob('*') if p.is_file()]
    final_paths += [EXP/'config.json',EXP/'PROJECT_PLAN.md',EXP/'finalize.py',EXP/'report_manifest.json',
        EXP/'eth_run.log',EXP/'eth_failed_54b2b393db.log',EXP/'eth_before_guard_hardening_e8f490afd1.log',
        EXP/'results/eth_failed_54b2b393db/failure.json',EXP/'results/eth_before_guard_hardening_e8f490afd1/superseded.json',
        ROOT/'analysis/p1_spike_v9_cost_be2_20260915.md',ROOT/'analysis/html/p1_spike_v9_cost_be2_20260915.html']
    save(EXP/'delivery_manifest.json',dict(status='eth_pre_only_complete',holdout_consumed=False,holdout_consumption_number=0,
        universe_status='awaiting_configuration_specific_owner_approval',source_commit='515e50ea6b',
        finalizer_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        files=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p),size_bytes=p.stat().st_size) for p in sorted(final_paths)],
        preserved_attempts=['results/eth_failed_54b2b393db','results/eth_before_guard_hardening_e8f490afd1'],
        training_eligible=False,production_eligible=False))


if __name__=='__main__':finalize()
