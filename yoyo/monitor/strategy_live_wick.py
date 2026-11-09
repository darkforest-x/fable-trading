"""Project the saved live-wick backtest paths without replaying trades (owner 2026-11-01).

Sources: exp-monday-limit-close-survivor-paths-20261101-v10 (6 slots x 3), -v11 (3 slots x 3), -v12 (6x3, 1.5x from
score 0.76) and -v13 (3x3, close >= 1.5 percent past the level): v5 / v7 / v8 / v9 continued through the October 2026
extension (owner "应该融合进整体的回测数据中吧"), near Monday edge, 1000 USDT from 2020-01-01, one tie seed. Entries are the touch-close survivors of the stop grid's E20 fills
(NautilusTrader, worse of two intrabar paths); amounts and P&L are the saved ledger facts.
"""
import csv
import gzip
import json
import math
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / 'experiments/active'
CONTROL = A / 'exp-monday-limit-close-slice-control-20261101-v1'
SOURCES = {'live-wick6x3': (A / 'exp-monday-limit-close-survivor-paths-20261101-v10', 'near_monday_edge-2020', 6),
           'live-wick3x3': (A / 'exp-monday-limit-close-survivor-paths-20261101-v11', 'near_monday_edge-2020', 3),
           'live-wick6x3hi': (A / 'exp-monday-limit-close-survivor-paths-20261101-v12', 'near_monday_edge-2020', 6),
           'live-wick3x3far': (A / 'exp-monday-limit-close-survivor-paths-20261101-v13', 'near_monday_edge-2020', 3)}
MIDPOINT_SOURCE = A / 'exp-live-wick-shallow-midpoint-20261109-v1'
MIDPOINT_BASE = A / 'exp-monday-limit-close-survivor-paths-20261101-v10'
MIDPOINT_OLD_LEGS = A / 'exp-monday-limit-close-survivor-paths-20261101-v5'
MIDPOINT_TRACK = 'live-wick6x3mid'
MIDPOINT_EXPERIMENT_COMMIT = 'd203587331'
MIDPOINT_RULE = ('其余入场和持仓管理沿用 live-wick6x3：触价5分钟K线收盘评分≥0.5、收盘仍在1.618外，'
                 '收回幅度<64%、离1.618<30%时收盘市价进场；开仓价−20%止损，下一根1小时收盘未回到1.618以内则市价退出，'
                 '回到以内后止损移到1.618。止盈按触价收盘按方向计算的1.618越界幅度 beyond_pct 分档：严格满足0<beyond_pct<1.5%时，'
                 '以纽约时区周一区间中点止盈；其他合格交易仍以近侧周一边界止盈。6个槽位，保证金=已实现净值/6，3倍。'
                 'Taker 5bp并计10bp入场滑点，Maker 2bp止盈，其他退出Taker 5bp并计10bp滑点，不含资金费。')
BOOST = {'live-wick6x3hi': '评分≥0.76时保证金×1.5（观察账户：0.76由2025–26回测事后选出，2025年前没有这类单，未经检验）。',
         'live-wick3x3far': '只做收盘离1.618至少1.5%的单（观察账户：门槛由回测事后发现，2020–24与2025–26两段都成立，待前向检验）。'}
CLOSE_EXITS = (0, 11, 12, 14)
RULE = ('触价那根5分钟K线收盘时：评分≥0.5、收盘仍在1.618外且插出去的部分收回不到64%、离1.618不到30%，'
        '收盘市价进场。止盈周一近侧边界；止损开仓价−20%；触价后下一根1小时K线收盘仍未回到1.618以内则市价平，'
        '回到以内则止损移到1.618。')
# Booklet supplement (owner 2026-11-01 "这些数据整合一下补充到对应的策略小册 … 先不做倍投了"); October is in the paths.
DOUBLING = A / 'exp-monday-limit-live-martingale-20261101-v1'
# track -> (doubling account or None, doubling caps that fit the margin limit)
SUPPLEMENT = {'live-wick6x3': ('live-wick|6x3', (1, 2)), 'live-wick3x3': ('live-wick|3x3', (1,)),
              'live-wick6x3hi': (None, ()), 'live-wick3x3far': ('live-wick-far|3x3', (1,))}
