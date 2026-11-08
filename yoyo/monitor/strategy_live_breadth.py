"""Booklet and backtest projection of the live-breadth signal's historical cases (owner 2026-11-08).

Owner 2026-11-08 chose "Design via Stitch first" for the live-breadth account (Stitch 2762455ffefe4f5a9784c2e2e0fd4db5);
every paper account on the page carries a strategy booklet and a backtest trade list. Source: the 13 native 5m cases
of exp-monday-breadth-six-day-excursions-20261108-v1 (50-99 Binance coins first touching the weekly lower 1.618 in one
bar, zero new lower touches for 30 minutes, buy at the decision 5m open, sell at the open six days later; VectorBT
fixed exit, taker 0.05 percent per fill, no funding or slippage). Only SOL is projected, as the funded leg of the live
account, compounding at 1x all-in from 1000 USDT. The cases never overlap, so one position at a time holds as booked.
No fills, fees or exits are recomputed here. The drop-matched controls are read from
exp-monday-breadth-drop-control-20261108-v1 (pre-registered) and -v2 (post hoc, 30-day trend matched).
"""
from __future__ import annotations

import csv
import gzip
import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / 'experiments/active'
SOURCE = A / 'exp-monday-breadth-six-day-excursions-20261108-v1'
CONTROLS = (A / 'exp-monday-breadth-drop-control-20261108-v1', A / 'exp-monday-breadth-drop-control-20261108-v2')
TRACK = 'live-breadth'
START_MS = 1672531200000  # 2023-01-01 UTC; the first case is 2023-04-26
RULE = ('同一根5分钟K线里有50–99个币安币首次碰到周一下轨1.618（实盘用OKX，换算为31–62个），之后30分钟没有新币碰下轨，'
        '按下一根5分钟开盘价买入SOL，6天后按开盘价平仓，不设止损；1倍，用全部已实现净值，一次只持有一笔。'
        'Taker 0.05%（单边），不含资金费和滑点。')
LIMITATION = ('50–99这一档是看过结果后从多个币数档里选出的，3年半只有13次。历史成交来自币安5分钟数据（VectorBT固定6天出场）；'
              '实盘账户读OKX，31–62是两边重叠一周的计数比约0.63换算的近似值。BTC/ETH在模拟账户里只作对照，不计入本曲线。'
              '模拟运行，不下真单，不代表未来收益。')


def ms(text):
    return int(datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()*1000)


def cases():
    path = SOURCE / 'events.csv.gz'
    return _cases(path.stat().st_size, path.stat().st_mtime_ns)


@lru_cache(maxsize=2)
def _cases(size, mtime):
    rows = []
    with gzip.open(SOURCE / 'events.csv.gz', 'rt') as handle:
        for r in csv.DictReader(handle):
            if r['minutes'] != '5' or r['asset'] != 'SOL' or r['new_coins'] != '0' or not 50 <= int(r['initial']) <= 99:
                continue
            rows.append(dict(known_ms=ms(r['known']), entry_ms=ms(r['decision']), exit_ms=ms(r['exit_time']), initial=int(r['initial']),
                             entry=float(r['entry_open']), exit=float(r['exit_open']), net_pct=float(r['net_pct']),
                             peak_pct=float(r['peak_pct'])))
    rows.sort(key=lambda r: r['entry_ms'])
    if len(rows) != 13 or any(b['entry_ms'] < a['exit_ms'] for a, b in zip(rows, rows[1:])):
        raise ValueError('live_breadth_cases_mismatch')
    return tuple(rows)


def capital():
    """Compounded 1x all-in SOL ledger: one starting row, then one row per settlement."""
    equity, out = 1000., [dict(time_ms=START_MS, equity=1000., pnl=0., cashflow=0., stake_budget=1000., notional=0.,
                                trade_id='', symbol='', side=0, net_bp=float('nan'), entry_ms=float('nan'), reason='')]
    for n, c in enumerate(cases(), 1):
        notional = equity
        pnl = notional*c['net_pct']/100
        equity += pnl
        out.append(dict(time_ms=c['exit_ms'], equity=equity, pnl=pnl, cashflow=0., stake_budget=0., notional=notional,
                        trade_id=f"SOL|{n}", symbol='SOLUSDT', side=1, net_bp=c['net_pct']*100, entry_ms=c['entry_ms'], reason='time_6d'))
    return out


