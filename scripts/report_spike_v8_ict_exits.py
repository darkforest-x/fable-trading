"""Render frozen exit results only; never load market prices or choose new arms."""
import json
from pathlib import Path

import pandas as pd

from report_spike_fanshen import fmt, table

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-v8-ict-exits-20260915-v1'
REPORT=ROOT/'analysis/p1_spike_v8_ict_exits_20260915.md'
LABELS={'original':'原版4ATR跟踪','trail3_atr':'3ATR跟踪','trail2_atr':'2ATR跟踪',
 'cost_be1':'净浮盈1R后含费保本','cost_be2':'净浮盈2R后含费保本',
 'partial25_at2':'毛2R兑现25%','partial50_at2':'毛2R兑现50%','fixed3r':'毛3R全平'}
WINDOWS={'development':'开发：2023年8月—2024年末','validation':'后续：2025全年',
 'common':'后续：2026年1—4月','available':'连续：2023年8月—2026年4月'}


def fee_floor_exits(df):
    """Descriptive rounding-sized exits near the fee floor, not a new strategy."""
    return ((df.protection_mode=='cost') & df.protection_armed.fillna(False)
        & df.exit_reason.str.startswith('trailing_stop') & ~df.censored
        & (df.net_r>=-1e-9) & (df.net_r<=.01/df.initial_risk+1e-9))