LIMITATION = ('live-标识可在收盘后执行的规则，当前仅模拟运行，不下真单。历史成交来自Binance 5分钟数据的较差路径，'
              '与OKX逐分钟模拟存在差异；固定同刻排序不等于实时到达顺序。入场条件在2020–2024选出、2025–2026检验，'
              '止损和仓位是看过2025–2026后定的；不代表未来收益。2026-09-21之后的成交来自币安每日归档补数，同一规则同一框架；'
              '币安没有发布10月28日数据，10-26当周只看到10-27收盘，10-28至10-31的触价没有计入。')
MIDPOINT_LIMITATION = (LIMITATION + '浅穿越止盈切换由历史结果事后启发，10月原近侧路径已知后补跑了中点目标，不是前瞻验证。'
                       '历史1000U资金曲线仅作回看，未转入live-wick6x3mid独立前向1000U模拟账户。')


def csv_rows(path):
    with gzip.open(path, 'rt') as handle:
        return list(csv.DictReader(handle))


def note(slots, track=None):
    return (RULE + f'每笔保证金=已实现净值/{slots}，3倍，最多同时{slots}笔；' + BOOST.get(track, '') +
            'Taker 5bp+10bp滑点进场，Maker 2bp止盈，其他退出Taker 5bp+10bp；不含资金费。')


def ledger(track):
    if track == MIDPOINT_TRACK:
        files = [MIDPOINT_SOURCE / 'config.json', MIDPOINT_SOURCE / 'funded.csv.gz', MIDPOINT_SOURCE / 'paired.csv.gz',
                 MIDPOINT_SOURCE / 'october_midpoint_legs.csv.gz', MIDPOINT_BASE / 'manifest.json',
                 MIDPOINT_BASE / 'near_monday_edge-2020-ledger.csv.gz',
                 MIDPOINT_OLD_LEGS / 'range_0.5-2020-ledger.csv.gz']
        signature = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in files)
        return _midpoint_ledger(signature)
    directory, name, _ = SOURCES[track]
    paths = [directory / 'manifest.json', directory / f'{name}-ledger.csv.gz']
    return _ledger(track, tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in paths))


@lru_cache(maxsize=4)
def _ledger(track, signature):
    directory, name, slots = SOURCES[track]
    manifest = json.loads((directory / 'manifest.json').read_text())
    meta = manifest['accounts'][name]
    rows, ranking = [], {}
    for n, r in enumerate(csv_rows(directory / f'{name}-ledger.csv.gz')):
        if float(r['notional']) <= 0:
            continue
        reason = int(float(r['reason']))
        entry_ms, exit_ms = int(float(r['entry_ms'])), int(r['time_ms'])
        score = float(r['score'])
        if not (math.isfinite(score) and score >= .5 and float(r['recovery']) < .6395668107499273 and exit_ms >= entry_ms):
            raise ValueError('live_wick_ledger_mismatch')
        duration = 5 * 60000
        row = dict(id=n, symbol=r['symbol'], event=r['trade_id'], side=int(float(r['side'])), entry_ms=entry_ms, exit_ms=exit_ms,
                   entry_start_ms=entry_ms-duration, pnl=float(r['pnl']),
                   return_pct=100*float(r['pnl'])/float(r['notional']), exit_reason=reason,
                   entry_rules=dict(status='saved', score=score, minimum=.5, kind='touch_close', score_basis='touch_close',
                                    variant='E20', recovery=float(r['recovery']), beyond_pct=float(r['beyond_pct']),
                                    source_net_bp=float(r['net_bp'])),
                   fills=dict(entry_price=float(r['entry']), exit_price=float(r['exit_price']),
                              entry_start_ms=entry_ms-duration, entry_end_ms=entry_ms,
                              exit_start_ms=exit_ms if reason in CLOSE_EXITS else exit_ms-duration,
                              exit_end_ms=exit_ms, score_close_ms=entry_ms, execution_minutes=5,
                              exit_kind='close' if reason in CLOSE_EXITS else 'interval'))
        rows.append(row)
        rank = ranking.setdefault(row['symbol'], dict(symbol=row['symbol'], pnl=0., trades=0))
        rank['pnl'] += row['pnl']; rank['trades'] += 1
    if len(rows) != meta['funded'] or not math.isclose(sum(r['pnl'] for r in rows), meta['summary']['pnl'], abs_tol=1e-6):
        raise ValueError('live_wick_ledger_incomplete')
    return dict(meta=dict(track=track, start=meta['start'], end=meta['end'], note=note(slots, track), limitation=LIMITATION,
                         source='Binance', trades=len(rows), pnl=meta['summary']['pnl'],
                         source_commit=manifest['source_commit'], sources=[str(directory.relative_to(ROOT))],
                         basis='触价收盘市价进场 · 原始资金账本；百分比为净盈亏除以开仓名义金额，费用和滑点已扣。'),
                rows=rows, ranking=sorted(ranking.values(), key=lambda r: (-r['pnl'], r['symbol'])))


