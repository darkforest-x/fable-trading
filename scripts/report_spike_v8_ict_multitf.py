"""Render frozen ICT entry-filter results; never read price sources or tune rules."""
import json
from pathlib import Path

import pandas as pd

from report_spike_fanshen import fmt, table

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-v8-ict-multitf-20260915-v1'
REPORT=ROOT/'analysis/p1_spike_v8_ict_multitf_20260915.md'
TF={3:'3min',5:'5min',15:'15min',30:'30min',60:'1h',240:'4h'}


def main():
    out=EXP/'results'
    summary=pd.read_csv(out/'summary.csv')
    cash=pd.read_csv(out/'cash_summary.csv')
    cfg=json.loads((EXP/'config.json').read_text())
    receipt=json.loads((out/'run_receipt.json').read_text())
    primary=cfg['primary']
    def get(m,w,p,s):
        return summary.loc[(summary.minutes==m)&(summary.window==w)&(summary.policy==p)&(summary.session==s)].iloc[0]
    common=[get(m,'common',primary,'union') for m in TF]
    improved=[TF[int(r.minutes)] for r in common if r.sum_net_r>get(int(r.minutes),'common',primary,'all').sum_net_r]
    positive=[TF[int(r.minutes)] for r in common if r.sum_net_r>0]
    parts=['# 原V8 × ICT时段：六周期对照\n',
        'Owner在2026-09-15要求所有周期只在此前ICT时段开仓。本次已比较6周期、原V8退出、全天与ICT合并时段、两历史窗口，共24组交易统计和48组资金账本。\n',
        f'按Owner最新要求，已经去掉翻身指标退出，保留原V8止损和跟踪。共同2026年1—4月中，ICT后总净R改善的周期：{"、".join(improved) or "无"}；ICT后净R为正的周期：{"、".join(positive) or "无"}。这两组名单不等价，少做交易可减亏但未必建立正期望。\n',
        '## 原V8：全天开仓与ICT开仓\n']
    for window,label in [('common','共同2026年1—4月'),('available','各周期较长可用历史')]:
        parts.append(f'### {label}\n')
        rows=[]
        for m in TF:
            a,b=get(m,window,primary,'all'),get(m,window,primary,'union')
            rows.append([TF[m],f'{int(a.natural)}→{int(b.natural)}',f'{fmt(a.win_rate,True)}→{fmt(b.win_rate,True)}',
                f'{fmt(a.sum_net_r)}→{fmt(b.sum_net_r)}',f'{fmt(a.profit_factor)}→{fmt(b.profit_factor)}',
                f'{int(a.max_net_loss_streak)}→{int(b.max_net_loss_streak)}',fmt(a.random_mean_net_r)+'→'+fmt(b.random_mean_net_r),fmt(b.excess_net_r),fmt(b.p_holm)])
        parts.append(table(['周期','自然笔数 全天→ICT','净胜率','总净R','PF','最长净亏连单','随机均净R 全天→ICT','ICT超额均净R','校正p'],rows))
    parts.append('R为初始价格止损距离单位，净收益已扣名义金额0.2%往返成本。固定价格风险1U与无限容量串行总净R不是任何杠杆的账户百分比；末尾未自然平仓单独估值，不混进胜率。PF在没有亏损分母时显示“—”，不显示虚假的零值。\n')
    parts.append('## 时段定义与执行\n')
    parts.append('纽约本地London[02:00,05:00)、lunch[05:00,08:00)、NewYork[08:00,11:00)，三段合并为[02:00,11:00)。夏令时北京时间14:00—23:00；冬令时15:00—次日00:00。使用America/New_York处理DST。起点含、终点不含，周末保留。\n')
    parts.append('按信号确认后的下一根实际开盘判定是否准入，信号bar的开盘时刻不能替代它；时段外不延后排队。只筛开仓，持仓可跨时段，全部退出一直有效。先筛完整机会集合再重建单仓；全天旧持仓被剔除后，原本被挡住的信号可以进入，不能仅删除旧成交表中的几行。高周期只在自身网格开盘，时段并非连续下单窗口。\n')
    parts.append('时段来源是Owner图表在上轮观察到的夏令时边界；冬季按纽约同钟面的实现仍是明确模型假设，受保护原脚本尚未核实冬季行为。未做TradingView原生逐信号/成交对齐。\n')
    parts.append('## 保护规则与准入变化\n')
    parts.append('原V8保持5bar初始极值止损、0.2ATR缓冲及2ATR风险下限；收盘浮盈达到2R激活原4ATR跟踪，原V6反向信号仍按下一开盘退出。没有新增固定止盈、保本或翻身指标退出。\n')
    for window in ['common','available']:
        rows=[]
        for m in TF:
            a,b=get(m,window,primary,'all'),get(m,window,primary,'union')
            rows.append([TF[m],a.start,f'{int(a.max_initial_stop_streak)}→{int(b.max_initial_stop_streak)}',
                f'{int(a.gross3r)}→{int(b.gross3r)}',int(b.retained_all_day),int(b.new_after_filter),int(b.dropped_all_day),
                f'{int(b.matched_n)}/{int(b.natural)}'])
        parts.append(f'### {window}\n')
        parts.append(table(['周期','起点UTC','最长初始SL 全天→ICT','实现毛3R 全天→ICT','保留旧成交','新增准入','旧成交未进入','ICT完整随机匹配'],rows))
    parts.append('## 1000U固定风险与亏后翻倍\n')
    for window,label in [('common','共同窗口'),('available','各周期可用历史')]:
        parts.append(f'### {label}：原V8退出\n')
        rows=[]
        for m in TF:
            sub=cash.loc[(cash.minutes==m)&(cash.window==window)&(cash.policy==primary)]
            records={(r.session,r.schedule):r for r in sub.itertuples()}
            a,b=records['all','fixed'],records['union','fixed']
            c,d=records['all','double'],records['union','double']
            rows.append([TF[m],fmt(a.final_balance)+'→'+fmt(b.final_balance),fmt(c.final_balance)+'→'+fmt(d.final_balance),
                int(d.n_natural),fmt(d.max_realized_drawdown,True),int(d.n_capacity_rejected),fmt(d.residual_debt),
                str(d.halt_time) if pd.notna(d.halt_time) else '无永久终止'])
        parts.append(table(['周期','固定1U期末 全天→ICT','翻倍期末 全天→ICT','ICT翻倍自然笔数','已实现最大回撤','容量拒绝','未回本U','永久终止时刻UTC'],rows))
    parts.append('基础1U仅为价格止损风险，费用另计；10倍保证金容量沿用旧研究。亏后翻倍，累计净回本才重置；保本不抹去前亏，也不跨日清账。容量不足会拒绝入场且不占仓，因此现金账户可能与不受容量约束的串行交易表走出不同序列。“无永久终止”可能只是长期等待容量，必须一起看成交数、拒绝次数及未回本金额。未建模标记价格强平、资金费率、额外滑点或盘中浮动权益。\n')
    parts.append('## 对照与核验\n')
    parity=json.loads((out/'baseline_parity.json').read_text())
    rows=[]
    for kind in ['archived_all_day','original_engine_union']:
        group=[r for r in parity if r['kind']==kind]
        rows.append([kind,len(group),sum(r['rows'] for r in group),all(r['passed'] for r in group)])
    parts.append(table(['核验','组数','行数（重叠窗口不独立）','通过'],rows))
    parts.append('每笔自然交易匹配5个同ETH/周期/方向、实际入场UTC月、纽约小时、纽约周末状态、信号ATR/close相对前120根波动桶的随机入场，退出规则与成本一致。池仅在对应窗口和允许时段内；未来收益只用于标签，不用于选桶。匹配不够5笔的交易不混入完整匹配均值，缺配对与删失重抽保留。相较上轮，本轮全天对照也加入相同小时/周末条件，故随机列不应与旧报告直接数值比对。\n')
    parts.append('随机入场是独立交易对照，不构成可执行随机账户。月块符号置换9999次，每窗口最多12项Holm校正；两个窗口重叠，不能视为两次独立验证。无预测排序，AUC、top-decile排序收益与单特征评分基线不适用，以全天同规则及匹配随机交易作为零假设对照。\n')
    parts.append(f'冻结builder：`{receipt["builder_commit"]}`。所有源前缀、旧机会表与输出哈希见input_receipts.json、verified_previous_inputs.json、manifest.json。holdout使用次数为0，所有价格在2026-05-01之前。测试与独立复核记录见实验目录的validation_receipt.json。\n')
    parts.append('## 逐笔CSV\n')
    delivery=EXP/'delivery';delivery.mkdir(exist_ok=True)
    for m in TF:
        for window in ['common','available']:
            df=pd.read_csv(out/f'{m}m_{window}_{primary}_union_trades.csv.gz')
            for col in ['entry_time','exit_time']:
                df[col+'_beijing']=pd.to_datetime(df[col],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
            df['direction']=df.side.map({1:'多',-1:'空'})
            cols=['direction','entry_time_beijing','entry_ny','ict_segment','entry_price','initial_stop','exit_time_beijing','exit_price','exit_at_open','exit_reason','gross_r','net_r','censored']
            names=['方向','入场北京时间','入场纽约时间','ICT时段','入场价','初始止损','退出根北京时间','退出价','是否开盘退出','退出原因','毛R','净R','是否边界未平']
            df=df[cols];df.columns=names
            path=delivery/f'ETH_{m}m_{window}_ict_original_exit.csv';df.to_csv(path,index=False,encoding='utf-8-sig')
            parts.append(f'- [{TF[m]} {window} ICT原V8逐笔](../../experiments/active/{EXP.name}/delivery/{path.name})\n')
    parts.append('退出根北京时间对盘中止损只是所属K线开盘标签，不能冒充精确秒；exit_at_open为true才是该根开盘成交。全天和ICT的逐笔、随机匹配、现金账户及准入变化在实验results目录。\n')
    parts.append('## 风险与诚实声明\n')
    parts.append('这轮只改变ICT开仓时段，没有调退出参数、成本或止损。历史已在先前研究中看过，不是盲测。5m较长源只从2025-12-25预热后开始，其余可用起点不同，不能用较长总R跨周期排序；4h信号稀少，缩小时段后样本可能更少。自然连亏变短可能来自样本缩小，不能承诺未来最多6次；多个周期单独账户不是并行组合回测。未训练、promote或改动实盘。\n')
    parts.append('## 复现命令\n')
    parts.append('```bash\ncd /Users/zhangzc/fable-trading\nexport PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages:/Users/zhangzc/fable-trading\n/usr/bin/python3 -m pytest -q tests/test_spike_v8_ict.py tests/test_spike_v8_ict_multitf.py\n# 必须保留原结果；冻结源码回放要求新实验results目录尚不存在\n/usr/bin/python3 -m yoyo.evaluation.spike_v8_ict_multitf\n/usr/bin/python3 scripts/report_spike_v8_ict_multitf.py\n/usr/bin/python3 scripts/md_to_html.py analysis/p1_spike_v8_ict_multitf_20260915.md --out-dir analysis/html\n```\n')
    parts.append('## 下一步\n')
    parts.append('只有跨窗口表现一致且样本足够的候选才值得单独冻结验证；任何新的holdout评估须按配置确认授权并记录次数。当前结果不能代替原生TV逐笔核对，也没有实盘准入。\n')
    REPORT.write_text('\n'.join(parts))
    print(REPORT)


if __name__=='__main__':main()
