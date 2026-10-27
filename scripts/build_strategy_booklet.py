"""Project saved research summaries into the requested strategy booklet; never replay trades.

Sources are the registered October 25–27 studies. Only the unaffected linear
rows of the profit-lock comparison are eligible. Historical BE0 and Binance
inputs must remain explicitly distinct from current OKX paper execution.
"""
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'yoyo/monitor/static/strategy-booklet'
PERIODS = ['y2020_2024', 'y2025', 'y2026', 'last5m']
LABELS = ['2020–2024 · 训练期', '2025', '2026 · 至9月', '近5个月 · 与2026重叠']


def source(name):
    path = f'experiments/active/exp-monday-limit-{name}/summary.json'
    data = json.loads((ROOT / path).read_text())
    return data['results'], {'path': path, 'commit': data.get('source_commit'), 'selection': ''}


def table(rows, fields):
    return [[label, *[rows[p].get(f) for f in fields]] for p, label in zip(PERIODS, LABELS)]


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    lock, lock_src = source('be-lock-compare-20261027-v1')
    add, add_src = source('be-scalein-compare-20261026-v1')
    exits, exits_src = source('reclaim-allexits-20261025-v1')
    spike, spike_src = source('spike-hw-20261026-v1')
    allin, allin_src = source('langlang-20261027-v1')
    accounts = {}
    headers = ['区间', '样本数', '胜率 %', '平均净收益 bp']
    for track, variant in [('v3', 'v3_be17'), ('v3lock', 'v3_be50')]:
        rows = lock['per_trade'][variant]
        sized = lock['sizing'][variant+'|linear|L1']
        accounts[track] = dict(
            status='规则对应的历史研究',
            note='保护偏移与本账户一致；历史为 Binance 5m，当前为 OKX 前向模拟。线性分配、总名义敞口1倍。区间分别从1000 USDT开始，同刻顺序10次取中位数；末列区间与2026重叠，不能相加。',
            headers=headers, rows=table(rows, ['n', 'win_pct', 'mean_bp']),
            capital_headers=['区间', '期末本金倍数', '最大回撤 %', '资金成交笔数'],
            capital_rows=table(sized, ['final_multiple', 'max_drawdown_pct', 'taken']),
            chart=dict(title='分区间期末本金倍数', unit='倍', labels=LABELS, values=[sized[p]['final_multiple'] for p in PERIODS]),
            sources=[{**lock_src, 'selection': f'per_trade.{variant}; sizing.{variant}|linear|L1'}],
        )
    accounts['60'] = dict(
        status='历史研究 · 仓位口径不同',
        note='本表为60分钟、+0.17%保护偏移的独立交易结果。原研究资金回放使用评分线性仓位，当前60m账户使用每笔10%净值，因此不展示其资金倍数作为当前账户收益。',
        headers=headers, rows=table(add['offsets']['v2_be17'], ['n', 'win_pct', 'mean_bp']),
        chart=dict(title='独立交易平均净收益', unit='bp', labels=LABELS, values=[add['offsets']['v2_be17'][p]['mean_bp'] for p in PERIODS]),
        sources=[{**add_src, 'selection': 'offsets.v2_be17'}],
    )
    five = exits['variants']['m5|near']
    keys = ['before_2025|top40', 'from_2025|top40', 'last5m|top40']
    labels = ['2020–2024 · 训练期', '2025起 · 至2026年9月', '近5个月 · 重叠子区间']
    accounts['5'] = dict(
        status='历史基线 · 保护偏移不同',
        note='5分钟确认、近侧边界目标、冻结评分前40%（门槛0.22621664）。历史确认后止损在开仓价，当前为盈利方向+0.17%。近5个月为2026-04-23至09-23（UTC），属于前一区间子集。',
        headers=headers, rows=[[label, *[five[p][f] for f in ['n','win_pct','mean_bp']]] for p,label in zip(keys,labels)],
        chart=dict(title='独立交易平均净收益 · 历史BE0', unit='bp', labels=labels, values=[five[p]['mean_bp'] for p in keys]),
        sources=[{**exits_src, 'selection': 'variants.m5|near; top40 only'}],
    )
    addition = add['replays']['v3_be0_plus_add5']
    accounts['v3add'] = dict(
        status='历史加仓研究 · 保护偏移不同',
        note='高分主单逆向5%等额加仓。历史主单使用开仓价保本（BE0），当前为主单入场价盈利方向+0.17%。原研究按评分线性仓位、总名义敞口1倍，10次同刻顺序取中位数。样本数包含主单与已成交加仓，不等于资金成交数。',
        headers=['区间', '回放样本数', '期末本金倍数', '最大回撤 %'], rows=table(addition, ['n','final_median','dd_median']),
        chart=dict(title='历史BE0加仓 · 分区间本金倍数', unit='倍', labels=LABELS, values=[addition[p]['final_median'] for p in PERIODS]),
        sources=[{**add_src, 'selection': 'replays.v3_be0_plus_add5'}],
    )
    sized = {p: spike['equity']['规则：分10份×5倍|'+p] for p in PERIODS}
    accounts['spike'] = dict(
        status='插针高胜率小册 · 历史BE0',
        note='219笔、181个币种。历史与当前均为评分≥0.6、排列分≥3、10份×5倍；历史用Binance行情与开仓价保本，当前排列分用OKX 15m、保护偏移+0.17%。历史另设19%浮亏强平情景，当前模拟按保证金封顶，两者不能视为完全相同的账户。',
        headers=headers, rows=table(spike['periods'], ['n','win_pct','mean_bp']),
        capital_headers=['区间', '期末本金倍数', '最大回撤 %', '资金成交笔数'], capital_rows=table(sized, ['final_multiple','max_drawdown_pct','taken']),
        chart=dict(title='独立交易平均净收益', unit='bp', labels=LABELS, values=[spike['periods'][p]['mean_bp'] for p in PERIODS]),
        images=[dict(file='spike-equity.png',caption='历史已实现资金曲线 · 1000 USDT起步 · 对数轴。蓝线：排列分≥3；灰线：排列分≥1。两条都使用历史BE0。'),dict(file='spike-distribution.png',caption='历史年度表现与单笔收益分布 · 原小册图。')],
        sources=[{**spike_src, 'selection': 'periods; equity.规则：分10份×5倍; 1_equity.png; 2_years_and_returns.png'}],
    )
    grid = allin['grid']['v3_ge06_opp1618|allin|L3']
    accounts['allin3'] = dict(
        status='梭哈3倍 · 历史起点研究',
        note='842个候选交易，44个月度起点×5次同刻顺序。只展示已保存的到达目标耗时统计；这是历史样本描述，不是未来达到目标的概率。历史opp1618_P30为开仓价保本，当前为+0.17%；历史使用MAE强平阈值，当前按单笔保证金封顶。',
        headers=['累计本金目标', '历史到达比例 %', '到达耗时中位数 / 天', '此前重启次数中位数'],
        rows=[[f'{g}倍', *[grid['x'+str(g)][k] for k in ['reached_pct','days_median','blowups_before_median']]] for g in [10,100]],
        chart=dict(title='各月起点达到10倍累计本金的耗时', unit='天', labels=list(grid['by_start']), values=[r['d10'] for r in grid['by_start'].values()]),
        detail='耗时从该起点之后第一笔可用交易开始计；目标分母计入每次新增1000 USDT的本金份数。各起点使用重叠行情，不是44份独立验证；图中耗时不是资金曲线。',
        sources=[{**allin_src, 'selection': 'grid.v3_ge06_opp1618|allin|L3'}],
    )
    # More plots are projections of saved aggregates, not new trading replays.
    reason_names = {'target':'目标止盈', 'breakeven':'保护止损', 'failed_reclaim':'未回收', 'reclaimed_inside_offset':'未越过保护价', 'backstop':'兜底止损', '0':'期末结算'}
    for track, variant in [('60','v2_be17'), ('v3','v3_be17'), ('v3lock','v3_be50')]:
        stats = add['offsets'][variant] if track == '60' else lock['per_trade'][variant]
        reasons = stats['last5m']['reasons']
        accounts[track]['charts'] = [
            dict(title='分区间胜率', unit='%', labels=LABELS, values=[stats[p]['win_pct'] for p in PERIODS]),
            dict(title='近5个月退出原因占比', unit='%', labels=[reason_names.get(k,k) for k in reasons], values=list(reasons.values())),
        ]
        if track != '60':
            capital = lock['sizing'][variant+'|linear|L1']
            accounts[track]['charts'].append(dict(title='分区间最大已实现回撤', unit='%', labels=LABELS, values=[capital[p]['max_drawdown_pct'] for p in PERIODS]))
    accounts['5']['charts'] = [dict(title='分区间胜率 · 历史BE0',unit='%',labels=labels,values=[five[p]['win_pct'] for p in keys]),dict(title='近5个月多空平均净收益',unit='bp',labels=['做多','做空'],values=[five['last5m|top40']['long_mean_bp'],five['last5m|top40']['short_mean_bp']])]
    accounts['v3add']['charts'] = [dict(title='历史BE0加仓 · 最大回撤',unit='%',labels=LABELS,values=[addition[p]['dd_median'] for p in PERIODS]),dict(title='高分主单的加仓成交率',unit='%',labels=LABELS,values=[add['scale_in']['add5'][p]['child_fill_pct'] for p in PERIODS])]
    accounts['allin3']['charts'] = [dict(title='各月起点达到100倍累计本金的耗时',unit='天',labels=list(grid['by_start']),values=[r['d100'] for r in grid['by_start'].values()]),dict(title='10倍目标 · 历史到达比例',unit='%',labels=['90天内','180天内','365天内','数据结束前'],values=[grid['x10'][k] for k in ['within_90d_pct','within_180d_pct','within_365d_pct','reached_pct']]),dict(title='100倍目标 · 历史到达比例',unit='%',labels=['90天内','180天内','365天内','数据结束前'],values=[grid['x100'][k] for k in ['within_90d_pct','within_180d_pct','within_365d_pct','reached_pct']])]
    equity_ref = dict(file='v2-equity.png', caption='历史资金曲线 · v2早期BE0版本 · 每笔10%、总敞口≤1倍。蓝线60分钟、橙线5分钟；图内“现行”指原研究2026-10-25当时版本，当前保护偏移已为+0.17%。')
    for track in ['60','5']:
        accounts[track]['images'] = [equity_ref]
        accounts[track]['sources'].append({**exits_src,'selection':'2_last5m_equity.png; earlier BE0 account paths'})
    # Keep an earlier curve explicitly secondary, never relabel its parameters.
    for track in ['v3','v3add','v3lock']:
        accounts[track]['references'] = [dict(file='v3-early-equity.png',caption='早期v3研发曲线（参考）：20%兜底、BE0，门槛0.226/0.5对照。当前v3/v3lock为30%兜底，v3add另有加仓；该图不代表这些当前账户的历史资金表现。')]
        accounts[track]['sources'].append(dict(path='experiments/active/exp-monday-limit-v3-compare-20261025-v1/1_v3_equity_2026.png',commit=None,selection='Original earlier-version comparison chart; not current account equity'))
    accounts['spike']['images'] += [dict(file='spike-floating-loss.png',caption='确认前最大浮亏分布 · 赢单与非赢单对比。'),dict(file='spike-frequency.png',caption='月度、单日与累计开单频率 · 满仓跳过前的219笔候选成交。')]
    accounts['spike']['sources'][0]['selection'] += '; 3_floating_loss.png; 4_frequency.png'
    accounts['spike']['charts'] = [dict(title='分区间胜率 · 历史BE0',unit='%',labels=LABELS,values=[spike['periods'][p]['win_pct'] for p in PERIODS]),dict(title='分区间最大已实现回撤',unit='%',labels=LABELS,values=[sized[p]['max_drawdown_pct'] for p in PERIODS])]
    data = dict(version=1, reviewed_on='2026-10-27', accounts=accounts,
        methodology='成交来自既有NautilusTrader回测，逐笔取O-H-L-C与O-L-H-C中较差路径。Maker单边2bp、Taker单边5bp，非目标退出另计10bp滑点；未纳入资金费率。1bp=0.01%。胜率按各原研究口径，不能跨表直接比较。评分训练截止2025年前；规则与参数也在这些历史数据上研究过，2025以后不等于完全独立的样本外验证。',
        integrity='2026-10-27发现旧slots回放重复计算初始资金。该类分仓资金倍数与排行不展示；本页只选不受影响的linear、spike小册slot_replay和allin path_run结果。曲线缺失时保留缺失状态，区间汇总只画柱状图。',
        comparison='本页未提供同币×同时间×同波动桶的匹配随机对照，不能据此断言策略具备超额收益。排列分≥1仅是规则对照。',
        period_note='2026与近5个月均截至原数据末端2026年9月；近5个月与2026重叠。各独立本金回放区间从1000 USDT重启，不可拼接成连续资金曲线。')
    (OUT/'research.json').write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    src = ROOT/'experiments/active/exp-monday-limit-spike-hw-20261026-v1'
    for old,new in [('1_equity.png','spike-equity.png'),('2_years_and_returns.png','spike-distribution.png'),('3_floating_loss.png','spike-floating-loss.png'),('4_frequency.png','spike-frequency.png')]:
        shutil.copyfile(src/old, OUT/new)
    for folder,name,new in [('reclaim-allexits-20261025-v1','2_last5m_equity.png','v2-equity.png'),('v3-compare-20261025-v1','1_v3_equity_2026.png','v3-early-equity.png')]:
        shutil.copyfile(ROOT/('experiments/active/exp-monday-limit-'+folder)/name,OUT/new)


if __name__ == '__main__':
    build()