def _source_trade_map(path):
    result = {}
    for n, row in enumerate(csv_rows(path)):
        event = row.get('trade_id')
        if event and float(row.get('notional') or 0) > 0:
            if event in result:
                raise ValueError('duplicate_live_wick_source_event')
            result[event] = (n, row)
    return result


def _leg_exit_ms(row):
    if row.get('time_ms'):
        return int(float(row['time_ms']))
    from .strategy_backtest_evidence import millis
    return millis(row['exit_t'])


def midpoint_capital_rows():
    """Return the 400 saved funded settlements for the six-slot 2020-start account."""
    rows = [r for r in csv_rows(MIDPOINT_SOURCE / 'funded.csv.gz')
            if r['start'] == '2020-01-01' and r['policy'] == 'shallow_midpoint']
    if not rows or not any(float(r['notional'] or 0) > 0 for r in rows):
        raise ValueError('live_wick_midpoint_account_missing')
    return rows


@lru_cache(maxsize=1)
def _midpoint_ledger(signature):
    """Join the actual 400 funded outcomes to their saved original or midpoint native fills."""
    import pandas as pd

    base_path = MIDPOINT_BASE / 'near_monday_edge-2020-ledger.csv.gz'
    midpoint_path = MIDPOINT_OLD_LEGS / 'range_0.5-2020-ledger.csv.gz'
    near = _source_trade_map(base_path)
    mid = _source_trade_map(midpoint_path)
    paired = {r['event']: r for r in csv_rows(MIDPOINT_SOURCE / 'paired.csv.gz')}
    october = {r['event']: r for r in csv_rows(MIDPOINT_SOURCE / 'october_midpoint_legs.csv.gz')}
    if len(paired) != 416 or len(october) != 10:
        raise ValueError('live_wick_midpoint_opportunity_set_incomplete')

    account_rows = midpoint_capital_rows()
    trades = [r for r in account_rows if float(r['notional'] or 0) > 0]
    if len(trades) != 400 or len({r['trade_id'] for r in trades}) != 400:
        raise ValueError('live_wick_midpoint_funded_count_mismatch')
    if set(r['trade_id'] for r in trades) != set(near):
        raise ValueError('live_wick_midpoint_funded_events_mismatch')

    rows, ranking = [], {}
    midpoint_count = near_count = 0
    for row in trades:
        event = row['trade_id']
        if event not in paired or event not in near:
            raise ValueError('live_wick_midpoint_event_missing')
        source_id, original = near[event]
        beyond = float(original['beyond_pct'])
        far = paired[event]['far'].lower() == 'true'
        if not (math.isfinite(beyond) and 0 < beyond < 30 and far == (beyond >= 1.5)):
            raise ValueError('live_wick_midpoint_band_mismatch')
        use_midpoint = beyond < 1.5
        if use_midpoint:
            leg = october.get(event) or (mid[event][1] if event in mid else None)
            target = 'range_0.5'
            midpoint_count += 1
        else:
            leg = original
            target = 'near_monday_edge'
            near_count += 1
        if leg is None:
            raise ValueError('live_wick_midpoint_native_leg_missing')

        exit_ms = int(float(row['time_ms']))
        entry_ms = int(float(row['entry_ms']))
        reason = int(float(row['reason']))
        net_bp = float(row['net_bp'])
        leg_net = float(leg.get('net_bp') or leg.get('net'))
        if (int(float(leg['reason'])) != reason or _leg_exit_ms(leg) != exit_ms
                or not math.isclose(leg_net, net_bp, abs_tol=1e-6, rel_tol=1e-10)
                or int(float(original['entry_ms'])) != entry_ms
                or not math.isclose(float(leg['entry']), float(original['entry']), abs_tol=1e-10, rel_tol=1e-10)):
            raise ValueError('live_wick_midpoint_native_lineage_mismatch')
        score, recovery = float(original['score']), float(original['recovery'])
        if (not math.isfinite(score) or score < .5 or not math.isfinite(recovery)
                or recovery >= .6395668107499273 or exit_ms < entry_ms):
            raise ValueError('live_wick_midpoint_entry_rule_mismatch')
        notional, pnl = float(row['notional']), float(row['pnl'])
        if notional <= 0 or not math.isfinite(pnl) or not math.isclose(pnl, notional * net_bp / 10000,
                                                                         abs_tol=1e-6, rel_tol=1e-10):
            raise ValueError('live_wick_midpoint_account_pnl_mismatch')
        duration = 5 * 60000
        symbol = original['symbol']
        trade = dict(id=source_id, symbol=symbol, event=event, side=int(float(original['side'])),
                     entry_ms=entry_ms, exit_ms=exit_ms, entry_start_ms=entry_ms-duration, pnl=pnl,
                     return_pct=100*pnl/notional, exit_reason=reason, target=target,
                     entry_rules=dict(status='saved', score=score, minimum=.5, kind='touch_close',
                                      score_basis='touch_close', variant='E20', recovery=recovery,
                                      beyond_pct=beyond,
                                      target_rule=('shallow_ny_monday_midpoint' if use_midpoint else 'near_monday_edge'),
                                      source_net_bp=net_bp),
                     fills=dict(entry_price=float(original['entry']), exit_price=float(leg['exit_price']),
                                entry_start_ms=entry_ms-duration, entry_end_ms=entry_ms,
                                exit_start_ms=exit_ms if reason in CLOSE_EXITS else exit_ms-duration,
                                exit_end_ms=exit_ms, score_close_ms=entry_ms, execution_minutes=5,
                                exit_kind='close' if reason in CLOSE_EXITS else 'interval'))
        rows.append(trade)
        symbol_rank = ranking.setdefault(symbol, dict(symbol=symbol, pnl=0., trades=0))
        symbol_rank['pnl'] += pnl
        symbol_rank['trades'] += 1

    if midpoint_count != 200 or near_count != 200 or len(rows) != 400:
        raise ValueError('live_wick_midpoint_target_counts_mismatch')

    frame = pd.DataFrame(account_rows)
    for col in ('time_ms', 'equity', 'pnl', 'cashflow', 'stake_budget', 'notional', 'side', 'entry_ms'):
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    error = float((frame.equity - 1000 - frame.pnl.cumsum() - frame.cashflow.cumsum()).abs().max())
    if not frame.time_ms.is_monotonic_increasing or error > 1e-6:
        raise ValueError('live_wick_midpoint_capital_path_mismatch')
    summary = json.loads((MIDPOINT_SOURCE / 'summary.json').read_text())
    account = summary['accounts']['2020-01-01']['shallow_midpoint']
    if (not math.isclose(float(frame.equity.iloc[-1]), 1000*account['final_multiple'], abs_tol=1e-6)
            or not math.isclose(sum(r['pnl'] for r in rows), float(frame.pnl.sum()), abs_tol=1e-6)):
        raise ValueError('live_wick_midpoint_capital_summary_mismatch')
    return dict(meta=dict(track=MIDPOINT_TRACK, start='2020-01-01',
                          end=str(pd.to_datetime(int(frame.time_ms.iloc[-1]), unit='ms', utc=True)),
                          note=MIDPOINT_RULE, limitation=MIDPOINT_LIMITATION, source='Binance',
                          trades=len(rows), candidates=416, skipped=16, midpoint_trades=midpoint_count,
                          near_target_trades=near_count, pnl=sum(r['pnl'] for r in rows),
                          source_commit=MIDPOINT_EXPERIMENT_COMMIT,
                          sources=[str(MIDPOINT_SOURCE.relative_to(ROOT)) + '/funded.csv.gz',
                                   str(MIDPOINT_SOURCE.relative_to(ROOT)) + '/paired.csv.gz',
                                   str(MIDPOINT_BASE.relative_to(ROOT)) + '/near_monday_edge-2020-ledger.csv.gz',
                                   str(MIDPOINT_OLD_LEGS.relative_to(ROOT)) + '/range_0.5-2020-ledger.csv.gz',
                                   str(MIDPOINT_SOURCE.relative_to(ROOT)) + '/october_midpoint_legs.csv.gz'],
                          basis='六槽、3倍、2020起步的历史资金账本；400笔真实 funded 成交，416候选中16笔未获资金分配。历史收益不注入独立前向模拟账户。'),
                rows=rows, ranking=sorted(ranking.values(), key=lambda r: (-r['pnl'], r['symbol'])))


