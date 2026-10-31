"""Project Claude's saved pre-touch allin3 path without replaying trades.

Source: exp-pretouch-booklet-20261031-v1, A_rest_causal, seed 20261105.
Native geometry and causal scores are joined by event and exact fill bounds.
Amounts, cash additions and the terminal settlement remain the original facts.
"""
import csv
import gzip
import json
import math
from functools import lru_cache
from pathlib import Path

from .strategy_backtest_evidence import millis

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/active/exp-pretouch-booklet-20261031-v1'
NATIVE = ROOT / 'experiments/active/exp-monday-limit-paper-accounts-20261028-v1'
SCORES = ROOT / 'experiments/active/exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz'
ARM = 'allin3-A_rest_causal'
NOTE = ('触价前冻结评分≥0.6；单仓、全部净值×3倍，60分钟确认、30%兜底、+0.17%保护。'
        '1000 USDT起步，低于100后补至1000。Maker 2bp/Taker 5bp，非目标退出另扣10bp滑点，不含资金费。'
        '全程2023–2026交易净盈亏406801.41 USDT；2026年417032.22 USDT；另补本金20203.83 USDT。'
        '最后ONUSDT的127506.23 USDT来自数据末端结算。最大已实现回撤100%，21次补款。')
LIMITATION = ('live-标识触价前规则，当前仅模拟运行。历史Binance的双OHLC较差路径和MAE强平情景，'
              '与当前OKX逐分钟模拟、保证金封顶存在差异；固定同刻排序也不等于实时到达顺序。'
              '参数在历史上选择，未获实盘准入，不代表未来收益。')


def csv_rows(path):
    with gzip.open(path, 'rt') as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def _native(symbol):
    return [{r['trade_id']: r for r in csv_rows(NATIVE / p / 'symbols' / symbol / 'trades.csv.gz')}
            for p in ('ohlc', 'olhc')]


def ledger():
    paths = [SOURCE / 'manifest.json', SOURCE / f'{ARM}-ledger.csv.gz', SCORES]
    signature = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
    return _ledger(signature)


@lru_cache(maxsize=1)
def _ledger(signature):
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    meta = manifest['accounts'][ARM]
    scores = {r['event']: float(r['causal']) for r in csv_rows(SCORES)}
    rows, ranking = [], {}
    raw = list(enumerate(csv_rows(SOURCE / f'{ARM}-ledger.csv.gz')))
    # Group native reads by symbol; the final display order is selected by the API.
    for n, r in sorted(raw, key=lambda item: item[1]['symbol']):
        if float(r['notional']) <= 0:
            continue
        event, symbol = r['trade_id'], r['symbol']
        score = scores[event]
        if not math.isfinite(score) or score < .6:
            raise ValueError('pre_touch_score_mismatch')
        tid = event + ':m60:opposite_1618:c0:v_rest_v3'
        o, l = [p[tid] for p in _native(symbol)]
        net = lambda v: float(v['net_notional_bp']) - (0 if int(v['reason']) == 4 else 10)
        chosen = l if net(l) < net(o) else o
        entry_ms, exit_ms = int(float(r['entry_ms'])), int(r['time_ms'])
        if (millis(o['entry_time']) != entry_ms or millis(chosen['exit_time']) != exit_ms
                or int(o['side']) != int(float(r['side']))
                or not math.isclose(net(chosen), float(r['net_bp']), abs_tol=1e-6)):
            raise ValueError('pre_touch_native_lineage_mismatch')
        job = json.loads((SOURCE.parent / 'exp-monday-limit-reclaim-20261024-v1/symbols' / symbol / 'identity.json').read_text())['job']
        duration = int(job['execution_minutes']) * 60000
        reason = int(chosen['reason'])
        row = dict(id=n, symbol=symbol, event=event, side=int(o['side']), entry_ms=entry_ms, exit_ms=exit_ms,
                   entry_start_ms=entry_ms-duration, pnl=float(r['pnl']),
                   return_pct=100*float(r['pnl'])/float(r['notional']), exit_reason=reason,
                   entry_rules=dict(status='saved', score=score, minimum=.6, kind='first_touch',
                                    score_basis='pre_touch', variant='rest_v3', source_net_bp=net(chosen)),
                   fills=dict(entry_price=float(o['entry']), exit_price=float(chosen['exit_price']),
                              entry_start_ms=entry_ms-duration, entry_end_ms=entry_ms,
                              exit_start_ms=exit_ms if reason in (0, 11, 12, 14) else exit_ms-duration,
                              exit_end_ms=exit_ms, score_close_ms=entry_ms-duration,
                              execution_minutes=int(job['execution_minutes']),
                              exit_kind='close' if reason in (0, 11, 12, 14) else 'interval'))
        rows.append(row)
        rank = ranking.setdefault(symbol, dict(symbol=symbol, pnl=0., trades=0))
        rank['pnl'] += row['pnl']; rank['trades'] += 1
    if len(rows) != meta['funded'] or not math.isclose(sum(r['pnl'] for r in rows), meta['summary']['pnl'], abs_tol=1e-6):
        raise ValueError('pre_touch_ledger_incomplete')
    return dict(meta=dict(track='live-allin3', start=meta['start'], end=meta['end'], note=NOTE, limitation=LIMITATION,
                         source='Binance', trades=len(rows), pnl=meta['summary']['pnl'],
                         source_commit=manifest['source_commit'], sources=[str(SOURCE.relative_to(ROOT))],
                         basis='冻结触价前评分 · 原始资金账本；补款不计收益，期末结算单独标明。'),
                rows=rows, ranking=sorted(ranking.values(), key=lambda r: (-r['pnl'], r['symbol'])))


def publish_booklet():
    """Copy the existing charts and add source-backed booklet text; no fills or allocations."""
    value = ledger()  # Require all 422 source joins before publishing.
    out = ROOT / 'yoyo/monitor/static/strategy-booklet'
    history = json.loads((SOURCE / f'{ARM}-history.json').read_text())
    history['meta'].update(track='live-allin3', note=NOTE + LIMITATION)
    history['source_commit'] = value['meta']['source_commit']
    (out / 'live-allin3-history.json').write_text(json.dumps(history, ensure_ascii=False, separators=(',', ':')))
    research = json.loads((out / 'research.json').read_text())
    research['accounts']['live-allin3'] = dict(status='触价前评分 · 独立模拟策略', note=NOTE, detail=LIMITATION,
        headers=['年份', '交易净盈亏 USDT'], rows=history['yearly'],
        chart=dict(title='年度交易净盈亏 · 补款不计收益', unit='USDT',
                   labels=[r[0] for r in history['yearly']], values=[r[1] for r in history['yearly']]),
        sources=[dict(path=str(SOURCE.relative_to(ROOT)) + '/manifest.json',
                      commit=value['meta']['source_commit'], selection=ARM + '; 422 funded settlements')])
    (out / 'research.json').write_text(json.dumps(research, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    publish_booklet()
