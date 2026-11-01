"""Project the saved live-wick backtest paths without replaying trades (owner 2026-11-01).

Sources: exp-monday-limit-close-survivor-paths-20261101-v5 (6 slots x 3) and -v7 (3 slots x 3), near Monday edge,
1000 USDT from 2020-01-01, one tie seed. Entries are the touch-close survivors of the stop grid's E20 fills
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
SOURCES = {'live-wick6x3': (A / 'exp-monday-limit-close-survivor-paths-20261101-v5', 'near_monday_edge-2020', 6),
           'live-wick3x3': (A / 'exp-monday-limit-close-survivor-paths-20261101-v7', 'near_monday_edge-2020', 3),
           'live-wick6x3hi': (A / 'exp-monday-limit-close-survivor-paths-20261101-v8', 'near_monday_edge-2020', 6)}
BOOST = {'live-wick6x3hi': '评分≥0.76时保证金×1.5（观察账户：0.76由2025–26回测事后选出，2025年前没有这类单，未经检验）。'}
CLOSE_EXITS = (0, 11, 12, 14)
RULE = ('触价那根5分钟K线收盘时：评分≥0.5、收盘仍在1.618外且插出去的部分收回不到64%、离1.618不到30%，'
        '收盘市价进场。止盈周一近侧边界；止损开仓价−20%；触价后下一根1小时K线收盘仍未回到1.618以内则市价平，'
        '回到以内则止损移到1.618。')
LIMITATION = ('live-标识可在收盘后执行的规则，当前仅模拟运行，不下真单。历史成交来自Binance 5分钟数据的较差路径，'
              '与OKX逐分钟模拟存在差异；固定同刻排序不等于实时到达顺序。入场条件在2020–2024选出、2025–2026检验，'
              '止损和仓位是看过2025–2026后定的；不代表未来收益。')


def csv_rows(path):
    with gzip.open(path, 'rt') as handle:
        return list(csv.DictReader(handle))


def note(slots, track=None):
    return (RULE + f'每笔保证金=已实现净值/{slots}，3倍，最多同时{slots}笔；' + BOOST.get(track, '') +
            'Taker 5bp+10bp滑点进场，Maker 2bp止盈，其他退出Taker 5bp+10bp；不含资金费。')


def ledger(track):
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
            controls=controls(),
            sources=[dict(path=str(directory.relative_to(ROOT)) + '/manifest.json', commit=value['meta']['source_commit'],
                          selection=f'{name}; {value["meta"]["trades"]} funded settlements')])
    (out / 'research.json').write_text(json.dumps(research, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    publish_booklet()