def midpoint_history():
    """Project the saved 1000U historical account for the strategy booklet, never for paper runtime."""
    import pandas as pd
    from yoyo.evaluation.strategy_booklet_metrics import project

    frame = pd.DataFrame(midpoint_capital_rows())
    seed = dict(time_ms=1577836800000, equity=1000.0, pnl=0.0, cashflow=0.0,
                stake_budget=1000.0, notional=0.0, side='', entry_ms='',
                symbol='', trade_id='', net_bp='', reason='', start='2020-01-01', policy='shallow_midpoint')
    frame = pd.concat([pd.DataFrame([seed]), frame], ignore_index=True)
    for col in ('time_ms', 'equity', 'pnl', 'cashflow', 'stake_budget', 'notional', 'side', 'entry_ms'):
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    summary = json.loads((MIDPOINT_SOURCE / 'summary.json').read_text())
    source_manifest = json.loads((MIDPOINT_BASE / 'manifest.json').read_text())
    source_account = summary['accounts']['2020-01-01']['shallow_midpoint']
    meta = dict(target='浅穿越中点 / 其他近侧周一边界', start='2020-01-01',
                end=str(pd.to_datetime(int(frame.time_ms.iloc[-1]), unit='ms', utc=True)),
                seed=source_manifest['seed'], candidates=416, funded=400,
                allocator=dict(kind='frac', slots=6, leverage=3),
                allocator_result=dict(final_multiple=source_account['final_multiple'],
                                      max_drawdown_pct=source_account['max_drawdown_pct'], liquidations=0,
                                      skipped=416-400, taken=400, win_pct=source_account['win_pct']),
                reconciliation_error=float((frame.equity - 1000 - frame.pnl.cumsum() - frame.cashflow.cumsum()).abs().max()),
                note=MIDPOINT_RULE, track=MIDPOINT_TRACK)
    history = project(frame, meta)
    history['source_commit'] = MIDPOINT_EXPERIMENT_COMMIT
    if (history['summary']['trades'] != 400 or not math.isclose(history['summary']['end'],
                                                                 1000*source_account['final_multiple'], abs_tol=1e-6)):
        raise ValueError('live_wick_midpoint_history_projection_mismatch')
    return history