def main():
    summaries=[];cash_frames=[];parity=[]
    for phase in ['develop','evaluate']:
        out=EXP/'results'/phase
        summaries.append(pd.read_csv(out/'summary.csv'))
        cash_frames.append(pd.read_csv(out/'cash_summary.csv'))
        parity.extend(json.loads((out/'parity.json').read_text()))
    data=pd.concat(summaries,ignore_index=True);cash=pd.concat(cash_frames,ignore_index=True)
    selection=json.loads((EXP/'results/selection.json').read_text());chosen=selection['selected_policy']
    indexed=data.set_index(['window','policy'])
    parts=['# ETH15min＋ICT：止盈与利润保护的8套对照\n',
        '问题是能否减少浮盈回吐，同时保留原V8的大赢家。本轮只研究OKX ETH-USDT-SWAP 15min；翻身指标退出已去掉。没有重跑其他周期，也没有扩大参数网格。\n',
        f'开发期按预先冻结的“自然交易至少30笔、总净R最高”选中**{LABELS[chosen]}**，开发净R为{fmt(indexed.loc[("development",chosen),"sum_net_r"])}。开发期8套均亏损，选中仅表示比较内亏得最少。选择文件先提交，随后才读取2025及2026年数据。\n',
        '## 各年份总净收益：不能只挑近期冠军\n']
    rows=[]
    for policy,label in LABELS.items():
        rows.append([label+('（开发选中）' if policy==chosen else '')]+[
            fmt(indexed.loc[(w,policy),'sum_net_r']) for w in WINDOWS])
    parts.append(table(['退出规则','开发净R','2025净R','2026前4月净R','连续历史净R'],rows))
    consistent=[LABELS[p] for p in LABELS if p!='original' and all(
        indexed.loc[(w,p),'sum_net_r']>indexed.loc[(w,'original'),'sum_net_r'] for w in ['validation','common'])]
    parts.append('在2025和2026前4月都超过原版的修改：'+('、'.join(consistent) if consistent else '**没有**')+'。连续历史包含开发期，不能拿它当独立验证；原版不是保证盈利的方案，只是判断修改有没有增益的必要对照。\n')
    parts.append('**可继续观察的方向是“净浮盈2R后含费保本＋保留原4ATR尾仓跟踪”。** 它在2025把净R从1.93提高到3.26，2026前4月从22.74提高到24.68，后者结算回撤从3.29R降到2.25R，未截短原毛3R赢家。但开发期更差，连续历史−10.92R也略差于原版−9.84R；这是8个冻结候选中的事后观察，不能替换开发预选方案并冒充验证通过。\n')
    parts.append('开发预选的固定3R在后续两段均落后原版；3ATR/2ATR跟踪及2R分批也都减少了后续净收益。净1R保护在连续历史把最长净亏从14降到6，却仍为124笔净−10.58R，较原版少赚0.74R；其同入场退出损失16.38R，提前释放仓位带来的新增准入抵消了大部分。历史6连亏不能成为未来上限，更不能推出倍投必赚。\n')
    for window,title in WINDOWS.items():
        parts.append(f'## {title}\n');sub=data.loc[data.window==window]
        rows=[]
        for r in sub.itertuples():
            rows.append([LABELS[r.policy],int(r.candidates),f'{int(r.natural)}＋{int(r.open)}',fmt(r.win_rate,True),
                fmt(r.sum_gross_r),fmt(r.sum_net_r),fmt(r.profit_factor),int(r.max_net_loss_streak),
                int(r.max_initial_stop_streak),fmt(r.max_realized_drawdown_r),int(r.gross3r)])
        parts.append(table(['规则','候选','自然＋边界','净胜率','总毛R','总净R','PF','最长净亏','最长完整初始SL','结算回撤R','毛≥3R笔数'],rows))
        rows=[]
        for r in sub.itertuples():
            rows.append([LABELS[r.policy],f'{int(r.matched_n)}/{int(r.natural)}',fmt(r.random_mean_net_r),
                fmt(r.excess_net_r),fmt(r.p),fmt(r.p_holm),int(r.months)])
        parts.append(table(['规则','完整匹配/自然','随机均净R','匹配超额均净R','月块p','Holm校正p','匹配月数'],rows))
        parts.append('每行退出均与同币、同方向、同入场月/纽约小时/周末状态、同因果波动桶的随机入场配对，目标每笔5个。匹配超额只在完整匹配子集上计算，不能拿全体均值减随机均值复算；缺匹配明确保留。\n')
    parts.append('## 同一入场：救回亏损与截短赢家\n')
    for window in ['validation','common','available']:
        parts.append(f'### {WINDOWS[window]}\n');rows=[]
        for r in data.loc[data.window==window].itertuples():
            rows.append([LABELS[r.policy],int(r.paired_n),fmt(r.paired_delta_net_r),int(r.paired_improved),
                int(r.paired_worsened),int(r.saved_losers),int(r.lost_3r_winners),int(r.partial_trades),int(r.ambiguous_trades)])
        parts.append(table(['规则','配对数','同入场净R增减','改善','变差','原净亏转净赚','原毛3R被截短','分批单','止损/目标同根歧义单'],rows))
    parts.append('同入场对照固定原版实际成交信号，允许修改退出后原信号之间相互重叠，用于隔离退出本身；不能当作可同时执行的账户。完整串行统计则从全部机会重建单仓，提前退出会释放后续信号，因此两种差值不应混用。\n')
    parts.append('## 原版回吐诊断与大赢家逐笔对照\n')
    out=EXP/'results/evaluate';original=pd.read_csv(out/'common_original_trades.csv.gz')
    natural=original.loc[~original.censored]
    losers=natural.loc[natural.net_r<0];giveback=natural.loc[(natural.mfe_r>=1)&(natural.net_r<0)]
    top=natural.nlargest(3,'net_r')
    parts.append(f'2026前4月原版{len(natural)}笔中，记录到浮盈至少1R后净亏的有{len(giveback)}笔，合计{fmt(giveback.net_r.sum())}R；原版全部净亏合计{fmt(losers.net_r.sum())}R。前三个赢家合计{fmt(top.net_r.sum())}R，占原版总净收益{fmt(top.net_r.sum()/natural.net_r.sum(),True)}。因此保护小回吐必须同时检查大赢家被减掉多少。\n')
    rows=[]
    pairs={p:pd.read_csv(out/f'common_{p}_paired.csv.gz').set_index('signal_i') for p in LABELS}
    for r in top.itertuples():
        rows.append([str(pd.Timestamp(r.entry_time).tz_convert('Asia/Shanghai'))]+[
            fmt(pairs[p].loc[r.signal_i,'new_net_r']) for p in LABELS])
    parts.append(table(['原前三赢家入场北京时间']+list(LABELS.values()),rows))
    parts.append('MFE沿用旧引擎记录，不包含被止损根的新有利极值。它是路径诊断，不证明能成交在最高点；固定止盈是否成交由逐根订单模型判断，不能直接把MFE减实际收益叫作可追回利润。\n')
    parts.append('## 含费保本与净胜率的区别\n');rows=[]
    for window in ['validation','common','available']:
        for p in ['cost_be1','cost_be2']:
            df=pd.read_csv(out/f'{window}_{p}_trades.csv.gz');be=fee_floor_exits(df);nat=~df.censored
            substantive=nat & (df.net_r>1e-9) & ~be
            rows.append([WINDOWS[window],LABELS[p],int(nat.sum()),int(substantive.sum()),int(be.sum()),
                int((nat & (df.net_r < -1e-9)).sum()),fmt(substantive.sum()/nat.sum(),True),f'{df.loc[be,"net_r"].sum():.6f}'])
    parts.append(table(['窗口','规则','自然数','剔除保护位微盈后的盈利','含费保护位微盈','净亏','剔除微盈胜率','微盈合计R'],rows))
    parts.append('微盈定义为含费保护已激活、经移动止损退出且净收益在0至一个价格最小步长折算R内，只做结果口径拆分，不改变交易或参数。主表按净R>1e-9算胜，故2026前4月净2R保护的64.7%含2笔合计约0.00118R的微盈；分开看是9笔盈利、2笔含费保本微盈、6笔净亏。现金旧引擎按exit_reason识别net_be，而此重放统一把移动保护退出记作trailing_stop，因此现金n_net_be字段不能用于经济保本计数；不影响其按累计实际净金额决定回本重置。\n')
    parts.append('## 1000U账户：固定价格风险1U与亏后翻倍\n')
    for window in WINDOWS:
        parts.append(f'### {WINDOWS[window]}\n');rows=[]
        for p in LABELS:
            sub=cash.loc[(cash.window==window)&(cash.policy==p)].set_index('schedule');a,b=sub.loc['fixed'],sub.loc['double']
            rows.append([LABELS[p],fmt(a.final_balance),fmt(a.max_realized_drawdown,True),fmt(b.final_balance),
                int(b.n_natural),int(b.n_boundary),fmt(b.max_realized_drawdown,True),int(b.n_capacity_rejected),fmt(b.residual_debt)])
        parts.append(table(['规则','固定期末U','固定结算回撤','翻倍期末U','翻倍自然笔数','边界数','翻倍结算回撤','容量拒绝','未回本U'],rows))
    parts.append('基础1U是价格止损风险，成本另计；10倍保证金容量保持旧研究假设。净亏后翻倍，累计净收益偿清前亏才重置，保本不抹去债务。资金不足时拒绝新仓，拒绝会改变后续序列；高期末余额若只做了少数单，不能替代全部机会表现。分批资金在整单终结时合计结算，以上仅为整单已实现回撤，未模拟分批到账时点、盘中权益或标记价格强平。未纳入资金费率和额外滑点。\n')
    parts.append('## 冻结执行规则与时间边界\n')
    parts.append('所有规则保留V8入场、5bar极值初始止损、0.2ATR缓冲、2ATR风险下限和原V6反向下一开盘退出。只在纽约本地[02:00,11:00)的实际下一根开盘准入，周末保留，时段外继续管理持仓。夏令时北京时间14—23点、冬令时15—次日0点；受保护ICT脚本冬季行为未核实，这里明确采用America/New_York模型。\n')
    parts.append('原跟踪在收盘浮盈达到毛2R后激活，保持单向收紧；3ATR/2ATR仅改变跟踪宽度。新增含费保本使用该根未先止损且高/低曾达到净1R或净2R的条件，在收盘确认，下一根生效；保护价相对开仓价有利偏移0.2%，再按0.01价格步长取整。这不是触及即生效，跳空仍可能净亏。\n')
    parts.append('分批是在毛2R处卖出/买回初始数量25%或50%，剩余继续原4ATR跟踪，不叠加新的残仓止损。毛3R全平方案保留初始止损、原跟踪与反向退出，新增一个更早可成交的上限。一笔名义0.2%往返成本按成交份额分配，绝不每一批再扣完整成本；分批不改变初始R分母。残仓碰到初始止损不等于全仓完整初始SL。\n')
    parts.append('限价目标入场后即有效，跳空用观察到的开盘价；同根止损和目标都可触发时止损优先并标记歧义。OHLC不能给出所有盘中顺序，TradingView也明确区分默认模拟路径和更细粒度数据；这里采用明确的保守规则，不声称已与TV原生逐笔一致。[TradingView策略执行文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)\n')
    parts.append('开发只读2025-01-01前价格且标签在该点截断；选择JSON提交后才读后续价格。2025标签同样在2026-01-01前截断；2026前4月和连续历史截止2026-05-01。所有窗口未自然终结的持仓单列边界，不混入自然胜率；连续窗单独回放，不能拼接每年重置的资金账本。所有规则holdout消耗0次。\n')
    parts.append('## 可复现证据\n')
    parts.append(table(['窗口','基线核验','明细'],[[r['window'],r['kind'],json.dumps({k:v for k,v in r.items() if k not in ['window','kind']},ensure_ascii=False)] for r in parity]))
    parts.append(f'共{len(data)}组统计、{len(cash)}组现金账本。冻结builder为`{selection["builder_commit"]}`；开发选择提交与评价读入提交见results/evaluate/read_receipt.json。每个输入前缀和输出文件的SHA在分阶段manifest.json中，分批比例、毛R贡献、成本与净R均由运行断言核对。测试与复核详情见validation_receipt.json。\n')
    parts.append('开发读入104962根，最早2022-01-03 15:30 UTC用于预热，最后完整收盘2025-01-01 00:00；评价读入151522根，最后完整收盘2026-05-01 00:00。两个前缀均无缺口、无重复、受限价格解析行数为0；正式开仓起点2023-08-01，较早价格只用于因果预热。\n')
    parts.append('本轮45项退出专项检查通过。扩大至仓库边界和holdout单一定义的检查为184通过、5失败；失败均因既有spike-v8-total2-1h-native-20260914-delivery登记缺source_commit，原HEAD已存在此缺项，本轮未改该记录，不能声称仓库检查全绿。HTML静态结构为19张表、16个逐笔CSV链接均可解析到本地文件；应用浏览器安全策略阻止file URL预览，因此未声称完成视觉渲染核验。\n')
    parts.append('每窗口8项月块9999次符号置换做Holm校正；这些检验针对匹配随机入场超额，不是退出修改相对原版的显著性检验。无预测模型或排名，AUC、top-decile毛净收益、单特征评分基线不适用，给出原版与匹配随机入场作为零假设对照，不编造分类指标。\n')
    parts.append('## 逐笔下载\n');delivery=EXP/'delivery';delivery.mkdir(exist_ok=True)
    for window in ['common','available']:
        for p in LABELS:
            df=pd.read_csv(out/f'{window}_{p}_trades.csv.gz')
            df['方向']=df.side.map({1:'多',-1:'空'})
            for col,label in [('entry_time','入场北京时间'),('exit_time','退出根北京时间')]:
                df[label]=pd.to_datetime(df[col],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
            names={'entry_price':'入场价','initial_stop':'初始止损','exit_price':'末次退出价','exit_reason':'末次退出原因',
                'gross_r':'整单毛R','net_r':'整单净R','full_initial_stop':'全仓完整初始SL','partial_executed':'是否分批',
                'fills':'分批成交明细','censored':'边界未自然平仓','exit_at_open':'末次是否开盘退出'}
            df=df[['方向','入场北京时间','退出根北京时间']+list(names)].rename(columns=names)
            path=delivery/f'ETH_15m_{window}_{p}.csv';df.to_csv(path,index=False,encoding='utf-8-sig')
            parts.append(f'- [{WINDOWS[window]}｜{LABELS[p]}](../../experiments/active/{EXP.name}/delivery/{path.name})\n')
    parts.append('退出根时间对盘中成交只是K线标签，不是精确秒；分批单收益是所有fills加权结果，末次退出价无法单独还原整单收益。\n')
    parts.append('## 风险与诚实声明\n')
    parts.append('研究历史已经被以前实验观察过，因此这次按时间分段也不是全新盲测。只测试预先列明的8套，不等于搜索到了所有止盈的全局最优。原版近期17笔的盈利集中于少数大单，任何胜率、最大连亏或回撤改善都不能保证未来。固定3R和保本不能保证倍投回本；本轮没有使用holdout、训练、promote或改动实盘。\n')
    parts.append('## 复现命令\n')
    parts.append('```bash\ncd /Users/zhangzc/fable-trading\nexport PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages:/Users/zhangzc/fable-trading\n/usr/bin/python3 -m pytest -q tests/test_spike_v8_ict_exit_study.py tests/evaluation/test_spike_partial_exit.py tests/evaluation/test_spike_recovery_exit.py tests/test_spike_v8_ict.py\n# 只在源文件已提交且对应phase目录不存在的干净复现环境运行；不得覆盖本轮证据\n/usr/bin/python3 -m yoyo.evaluation.spike_v8_ict_exit_study develop\ngit branch --show-current\ngit add experiments/active/exp-spike-v8-ict-exits-20260915-v1/results/selection.json\ngit commit -m "research: freeze development exit selection"\n/usr/bin/python3 -m yoyo.evaluation.spike_v8_ict_exit_study evaluate\n/usr/bin/python3 scripts/report_spike_v8_ict_exits.py\n/usr/bin/python3 scripts/md_to_html.py analysis/p1_spike_v8_ict_exits_20260915.md --out-dir analysis/html\n```\n')
    parts.append('## 下一步选项\n')
    parts.append('先保留原版作为研究基线。若继续改变退出机制，应另冻结新实验并同时报告保护亏损与截短大赢家的效果；原生TV逐笔对齐仍是未完成的外部校验。任何进入新holdout或实盘的动作须按项目规则另行确认。\n')
    REPORT.write_text('\n'.join(parts));print(REPORT)


if __name__=='__main__':main()
