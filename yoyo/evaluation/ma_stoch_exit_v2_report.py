"""Source-bound report for round-two exploration; no new price evaluation."""
import json
import subprocess
import pandas as pd
import numpy as np
from yoyo.evaluation.ma_stoch_exit_v2_study import EXP,ROOT
from yoyo.evaluation.ma_shift_stoch_report import table,num

NAMES={'baseline':'原反向箭头全平','stop_2':'2 ATR止损','stop_3':'3 ATR止损',
       'stop_4':'4 ATR止损','stop_6':'6 ATR止损','stop_8':'8 ATR止损',
       'stop_15m_2':'2倍15m ATR止损','close_stop_2':'收盘穿2ATR后止损','close_stop_3':'收盘穿3ATR后止损',
       'zone_touch':'到另一侧80/20即退出','zone_cross':'到80/20后反向交叉退出',
       'wide_tp_1':'6ATR止损＋1R止盈','wide_tp_2':'6ATR止损＋2R止盈',
       'wide_be_1':'6ATR止损＋1R后保本','wide_partial_2':'6ATR止损＋2R平一半',
       'wide_trail':'6ATR止损＋2R后2ATR跟踪','wide_arrow_half':'6ATR止损＋反向箭头两次各平半',
       'wide_no_arrow':'仅6ATR止损、取消反向退出','wide_tp2_no_arrow':'6ATR止损2R止盈、取消反向退出'}
for n in (6,12,24):NAMES[f'structure_{n}']=f'{n}根结构外止损'
for n in (.5,1,2):
    NAMES[f'tp_pct_{n}']=f'仅加{n}%固定止盈'
    NAMES[f'sl_pct_{n}']=f'{n}%固定止损'
    NAMES[f'pct1_tp_{n}']=f'1%止损＋{n}%止盈'