def controls():
    """Matched random-time control of the same events (near edge), as saved; per trade, after costs."""
    res = json.loads((CONTROL / 'summary.json').read_text())['results']['periods']
    rows = []
    for per, label in (('y2020_2024', '2020–24（选出期）'), ('y2025', '2025'), ('y2026', '2026'), ('test', '2025–26')):
        p = res[f'near_monday_edge|{per}']
        s, r, d = p['signal'], p['random'], p['paired']
        rows.append([label, f"{s['mean_bp']:+.0f}bp · {s['n']}笔", f"{r['mean_bp']:+.0f}bp · {r['n']}笔",
                     f"{d['mean_bp']:+.0f}bp（{round(d['ci95'][0])+0:+d} ~ {round(d['ci95'][1])+0:+d}）"])
    return dict(note='同币、同周、同方向、同波动桶的随机5分钟收盘市价进场，搬到虚拟1.618外同样距离，出场规则相同（原−30%止损口径）。'
                     '只和插针之后的随机进场比，2025–26每笔仍高+139bp；2025单年不显著。',
                headers=['时期', '信号每笔', '随机每笔', '配对差（95%区间）'], rows=rows)


def _multiple(v):
    return f"{v['final_median']:.2f}倍 · 回撤{v['dd_max']:.0f}%"


