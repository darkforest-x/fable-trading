"""Render the frozen triple-exit study's results; no parameter fitting here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v1_triple_study import EXP, ROOT, atomic_json

MAIN = ['baseline', 'triple', 'filtered_triple']
ARMS = {'baseline': 'V1默认', 'adverse65': '仅MAE0.65', 'be05': '仅MFE0.5保本', 'lock50': '仅锁50%MFE',
        'triple': '三规则组合', 'filters_only': '仅入场过滤', 'filtered_triple': '过滤+三规则'}
LABELS = {'cohort': '数据池', 'event_scope': '事件口径', 'asset_scope': '标的口径', 'period': '时间分区',
          'timeframe_min': '周期分钟', 'venue': '交易所', 'symbol': '标的', 'arm': '规则',
          'trades': '已平仓笔数', 'events': '事件数', 'censored': '未完成', 'win_rate': '净胜率', 'pf_r': 'R利润因子',
          'sum_net_r': '净R合计', 'mean_net_r': '平均净R', 'median_net_r': '净R中位数',
          'closed_event_cumulative_r_maxdd': '已平仓累计R回撤', 'top5_positive_net_r': '前5笔盈利R',
          'sum_without_top5_net_r': '去前5笔净R', 'realized_ge_10r': '兑现10R笔数', 'cost_r': '费用R',
          'mean_matched_excess_net_r': '相对随机平均超额R', 'excess_ci95_low': '超额CI下界', 'excess_ci95_high': '超额CI上界',
          'excess_one_sided_p': '超额单侧p', 'excess_one_sided_p_bonferroni9': '校正p', 'cross_cut_excluded': '跨切点排除'}
TABLE_FIELDS = ['trades', 'win_rate', 'sum_net_r', 'top5_positive_net_r', 'sum_without_top5_net_r',
                'mean_matched_excess_net_r', 'excess_ci95_low', 'excess_ci95_high', 'closed_event_cumulative_r_maxdd']


def human(frame):
    frame = frame.copy()
    if 'arm' in frame:
        frame['arm'] = frame.arm.map(ARMS).fillna(frame.arm)
    return frame.rename(columns=LABELS)


def table(frame, ids):
    cols = [x for x in ids + TABLE_FIELDS if x in frame]
    f = frame[cols].copy()
    if 'win_rate' in f:
        f['win_rate'] = f.win_rate.map(lambda x: f'{x:.2%}' if pd.notna(x) else '不适用')
    for c in f.select_dtypes(include='number'):
        if c not in ('trades', 'timeframe_min'):
            f[c] = f[c].map(lambda x: f'{x:.3f}' if pd.notna(x) else '不适用')
    return human(f).fillna('不适用').to_markdown(index=False) if len(f) else '当前分组无可报告样本。'


def read_outcomes(cohort):
    paths = sorted((EXP / 'results' / cohort / 'streams').glob('*/outcomes.csv.gz'))
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


def wide_trades(cohort):
    events = pd.read_csv(EXP / 'results' / cohort / 'events.csv.gz')
    result = read_outcomes(cohort)
    projection = EXP / 'results/stats' / f'{cohort}_identity_dedup.csv'
    if projection.exists():
        identity = pd.read_csv(projection).set_index('event_id')
        events['dedup_keep'] = events.event_id.map(identity.dedup_keep)
    metric = result.pivot(index='event_id', columns='arm', values='net_r')
    primary = result.loc[result.arm.eq('baseline')].set_index('event_id')
    combo = result.loc[result.arm.eq('triple')].set_index('event_id')
    f = events.set_index('event_id')
    columns = ['venue', 'symbol', 'timeframe_min', 'entry_time', 'volume_ratio', 'dedup_keep']
    out = f[columns].join(primary[['entry_price', 'initial_stop', 'risk_fraction_at_entry', 'exit_time', 'mfe_r']], rsuffix='_out')
    out = out.join(combo[['exit_time', 'exit_protection_source']], rsuffix='_triple').join(metric)
    out['delta_triple_r'] = out['triple'] - out['baseline']
    return out.reset_index()


def sheet_spec(name, title, frame, subtitle='', columns=None):
    if columns is not None:
        frame = frame[[c for c in columns if c in frame]].copy()
    spec = dict(name=name, title=title, subtitle=subtitle, headers=list(human(frame).columns),
                rows=json.loads(human(frame).to_json(orient='values')), formats={}, widths={}, rColumns=[])
    for i, col in enumerate(frame.columns):
        if col in ('trades', 'events', 'censored', 'timeframe_min', 'realized_ge_10r', 'cross_cut_excluded'):
            spec['formats'][i] = '#,##0'
        elif col in ('win_rate', 'risk_fraction_at_entry'):
            spec['formats'][i] = '0.00%'
        elif pd.api.types.is_numeric_dtype(frame[col]):
            spec['formats'][i] = '#,##0.00;[Red]-#,##0.00'
        if 'net_r' in col or col in ('baseline', 'triple', 'filtered_triple', 'delta_triple_r'):
            spec['rColumns'].append(i)
        if col in ('arm', 'period', 'cohort', 'symbol'):
            spec['widths'][i] = 23
    return spec


def build():
    summary_path = EXP / 'results/stats/summary.csv'
    summary = pd.read_csv(summary_path)
    receipts = []
    for cohort in ['original6253', 'top20']:
        paths = sorted((EXP / 'results' / cohort / 'streams').glob('*/receipt.json'))
        items = [json.loads(p.read_text()) for p in paths]
        if any(x.get('parity_errors') for x in items):
            raise ValueError('source parity failures must be resolved or explicitly quarantined before reporting')
        receipts.append(dict(cohort=cohort, streams=len(items), events=sum(x['seen_events'] for x in items)))
    original = summary.loc[summary.cohort.eq('original6253') & summary.event_scope.eq('raw') & summary.asset_scope.eq('all')]
    top = summary.loc[summary.cohort.eq('top20') & summary.event_scope.eq('dedup') & summary.asset_scope.eq('all')]
    original_main = original.loc[original.group_type.eq('primary') & original.arm.isin(MAIN)]
    top_main = top.loc[top.group_type.eq('primary') & top.arm.isin(MAIN)]
    ex = summary.loc[summary.group_type.eq('primary') & summary.asset_scope.eq('ex_rave') & summary.arm.eq('triple')]
    timeframe = summary.loc[summary.group_type.eq('timeframe') & summary.asset_scope.eq('all') & summary.arm.isin(MAIN)]
    period = summary.loc[summary.group_type.eq('period') & summary.event_scope.eq('dedup') & summary.asset_scope.eq('all') & summary.arm.isin(MAIN)]
    inference = summary.loc[summary.cohort.eq('top20') & summary.group_type.eq('primary_timeframe') & summary.asset_scope.eq('all')]
    inference_columns = ['period','timeframe_min','arm','trades','mean_net_r','mean_net_r_ci95_low','mean_net_r_ci95_high',
                         'mean_matched_excess_net_r','excess_ci95_low','excess_ci95_high','excess_one_sided_p','excess_one_sided_p_bonferroni9']
    # Preserve the full result table as CSV; the report uses the preregistered primary views.
    original_rows = {r['arm']: r for r in original_main.to_dict('records')}
    baseline = original_rows.get('baseline', {})
    triple = original_rows.get('triple', {})
    delta = triple.get('sum_net_r', np.nan) - baseline.get('sum_net_r', np.nan)
    report = [
        '# SPIKE V1 三规则组合退出：逐根回放',
        '',
        f'原6253笔队列的已平仓净R：默认 **{baseline.get("sum_net_r", np.nan):,.2f}R**，三规则 **{triple.get("sum_net_r", np.nan):,.2f}R**，差额 **{delta:+,.2f}R**。这与用户提供的近似改善+4326R分开核对，不能把单规则改善相加。',
        '',
        '## 1. 原始队列核账',
        '本表保留原始事件，包括同币跨交易所、跨周期重复。未结束持仓不混入已兑现净R。它回答近似XLSX在原分母上能否成立。',
        table(original_main, ['arm']),
        '',
        '### 去RAVE敏感性', table(ex, ['cohort', 'event_scope', 'arm']),
        '',
        '## 2. 固定前20流动性三年检验',
        '先按2023年8月真实USDT成交额选池，再观察2023年9月至2026年8月。不会按今天涨幅排行倒选历史。主表按底层标的×UTC入场日只保留最早可执行一笔，过滤后不补选当天其他信号。',
        table(top_main, ['arm']),
        '',
        '### 分周期', table(timeframe.loc[timeframe.event_scope.eq('dedup')], ['cohort', 'timeframe_min', 'arm']),
        '',
        '## 3. 按时间向前验证',
        '开发期入场早于2025-09-01且在切点前结束；测试期从2025-09-01开始。测试区间已被既往研究接触，因此是冻结配置的时序复验，不是新的盲测。2026-05-04之后单列。各月不调阈值。',
        table(period, ['cohort', 'period', 'arm']),
        '',
        '### 测试期随机对照与置信区间',
        human(inference[[c for c in inference_columns if c in inference]]).to_markdown(index=False, floatfmt='.4f') if len(inference) else '无可计算的测试期匹配样本。',
        '',
        '## 4. 精确成交规则',
        '- 入场沿用原始V1信号，下一根开盘成交。原版只做多；本轮不补空头，也不修改原始信号引擎。',
        '- 原生止损和移动保护保留信号收盘参考；新增0.65R、0.5R、2R门槛用实际入价减初始止损的风险单位。',
        '- 每根先检查上一根已生效的保护；只有未止损的完整K线才更新峰值/逆向波动，下一根生效。跳空穿越保护价用实际开盘成交。',
        '- MAE达到0.65R后，提高下一根保护至entry−0.65R；不能回溯成交为−0.65R。同根直接跌穿原始SL仍执行原SL或更差跳空价。',
        '- MFE达到0.5R后，下一根保护到entry；MFE严格大于2R后锁累计MFE的一半。保护只收紧，不固定止盈。',
        '- 过滤量比>20、实际入场风险宽度>30%、USDC、PAXG和明确美股关联标的。PAXG为黄金支持标的。股票类型先由交易所元数据确认，再与美国上市目录核对，避免误删同名加密币。',
        '',
        '## 5. 对照、费用与集中度',
        '随机对照匹配同标的、同UTC入场日、同周期和固定ATR/price分桶，最多20次不同随机入场。控制交易使用自己的因果初始止损和相同退出/成本；先求每笔信号的随机均值，再计算超额。4H可匹配数量有限，日线同日没有其他入场时刻，不能伪造对照。',
        '按symbol-day聚类进行5000次bootstrap，给出平均净R与随机超额95%CI；主要测试为前20测试期3臂×3周期，单侧p另做9次比较校正。前五贡献是盈利最大的5笔交易，不是5个币种。',
        '所有净收益固定扣0.2%往返名义成本。移到入场价仍会产生费用亏损，窄风险分母会放大成本R。累计R回撤来自已平仓事件，未包含持仓浮亏与保证金约束，不能当作账户最大回撤。',
        '',
        '## 6. 风险与诚实声明',
        '- 原6253队列来源为当时可用交易所目录，存在历史幸存者/覆盖偏差；独立前20池修复了用今天热门标的选历史的问题，但不是动态全市场策略。',
        '- 持仓期限结束或数据终止时未触发保护的交易标为censored；缺失K线切断序列，不填充。缺少可靠tick的入选币保留覆盖缺口，不以其它币替补。',
        '- 历史价格步长使用可追溯静态快照/公告，未重建所有逐次tick变更。缺少真实资金费率、成交滑点、流动性与账户并发约束。',
        '- 参数源于已观察过的交易结果；0.65/2.20不是已证明的普适相变。这里只验证已固定规则，不重新寻找最优阈值。',
        '- symbol-day聚类仍可能低估不同币在同一市场冲击中的共同波动；小样本和尾部集中时，显著性应谨慎解释。',
        '- val AUC不适用：本轮不是概率模型训练。top-decile排序没有因果事前评分，不能按事后利润排行冒充预测分组。对应零假设检验为匹配随机入场超额与聚类置换。',
        '- 用户提供的XLSX近似公式工作簿尚待逐格来源核对，+4326R仅作为待验证参考，不能声称是数学上界。',
        '',
        '## 7. 复现与来源',
        '```bash',
        'git checkout main',
        '.venv/bin/python -m yoyo.evaluation.spike_v1_triple_data --help',
        '.venv/bin/python -m yoyo.evaluation.spike_v1_triple_study original6253',
        '.venv/bin/python -m yoyo.evaluation.spike_v1_triple_study top20',
        '# 统计命令以stats/README.md中已冻结CLI为准',
        '.venv/bin/python -m yoyo.evaluation.spike_v1_triple_report',
        '.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v1_triple_exit_20260914.md --out-dir analysis/html',
        '```',
        '- 实验与固定规则：`experiments/active/exp-spike-v1-triple-exit-20260914-v1/PROJECT_PLAN.md`。',
        '- 原始账本SHA：`b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578`。',
        '- [Binance公开数据及校验说明](https://github.com/binance/binance-public-data)。',
        '- [Nasdaq美国上市证券目录定义](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)。当前目录只辅助身份，不冒充完整历史目录。',
        '- [Notion研究记录](https://app.notion.com/p/3db8856479af8166b1a4f0c1abfaed41)。',
        '',
        '本配置本轮首次使用既有已暴露历史。工程核账重启属于同一冻结配置的故障修复，逐次日志保留；未修改线上策略、通知或仓位。',
    ]
    path = ROOT / 'analysis/p1_spike_v1_triple_exit_20260914.md'
    path.write_text('\n\n'.join(report) + '\n')
    fields = ['cohort', 'event_scope', 'asset_scope', 'period', 'arm'] + TABLE_FIELDS
    primary = summary.loc[summary.group_type.eq('primary') & summary.arm.isin(MAIN)]
    specs = [sheet_spec('总体对比', 'SPIKE V1 三规则逐根回放', primary, '净R已扣0.2%往返成本；已平仓累计R回撤不是账户回撤', fields),
             sheet_spec('时间分区', '开发期与时序测试期', period, '固定阈值；历史已暴露，不能称为新盲测', fields),
             sheet_spec('分周期', '15m 1H 4H与原队列周期', timeframe, '旧队列和独立前20池分开比较', ['cohort','event_scope','timeframe_min','arm']+TABLE_FIELDS),
             sheet_spec('单规则消融', '单规则与组合的差别', summary.loc[summary.group_type.eq('primary')], '不能把单规则改善值相加', fields)]
    for group, name, title in [('month', '逐月', '固定规则逐月表现'), ('symbol', '标的明细', '逐标的已平仓结果')]:
        part = summary.loc[summary.group_type.eq(group) & summary.event_scope.eq('dedup') & summary.asset_scope.eq('all') & summary.arm.isin(MAIN)]
        specs.append(sheet_spec(name, title, part, '去重后的事件；不依据此榜单反选历史', ['cohort','period','symbol','timeframe_min','arm']+TABLE_FIELDS))
    specs.append(sheet_spec('随机对照','测试期超额与置信区间', inference, '前20测试期3规则×3周期；校正p涵盖9次主要比较', inference_columns))
    for cohort, title in [('original6253','原6253逐笔'), ('top20','前20逐笔')]:
        frame = wide_trades(cohort)
        cols = ['venue','symbol','timeframe_min','entry_time','entry_price','initial_stop','volume_ratio','risk_fraction_at_entry',
                'baseline','adverse65','be05','lock50','triple','filtered_triple','delta_triple_r','exit_time','exit_time_triple','mfe_r','exit_protection_source','dedup_keep','event_id']
        spec = sheet_spec(title, title, frame, 'R基于实际入价和固定初始风险；过滤未通过为空白', cols)
        spec['headers'] = ['交易所','标的','周期分钟','入场UTC','入价','初始SL','量比','风险宽度','默认净R','仅MAE净R','仅保本净R',
                           '仅锁利净R','三规则净R','过滤组合净R','三规则改善R','默认退出UTC','组合退出UTC','原版峰值R','组合退出来源','去重保留','事件ID']
        spec['widths'].update({3:25,4:17,5:17,15:25,16:25,18:24,20:30})
        spec['formats'].update({4:'0.########',5:'0.########'})
        spec['dateColumns']=[3,15,16]
        spec['formulas'] = {14:[f'=M{i+5}-I{i+5}' for i in range(len(frame))]}
        specs.append(spec)
    notes = pd.DataFrame([
        ('成本','0.2%往返名义成本；资金费率与真实滑点未建模'),
        ('R','实际下一根开盘入价与初始SL距离；全过程分母不变'),
        ('MAE0.65','只观察已完成且未止损的bar，保护下一根生效，跳空按真实开盘价'),
        ('MFE0.5','下一根移到入价，价格保本不等于扣费保本'),
        ('MFE>2','下一根锁累计峰值的50%，与原生保护取更紧值'),
        ('原队列','2024-09-10至2026-09-10；6253个原始事件，不是前三年固定20标的池'),
        ('独立前20','2023-08真实USDT成交额预选，2023-09至2026-08原生15m/1h/4h'),
        ('前5贡献','按正净R最大的5笔；不是按币种'),
        ('回撤','已平仓事件累计R回撤，不是含浮亏、资金费与保证金的账户最大回撤'),
        ('历史暴露','既往研究已看过2026-05-04后数据，本轮不能声称新盲测'),
        ('随机对照','同标的/UTC日/周期/因果波动桶；最多20次，先求每个信号的控制均值'),
        ('来源','https://github.com/binance/binance-public-data'),
        ('股票身份','https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs'),
        ('完整研究','https://app.notion.com/p/3db8856479af8166b1a4f0c1abfaed41'),
    ], columns=['项目','口径'])
    spec = sheet_spec('口径与来源','规则与数据口径', notes)
    spec['widths']={0:22,1:110};spec['filter']=False;spec['bodyWrap']=True
    specs.append(spec)
    atomic_json(EXP / 'results/workbook_payload.json', dict(sheets=specs))
    atomic_json(EXP / 'results/report_receipt.json', dict(source_stats=str(summary_path), stream_checks=receipts,
                                                       report=str(path), delta_original_triple_r=delta))
    print(path)


if __name__ == '__main__':
    build()