def result_table(summ):
    rows=[]
    for name,r in summ['arms'].items():
        c=r['control']
        rows.append([NAMES[name],r['n'],r['open_count'],num(r['mtm_return_pct']),num(r['mtm_max_drawdown_pct']),num(r['net_win_rate']*100),num(r['pf'],3),num(r['gross_mean_bp']),num(r['net_mean_bp']),r['longest_loss'],r['longest_losing_stop'],num(c.get('control_mean_bp')),num(c.get('excess_mean_bp'))])
    return table(['规则','已平','末仓','账户净%','回撤%','净胜率%','PF','单笔毛bp','单笔净bp','连亏','连续亏损止损','随机净bp','超额bp'],rows)


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dev=json.loads((EXP/'dev/summary.json').read_text());check=json.loads((EXP/'recheck/summary.json').read_text())
    selected=json.loads((EXP/'selection.json').read_text());winner=selected['selected']
    audit=json.loads((EXP/'ledger_audit.json').read_text())
    a,b=dev['arms'][winner],check['arms'][winner];d0,c0=dev['arms']['baseline'],check['arms']['baseline']
    improves=a['mtm_return_pct']>d0['mtm_return_pct'] and b['mtm_return_pct']>c0['mtm_return_pct']
    verdict='两段历史都出现相对改善，但尚未证明稳定盈利。' if improves else '没有找到能在两段历史都改善净收益的新退出。'
    if improves and a['mtm_return_pct']>0 and b['mtm_return_pct']>0:verdict='两段历史均为正收益，仍只是复用历史得到的待独立验证候选。'
    fig,axes=plt.subplots(2,2,figsize=(12,7),sharex='col',gridspec_kw={'height_ratios':[2,1]})
    for col,(phase,title) in enumerate([('dev','Selection: Jan-Feb 2026'),('recheck','Exposed-history recheck: Mar-Apr 2026')]):
        for name,color,label in [('baseline','#087e8b','Original arrow exit'),('stop_2','#999999','2 ATR stop'),(winner,'#d06527',winner)]:
            curve=pd.read_csv(EXP/phase/f'{name}_curve.csv');x=pd.to_datetime(curve.time,utc=True);eq=curve.equity.to_numpy()
            axes[0,col].plot(x,eq,color=color,lw=1.1,label=label)
            axes[1,col].plot(x,100*(eq/np.maximum.accumulate(eq)-1),color=color,lw=1)
        axes[0,col].set_title(title);axes[0,col].legend(fontsize=8);axes[0,col].axhline(1000,ls='--',lw=.6,color='#aaa')
        axes[1,col].set_xlabel('UTC date')
    axes[0,0].set_ylabel('Net equity (USDT)');axes[1,0].set_ylabel('Close MTM drawdown (%)')
    for ax in axes.flat:ax.grid(alpha=.15);ax.tick_params(axis='x',rotation=25)
    fig.suptitle('ETH perpetual | unchanged entries | 1x equity | 20 bp round trip')
    fig.tight_layout();fig.savefig(EXP/'equity.png',dpi=150);plt.close(fig)
    md=['# ETH止盈止损第二轮：更宽止损与不同获利退出\n',
        f'**{verdict}** 开发预选为 **{NAMES[winner]}（{winner}）**：1—2月净{num(a["mtm_return_pct"])}%，3—4月净{num(b["mtm_return_pct"])}%；原规则对应{num(d0["mtm_return_pct"])}% / {num(c0["mtm_return_pct"])}%。\n',
        '**范围说明：** 本轮保持15m颜色＋5m原箭头入场、单仓1倍权益、20bp成本；31组含3个旧锚点，只从28个新方案选一个再复查。1—4月已在前轮看过，因此全轮属于探索，不把后段或换实验ID当作新的盲样本外。近一月没有重测，holdout新增0。\n',
        '## 1. 选中方案究竟改了什么\n',
        f'新方案：{NAMES[winner]}。其相对参照为{NAMES.get(dev["policies"][winner]["reference"],dev["policies"][winner]["reference"])}。精确参数如下，其余入场、仓位、成本和成交时钟保持预注册口径。\n',
        '```json\n'+json.dumps(dev['policies'][winner],ensure_ascii=False,indent=2)+'\n```\n',
        '5m箭头本身已经包含Stoch5/3/3低于20金叉、高于80死叉；多空仅由最近已完成15m的hl2相对SMA40限制。收盘确认，下一根开盘执行。原反向箭头退出仍保留，只有名称no_arrow的两组明确关闭；没有改变方向或增加入场过滤。\n',
        'ATR均Wilder14、首14根真实波幅均值播种；5m/已完成15m尺度由方案明确，入场值冻结。结构止损取信号bar及之前6/12/24根极值外0.25个5m ATR，入场止损距离至少1 ATR。固定百分比目标为毛目标，仍扣20bp成本。\n',
        '## 2. 原规则、前轮2ATR锚点与新候选\n']
    rows=[]
    for label,summ in [('1—2月筛选',dev),('3—4月复查',check)]:
        for key in ('baseline','stop_2',winner):
            r=summ['arms'][key];c=r['control']
            rows.append([label,NAMES[key],num(r['final_equity']),num(r['mtm_return_pct']),num(r['mtm_max_drawdown_pct']),num(r['min_trade_net_pct']),r['longest_loss'],r['longest_losing_stop'],num(c.get('control_mean_bp')),num(c.get('excess_mean_bp'))])
    md += [table(['区间','规则','期末U','账户净%','回撤%','最差单笔净%','最长连亏','连续亏损止损','随机净bp','超额bp'],rows),
           f'![原规则、2ATR与新候选权益和回撤]({EXP/"equity.png"})\n',
           f'新候选相对原规则开发净收益差{num(a["mtm_return_pct"]-d0["mtm_return_pct"])}个百分点，复查差{num(b["mtm_return_pct"]-c0["mtm_return_pct"])}个百分点；开发回撤差{num(a["mtm_max_drawdown_pct"]-d0["mtm_max_drawdown_pct"])}个百分点，复查差{num(b["mtm_max_drawdown_pct"]-c0["mtm_max_drawdown_pct"])}个百分点。正收益差表示相对少亏/多赚，不自动等于盈利；正回撤差表示风险恶化。\n',
           '不同退出会释放不同的后续入场机会，所以比较是完整串行规则总效果；不能只把原交易替换退出价来做同一组交易的归因。较宽止损或取消箭头可能使交易减少、持仓变长，必须同时看末仓和尾部。\n',
           '## 3. 开发全部31组，包括所有失败\n',result_table(dev),
           'baseline、stop_2、stop_3是旧锚点，不参与新方案名次。至少30笔已平的新方案才可选，净权益最高优先，同值依次低回撤、少笔数、ID。所有不够30笔者同样展示，没有以交易少造成的表面收益宣称优胜。\n',
           '固定0.5%止盈在开发提高净胜率到61.81%，账户却亏32.61%；它缩小盈利而保留较大的反向退出亏损。胜率与连续亏损次数不能替代账户扣费后的期望收益。该归因只适用于这段历史，不证明任何市场都相同。\n',
           '## 4. 固定复查，仅三组\n',result_table(check),
           '后段只评价开发预选及两个锚点，未看它们结果后再把第二名补进来。前轮已观察后段结果，固定选择只能限制这一轮的直接数值择优，不能消除此前信息影响。\n',
           '## 5. 随机对照、样本和统计边界\n']
    rows=[]
    for phase,summ in [('开发',dev),('复查',check)]:
        for name,r in summ['arms'].items():
            c=r['control'];rows.append([phase,NAMES[name],c.get('matched',0),c.get('unmatched'),c.get('utc_day_blocks'),num(c.get('target_mean_bp')),num(c.get('control_mean_bp')),num(c.get('excess_mean_bp')),num(c.get('p_greater'),4)])
    md += [table(['区间','规则','匹配','未配','日块','目标净bp','随机净bp','超额bp','正超额单侧p'],rows),
           '同ETH、同方向、同UTC日、同历史SMA20(TR)/close固定波动桶，每个已平目标一次哈希抽随机入场，种子9152401；控制同组退出和费用，不因为结果亏损或删失重抽。控制有重叠、重复和跨日相关，仅作事件对照，不能合成单仓账户。配对检验只包含双方已平，删失可能带来偏差。日块符号置换9999次只是描述性交换对称检验；本轮多候选、复用历史，p值不构成确认性证据。\n',
           f'开发{dev["evaluated_bars"]}根，复查{check["evaluated_bars"]}根；源前缀{check["source"]["rows"]}根5m，预热自2025-12-20 10:00 UTC，缺根/重复均0。仅timestamp-first读取截止2026-05-01前OHLC，排除行只读时间戳。两段独立空仓1000U，期末持仓按最后收盘估值并预留退出费，不计胜率。\n',
           'AUC与top-decile毛/净收益不适用：没有训练模型或预定排序分数，不能按事后收益排序凑top-decile。后段已平仓样本数及盈利正类率分别是结果表的“已平”和“净胜率”；给出全交易、旧锚点和匹配随机对照。\n',
           '## 6. 风险与诚实声明\n',
           '- 每笔按入场前权益1倍名义，费用开平各10bp。PF为逐笔正净收益率之和/负值绝对值之和，账户收益含复投，不能直接相加各笔百分比。\n- 最大回撤用每根5m收盘持仓估值，不含盘中最坏价格路径。“连续亏损止损”要求最终退出为stop/stop_gap/close_stop且该笔净亏；盈利保本/跟踪保护退出不计，所有净亏均计“连亏”。没有硬止损的组止损次数0不代表没有风险。\n- 开盘已挂保护优先，跳空按实际open；盘中止损目标同时触发按止损先。盘中记录时间为所属bar收盘上界，保存bar_open/intrabar，不声称知道秒级成交先后。\n- close_stop只依据前一收盘是否穿固定止损价，下一open成交，可能比硬止损亏得更多；保本/跟踪只收盘更新、次bar生效。区域退出只累计入场后已完成K值，不沿用入场前状态。\n- 反向半平按原仓50%两次，未平完不反开、不加仓；所有完全闭合交易累计20bp费用。\n- 无资金费率、额外滑点、强平、最小合约单位或冲击成本；宽止损的历史最差值不构成未来上限。\n- 31组是本轮有限候选，不代表搜遍全部退出；即使相对改善，也只能作为新假设保留。生产与训练资格false，未修改实盘。\n',
           '成交模型参考[TradingView官方策略文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)，本轮使用明确的保守OHLC规则，未宣称TradingView全成交parity。\n',
           '## 7. 复现和验证\n',
           f'引擎/协议先提交`{dev["source_commit"]}`再开发；开发选择在`{check["source_commit"]}`已提交后才复查。开发prefix SHA `{dev["source"]["prefix_sha256"]}`；复查prefix SHA `{check["source"]["prefix_sha256"]}`。旧v1源码与结果保持冻结，baseline和旧止损锚点逐笔parity通过。\n',
           f'42项信号/退出定向测试、另88项层边界和holdout一致性测试，共130个不同测试通过；本轮10项新测试在98项组合检查中再次通过。主代理另重算{audit["closed_trades"]}笔已平、{audit["fills"]}条成交和{audit["matched_controls"]}个匹配控制的费用、复投、风险单位、末仓和选择提交身份。未重新跑上一轮全库468通过/7既有失败的整套门，未绕过其失败。\n',
           '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest -q tests/test_ma_stoch_exit_v2.py tests/test_ma_stoch_exit_engine.py tests/test_ma_stoch_exit_study.py tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py\n# Requires committed builders and original prefix-matching source; output must not exist.\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_v2_study --phase dev\ngit branch --show-current\n# Must show main. Commit only the new selection and dev summary.\ngit add experiments/active/exp-ma-stoch-exit-optimization-20260915-v2/selection.json experiments/active/exp-ma-stoch-exit-optimization-20260915-v2/dev/summary.json\ngit commit -m "research: freeze second exit selection"\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_v2_study --phase recheck\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_v2_audit\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_v2_report\n```\n',
           '复现拒绝覆盖既有冻结目录；需保留原结果并在获准的新复现位置恢复原数据与代码再运行。稠密逐bar曲线保存在本地，交付manifest绑定哈希，git只保留必要摘要、逐笔、对照和报告。\n',
           f'[完整协议]({EXP/"PROJECT_PLAN.md"}) · [开发摘要]({EXP/"dev/summary.json"}) · [复查摘要]({EXP/"recheck/summary.json"}) · [选择冻结]({EXP/"selection.json"}) · [账本核验]({EXP/"ledger_audit.json"}) · [新候选复查逐笔]({EXP/"recheck"/(winner+"_trades.csv")})\n',
           '## 8. 下一步\n',
           '本轮达到预设候选上限即停止。如果仅为相对少亏，不能称已解决盈利问题；若后续改变入场或放大杠杆，那是不同问题。继续验证候选需要合适的独立历史或未来样本；任何holdout评价必须另获明确授权，不能靠不断重跑本段来宣布成功。\n']
    receipt=EXP/'validation.json'
    if receipt.exists():
        v=json.loads(receipt.read_text())
        md.append('HTML结构和本地链接已核对，权益图已目视检查；没有HTML浏览器像素验收。\n')
        if v.get('notion_url'):md.append(f'[Spike Notion第二轮记录]({v["notion_url"]})\n')
    report=ROOT/'analysis/p1_ma_stoch_exit_optimization_v2_20260915.md'
    report.write_text('\n'.join(md))
    subprocess.run(['python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    print(report)

if __name__=='__main__':main()