def _streak(flags):
    best = cur = 0
    for f in flags:
        cur = cur+1 if f else 0
        best = max(best, cur)
    return best


def sections(track):
    """Losing streaks of the account's own settlements (entry order) and the loss-doubling study, which is not adopted."""
    value = ledger(track)
    rows_ = sorted(value['rows'], key=lambda r: (r['entry_ms'], r['event']))
    since = [r for r in rows_ if r['entry_ms'] >= 1735689600000]  # 2025-01-01 UTC
    rows = [['最长连续亏损（2020起 / 2025起）', f"{_streak(r['pnl'] < 0 for r in rows_)}笔 / {_streak(r['pnl'] < 0 for r in since)}笔"],
            ['最长连续−20%止损', f"{_streak(r['exit_reason'] == 1 for r in rows_)}笔"]]
    account, caps = SUPPLEMENT[track]
    if account:
        d = json.loads((DOUBLING / 'summary.json').read_text())['results']['accounts'][account]
        for c in (0,)+caps:
            key = 'none' if c == 0 else f'x2_L{c}'
            label = '不倍投' if c == 0 else f'亏后翻倍，最多{c}次'
            rows.append([f'{label} · 2025起', _multiple(d['from2025']['variants'][key])])
            rows.append([f'{label} · 2020–24', _multiple(d['y2020_2024']['variants'][key])])
    note = ('连续亏损按本账户实际成交、按开仓顺序数，含补进的10月。倍投指亏一笔后下一笔保证金按实际仓位翻倍、赢一笔复原，'
            '测算期到2026-09（不含10月）：收益更高，但回撤和单笔亏损一起放大，且没有随机翻倍对照；把10月单独从1000U算，翻倍反而更差。'
            + ('' if account else '本账户按评分加仓，未单独测倍投。') + 'Owner 2026-11-01决定先不做倍投，模拟盘维持原仓位。')
    return [dict(title='连续亏损与倍投 · 已研究，暂不采用', note=note, headers=['项目', '结果'], rows=rows)]


def midpoint_section():
    """Show the exact registered comparison windows, with their actual counts and no cross-run median framing."""
    summary = json.loads((MIDPOINT_SOURCE / 'summary.json').read_text())
    accounts = summary['accounts']
    rows = []
    for start, label, period_end in (('2020-01-01', '2020-01-01 至 2026-10-27 UTC', '全期'),
                                     ('2025-01-01', '2025-01-01 至 2026-10-27 UTC', '近年')):
        near = accounts[start]['near']
        midpoint = accounts[start]['shallow_midpoint']
        a, b = near['funded'], midpoint['funded']
        rows.append([f'{label} · {a["trades"]}笔',
                     f'近侧 {near["final_multiple"]:.2f}倍 / 回撤{near["max_drawdown_pct"]:.1f}% / 胜率{a["win_pct"]:.1f}%',
                     f'浅穿越中点 {midpoint["final_multiple"]:.2f}倍 / 回撤{midpoint["max_drawdown_pct"]:.1f}% / 胜率{b["win_pct"]:.1f}%'])
    october = accounts['2026-10-01']
    near, midpoint = october['near'], october['shallow_midpoint']
    rows.append([f'2026-10-01 至 2026-10-27 UTC · {midpoint["taken"]}笔，10笔切换目标',
                 f'近侧 {near["funded"]["wins"]}/{near["taken"]}胜 · {near["funded"]["mean_net_bp"]:+.0f}bp/笔',
                 f'浅穿越中点 {midpoint["funded"]["wins"]}/{midpoint["taken"]}胜 · {midpoint["funded"]["mean_net_bp"]:+.0f}bp/笔'])
    return dict(title='事后目标对照 · 尚未前瞻验证',
                note='同一触价事件与六槽分配口径，只有小于1.5%的浅穿越目标切到区间中点。初始资本各自1000U；区间和目标档位见实验登记。结果由已知历史驱动，不代表未来表现。',
                headers=['区间 / 样本', '近侧周一边界', '浅穿越中点'], rows=rows)