def ledger():
    rows, ranking = [], {}
    for n, (c, r) in enumerate(zip(cases(), capital()[1:]), 1):
        row = dict(id=n, symbol='SOLUSDT', event=r['trade_id'], side=1, entry_ms=c['entry_ms'], exit_ms=c['exit_ms'],
                   entry_start_ms=c['entry_ms'], pnl=r['pnl'], return_pct=100*r['pnl']/r['notional'], exit_reason='time_6d',
                   entry_rules=dict(status='saved', kind='breadth', initial=c['initial'], new_coins=0, signal_close_ms=c['known_ms'],
                                    source_net_bp=c['net_pct']*100),
                   fills=dict(entry_price=c['entry'], exit_price=c['exit'], entry_start_ms=c['entry_ms'], entry_end_ms=c['entry_ms'],
                              exit_start_ms=c['exit_ms'], exit_end_ms=c['exit_ms'], execution_minutes=5, exit_kind='point'))
        if not (math.isfinite(row['pnl']) and math.isclose(row['return_pct'], c['net_pct'], abs_tol=1e-9)):
            raise ValueError('live_breadth_ledger_mismatch')
        rows.append(row)
        rank = ranking.setdefault('SOLUSDT', dict(symbol='SOLUSDT', pnl=0., trades=0))
        rank['pnl'] += row['pnl']; rank['trades'] += 1
    return dict(meta=dict(track=TRACK, start='2023-01-01', end=datetime.fromtimestamp(rows[-1]['exit_ms']/1000, timezone.utc).isoformat(),
                          note=RULE, limitation=LIMITATION, source='Binance', trades=len(rows), pnl=sum(r['pnl'] for r in rows),
                          source_commit=json.loads((CONTROLS[1] / 'summary.json').read_text())['source_commit'],
                          sources=[str(SOURCE.relative_to(ROOT))],
                          basis='宽度信号 · SOL 1倍全仓复利；百分比为净盈亏除以开仓名义金额，已扣双边手续费。'),
                rows=rows, ranking=list(ranking.values()))


def controls():
    """Drop-matched same-coin controls (v1 pre-registered, v2 adds the 30-day trend caliper), primary 13 cases."""
    rows = []
    for directory, label in zip(CONTROLS, ('跌幅匹配（预登记）', '跌幅+30天趋势匹配（事后）')):
        res = json.loads((directory / 'summary.json').read_text())['results']['primary']['main']
        for asset in ('SOL', 'BTC', 'ETH'):
            s = res[asset]
            lo, hi = s['excess_ci95_pp']
            rows.append([label, asset, f"{s['event_mean_pct']:+.2f}%", f"{s['control_mean_pct']:+.2f}%",
                         f"{s['excess_mean_pp']:+.2f}（{lo:+.2f} ~ {hi:+.2f}）"])
    return dict(note='每次信号配同一币种在±1年内、跌幅、反弹、30天趋势和7天波动相近、前24小时没有≥25币宽度暴跌的10个时点，'
                     '同样6天固定持有、同样费用。加上30天趋势匹配后，只有SOL的超额区间仍在0以上。',
                headers=['对照', '币种', '信号6天', '对照6天', '超额 · 百分点（95%区间）'], rows=rows)


def publish_booklet():
    """Write live-breadth-history.json and the research.json entry from the saved study; no fills or allocations."""
    import pandas as pd
    from yoyo.evaluation.strategy_booklet_metrics import project
    out = ROOT / 'yoyo/monitor/static/strategy-booklet'
    value = ledger()
    frame = pd.DataFrame(capital())
    meta = dict(track=TRACK, start='2023-01-01', end=value['meta']['end'], note=RULE + LIMITATION, sources=value['meta']['sources'],
                candidates=13, funded=13)
    history = project(frame, meta)
    history['source_commit'] = value['meta']['source_commit']
    (out / f'{TRACK}-history.json').write_text(json.dumps(history, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    case_rows = [[datetime.fromtimestamp(c['known_ms']/1000-300+8*3600, timezone.utc).strftime('%Y-%m-%d %H:%M'), c['initial'],
                  f"{c['net_pct']:+.2f}%", f"{c['peak_pct']:+.2f}%"] for c in cases()]
    research = json.loads((out / 'research.json').read_text())
    research['accounts'][TRACK] = dict(
        status='全市场宽度信号 · 模拟运行', note=RULE,
        detail=LIMITATION + f"2023年起1000U → {history['summary']['end']:,.0f}U，最大回撤{history['summary']['max_drawdown_pct']:.1f}%（按平仓点计）。",
        headers=['年份', '年度收益 %'], rows=history['yearly'],
        chart=dict(title='年度收益', unit='%', labels=[r[0] for r in history['yearly']], values=[r[1] for r in history['yearly']]),
        controls=controls(),
        sections=[dict(title='13次信号 · SOL', note='信号K为北京时间（5分钟K开盘时刻）；净收益已扣双边手续费，最高浮盈为6天内最高价相对买入价。',
                       headers=['信号K', '首次碰轨币数', '6天净收益', '最高浮盈'], rows=case_rows)],
        sources=[dict(path=str(SOURCE.relative_to(ROOT)) + '/events.csv.gz', commit=value['meta']['source_commit'],
                      selection='minutes=5, initial 50-99, new_coins=0, SOL; 13 settlements')]
        + [dict(path=str(d.relative_to(ROOT)) + '/summary.json', selection='drop-matched controls') for d in CONTROLS])
    (out / 'research.json').write_text(json.dumps(research, ensure_ascii=False, indent=2))
    return history['summary']


if __name__ == '__main__':
    print(json.dumps(publish_booklet(), ensure_ascii=False))