def publish_booklet():
    """Write the booklet curves and research text from the saved paths; no fills or allocations."""
    out = ROOT / 'yoyo/monitor/static/strategy-booklet'
    research = json.loads((out / 'research.json').read_text())
    for track, (directory, name, slots) in SOURCES.items():
        value = ledger(track)
        history = json.loads((directory / f'{name}-history.json').read_text())
        history['meta'].update(track=track, note=note(slots, track) + LIMITATION)
        history['source_commit'] = value['meta']['source_commit']
        (out / f'{track}-history.json').write_text(json.dumps(history, ensure_ascii=False, separators=(',', ':')))
        m = json.loads((directory / 'manifest.json').read_text())['accounts']
        recent = m[name.replace('2020', '2025')]['summary']
        research['accounts'][track] = dict(
            status='触价收盘进场 · 模拟运行', note=note(slots, track),
            detail=LIMITATION + f"2025年起1000U → {recent['end']:,.0f}U，最大回撤{recent['max_drawdown_pct']:.1f}%。",
            headers=['年份', '年度收益 %'], rows=history['yearly'],
            chart=dict(title='年度收益', unit='%', labels=[r[0] for r in history['yearly']], values=[r[1] for r in history['yearly']]),
            controls=controls(), sections=sections(track),
            sources=[dict(path=str(directory.relative_to(ROOT)) + '/manifest.json', commit=value['meta']['source_commit'],
                          selection=f'{name}; {value["meta"]["trades"]} funded settlements')]
            + [dict(path=str(DOUBLING.relative_to(ROOT)) + '/summary.json', selection='loss doubling, not adopted')])
    value = ledger(MIDPOINT_TRACK)
    history = midpoint_history()
    history['meta']['note'] = MIDPOINT_RULE
    out.joinpath(f'{MIDPOINT_TRACK}-history.json').write_text(
        json.dumps(history, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    research['accounts'][MIDPOINT_TRACK] = dict(
        status='浅穿越目标切换 · 独立模拟策略', note=MIDPOINT_RULE,
        detail=(f"历史账本区间 2020-01-01 至 2026-10-27 UTC：416个候选机会中实际开仓{value['meta']['trades']}笔，"
                f"中点目标{value['meta']['midpoint_trades']}笔、近侧目标{value['meta']['near_target_trades']}笔、"
                f"16个机会未获资金分配。历史1000U期末{history['summary']['end']:,.2f}U，"
                f"最大已实现回撤{history['summary']['max_drawdown_pct']:.1f}%；历史收益不注入独立前向1000U模拟账户。"),
        headers=['年份', '年度收益 %'], rows=history['yearly'],
        chart=dict(title='年度收益', unit='%', labels=[r[0] for r in history['yearly']],
                   values=[r[1] for r in history['yearly']]),
        sections=[midpoint_section()],
        sources=[dict(path=str(MIDPOINT_SOURCE.relative_to(ROOT)) + '/summary.json',
                      commit=MIDPOINT_EXPERIMENT_COMMIT, selection='完整区间与分段对照；400笔 funded account'),
                 dict(path=str(MIDPOINT_SOURCE.relative_to(ROOT)) + '/funded.csv.gz',
                      commit=MIDPOINT_EXPERIMENT_COMMIT, selection='2020起六槽历史资金账本，400笔实际成交')])
    (out / 'research.json').write_text(json.dumps(research, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    publish_booklet()
