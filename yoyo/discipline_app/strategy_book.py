"""Read-only strategy booklets and saved backtest trades for the iPhone app (2.7.0).

Owner 2026-11-09: every paper account in the app needs the same frame as the web workbench —
overview, rules, historical equity curve (策略小册) and the saved backtest trades with a K-line
replay (历史开单); ``breadth-ema20`` (全市场下轨反弹) has a research booklet only.

Sources are the monitor's own published files and routes on the fixed loopback origin, never
re-computed here:
- ``/static/strategy-booklet/research.json`` and ``<track>-history.json`` (reclaim-strategy-book.js),
- ``/static/strategy-booklet/breadth-ema20-data.json`` (breadth-booklet.js / breadth-book-history.js),
- ``/api/strategy-backtest`` and ``/api/strategy-backtest/candles`` (strategy_backtest.py),
- the paper snapshot's rule config (``raw[track]``, ``raw['model']``) for the overview facts.

Rules text is copied from the web booklet (``overviewHTML`` / ``flowHTML`` / breadth ``rulesHTML``) so
the two surfaces say the same thing; it is display text, not a rule source. Projections are
whitelisted: no source paths, commits, model features or internal identifiers cross over.
Historical results are Binance backtests (with the look-ahead noted in each booklet), not live
P&L and not an order signal.
"""
import math
import re
from datetime import datetime, timezone

MAX_POINTS = 480
COIN = re.compile(r'[A-Z0-9]{1,32}USDT')
WICK = ('live-wick6x3', 'live-wick6x3mid', 'live-wick3x3', 'live-wick6x3hi', 'live-wick3x3far')
V3 = ('v3', 'v3add', 'v3lock', 'v3add20')
EXIT_REASONS = frozenset(('target', 'breakeven', 'backstop', 'reclaimed_inside_be', 'failed_reclaim', 'manual', 'time_6d',
                          'stop', 'settlement', 'end', 'liquidation', 'timeout', 'time'))


class BookUnavailable(ValueError):
    """The monitor cannot supply an honest booklet or backtest page."""


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _ms(value):
    value = _num(value)
    return int(value) if value is not None and value > 0 else None


def _side(value):
    return 'long' if not isinstance(value, bool) and value == 1 else 'short' if not isinstance(value, bool) and value == -1 else None


def _text(value, limit=400):
    return value[:limit] if isinstance(value, str) else None


def _cell(value):
    if isinstance(value, str):
        return value[:120]
    return _num(value)


def _table(headers, rows, limit=40):
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None
    head = [h[:40] for h in headers if isinstance(h, str)][:10]
    body = [[_cell(c) for c in r[:len(head)]] for r in rows[:limit] if isinstance(r, list)]
    return dict(headers=head, rows=body) if head else None


def _fact(label, value, unit=''):
    return dict(label=label, value=value, unit=unit)


def _pct(value, digits=2):
    value = _num(value)
    return None if value is None else f'{value:.{digits}f}'.rstrip('0').rstrip('.')


# ---- overview facts and rule steps (reclaim-strategy-book.js overviewHTML / flowHTML) -------------------------

def facts(track, raw):
    config = raw.get(track) if isinstance(raw.get(track), dict) else {}
    if _num(config.get('hold_days')) is not None:
        lo, hi = _num(config.get('count_min')), _num(config.get('count_max'))
        return [_fact('信号', f'{lo:.0f}–{hi:.0f}' if lo is not None and hi is not None else '—', '个币 / 5分钟'),
                _fact('观察', f"{_num(config.get('observe_minutes')) or 0:.0f}", '分钟零新增'),
                _fact('持有', f"{_num(config.get('hold_days')) or 0:.0f}", '天')]
    tracks = raw.get('tracks') if isinstance(raw.get('tracks'), dict) else {}
    current = tracks.get(track) if isinstance(tracks.get(track), dict) else None
    if current is None:
        return []
    wick, lock, v3 = track in WICK, track == 'v3lock', track in V3
    own = wick or track in ('allin3', 'live-allin3', 'spike')
    v3config = raw.get('v3') if isinstance(raw.get('v3'), dict) else {}
    model = raw.get('model') if isinstance(raw.get('model'), dict) else {}
    threshold = config.get('min_score') if own else v3config.get('min_score') if v3 else model.get('threshold')
    if wick:
        offset = 0 if config else None
    elif lock:
        offset = (raw.get('v3lock') or {}).get('breakeven_offset_bp') if isinstance(raw.get('v3lock'), dict) else None
    else:
        offset = raw.get('breakeven_offset_bp')
    offset = _num(offset)
    protection = '—' if offset is None else f"{'+' if offset > 0 else ''}{_pct(offset/100)}%"
    minutes = _num(current.get('confirm_minutes'))
    threshold = _num(threshold)
    return [_fact('确认周期', '—' if minutes is None else f'{minutes:.0f}', '分钟'),
            _fact('入场分门槛', '—' if threshold is None else f'≥ {threshold:.4g}'),
            _fact('利润锁' if lock else '保护偏移', protection)]


def flow(track):
    if track == 'live-breadth':
        return [dict(title='数币', text='每根 UTC 5 分钟 K 线统计首次碰到周一下轨 1.618 的币数；31–62 个 OKX 币（近似币安 50–99）记为信号。'),
                dict(title='等 30 分钟', text='之后 30 分钟没有新币碰下轨才确认；有新币就取消。'),
                dict(title='买入', text='按 OKX 1 分钟开盘价买 SOL，1 倍全仓；BTC、ETH 同额记为对照，不计入净值。'),
                dict(title='第 6 天平仓', text='不设止损，6 天后按 1 分钟开盘价平仓；持有期间的新信号跳过。')]
    wick = track in WICK
    entry = ('冻结入场评分仅使用触价前已收盘信息；当前模拟运行。' if track == 'live-allin3' else
             '触价5m收盘时评分并检查收回比例，收盘后市价入场，不回记；当前模拟运行。' if wick else
             '原回测评分包含触价5m收盘信息，收盘后回记触价入场。')
    if track == 'live-wick6x3mid':
        entry += '向外穿过 1.618 不足 1.5% 时，整仓止盈取纽约周一高低点的中点；达到 1.5% 时，多单取周一低点，空单取周一高点。'
    confirm = ('触价所在1小时K线之后，下一根1小时K线收盘，以 1.618 判断是否收回。未收回则退出；目标可在确认前成交。' if wick else
               '触价所在K线之后，下一根确认周期K线收盘。未收回则退出；目标可在确认前成交。')
    protection = '收回后止损移到 1.618，不加偏移。' if wick else '收回且越过保护价才移动止损；收回但未越过时按确认收盘退出。多单加偏移，空单减偏移。'
    return [dict(title='划区间', text='纽约周一高低点 H / L，宽度 W=H−L。上沿 H+0.618W，下沿 L−0.618W。'),
            dict(title='等触价', text=f'每周每侧首次触及；下沿做多、上沿做空。{entry}分数不是胜率。'),
            dict(title='等回收', text=confirm),
            dict(title='保护余仓', text=protection)]


# ---- historical equity path (<track>-history.json, validateHistory) ---------------------------------------------

def downsample(points, limit=MAX_POINTS):
    """Keep first/last and each bucket's high and low equity so drawdowns and peaks survive."""
    if len(points) <= limit:
        return points
    buckets = max(1, (limit - 2) // 2)
    size = (len(points) - 2) / buckets
    keep = {0, len(points) - 1}
    for b in range(buckets):
        lo, hi = 1 + int(b * size), min(len(points) - 1, 1 + int((b + 1) * size))
        if lo >= hi:
            continue
        chunk = range(lo, hi)
        keep.add(min(chunk, key=lambda i: points[i][1]))
        keep.add(max(chunk, key=lambda i: points[i][1]))
    return [points[i] for i in sorted(keep)]


def project_history(value, track):
    if not isinstance(value, dict) or value.get('schema') != 1 or (value.get('meta') or {}).get('track') != track:
        raise BookUnavailable('历史曲线的数据或策略不匹配')
    raw = value.get('points')
    if not isinstance(raw, list) or len(raw) < 2:
        raise BookUnavailable('历史曲线点数不足')
    points = []
    for p in raw:
        if not isinstance(p, list) or len(p) != 5 or any(_num(x) is None for x in p):
            raise BookUnavailable('历史曲线数据不完整')
        points.append([int(p[0]), p[1], p[3], p[4]])
    s = value.get('summary') or {}
    meta = value.get('meta') or {}
    refilled = bool(value.get('refilled'))
    basis = meta.get('basis')
    marked = basis in ('marked', 'hourly_marked')
    end = _num(s.get('end')) if _num(s.get('end')) is not None else points[-1][1]
    start = _num(s.get('start')) if _num(s.get('start')) is not None else points[0][1]
    drawdown = _num(s.get('max_drawdown_pct'))
    if drawdown is None and marked:
        drawdown = abs(min((p[2] for p in points), default=0.0))
    pair = lambda rows: [[r[0][:7], _num(r[1])] for r in rows if isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str)]
    sides = _table(['方向', '成交笔数', '胜率 %', '净盈亏 USDT'], value.get('sides') or [], 4)
    curve_limit = MAX_POINTS - 1 if basis == 'hourly_marked' else MAX_POINTS
    sampled = downsample(points, curve_limit)
    if basis == 'hourly_marked':
        worst = min(points, key=lambda p: p[2])
        if not any(p[0] == worst[0] for p in sampled):
            sampled = sorted(sampled + [worst], key=lambda p: p[0])
    return dict(start_ms=points[0][0], end_ms=points[-1][0], note=_text(meta.get('note')), refilled=refilled,
                unit='USDT' if refilled else '%',
                summary=dict(start=start, end=end, pnl=_num(s.get('pnl')) if _num(s.get('pnl')) is not None else end-start,
                             return_pct=_num(s.get('return_pct')) if _num(s.get('return_pct')) is not None else (end/start-1)*100 if start else None,
                             max_drawdown_pct=drawdown,
                             win_pct=_num(s.get('win_pct')), trades=_num(s.get('trades')),
                             profit_factor=None if s.get('profit_factor_state') == 'no_losses' else _num(s.get('profit_factor')),
                             no_losses=s.get('profit_factor_state') == 'no_losses', cash_added=_num(s.get('cash_added')),
                             avg_win=_num(s.get('avg_win')), avg_loss=_num(s.get('avg_loss'))),
                drawdown_basis='hourly_marked' if basis == 'hourly_marked' else 'marked' if marked else 'realized',
                points=[[p[0], round(p[1], 4), round(p[2], 3)] + ([round(p[3], 2)] if refilled else []) for p in sampled],
                yearly=pair(value.get('yearly') or []), monthly=pair(value.get('monthly') or [])[-120:], sides=sides)


def _cloud_record_trade(record):
    """Project one frozen cloud-v17 manifest row into the native strategy-frame trade shape."""
    if not isinstance(record, dict):
        return None
    symbol = record.get('symbol')
    side = _side(record.get('side'))
    trade_id = record.get('id')
    if (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{1,32}', symbol) or not side or
            not isinstance(trade_id, str) or not re.fullmatch(r'cloud-v17-[0-9]{6}', trade_id)):
        return None
    entry, exit_price = _num(record.get('entryPrice')), _num(record.get('exitPrice'))
    if entry is None or exit_price is None:
        return None
    return dict(id=int(trade_id[-6:]), symbol=symbol + 'USDT', coin=symbol, side=side,
                entry_ms=_ms(record.get('entryMs')), exit_ms=_ms(record.get('exitMs')),
                pnl=_num(record.get('netPnl')), return_pct=_num(record.get('netReturnPct')),
                exit_reason=_text(record.get('exitReason'), 40), exit_reason_label=_text(record.get('exitReasonLabel'), 80),
                score=None, count=None, new_coins=None, kind='cloud-v17', entry_price=entry, exit_price=exit_price,
                entry_start_ms=_ms(record.get('entryMs')), entry_end_ms=_ms(record.get('entryMs')),
                exit_start_ms=_ms(record.get('exitMs')), exit_end_ms=_ms(record.get('exitMs')))


def project_cloud_backtest(value, track, sort='time', direction='desc', offset=0, limit=50, overview=None):
    """Stable, paged projection of the 1,252 frozen cloud-v17 account trades."""
    if (track != 'live-cloud-v17' or not isinstance(value, dict) or value.get('schemaVersion') != 1 or
            value.get('strategyId') != 'cloud-v17-history' or not isinstance(value.get('records'), list)):
        raise BookUnavailable('云带历史账本版本不匹配')
    records = value['records']
    total = _ms(value.get('tradeCount')) or 0
    if total != len(records) or not total:
        raise BookUnavailable('云带历史账本笔数不完整')
    rows = [trade for trade in (_cloud_record_trade(r) for r in records) if trade is not None]
    if len(rows) != total or len({r['id'] for r in rows}) != total:
        raise BookUnavailable('云带历史账本交易行不完整')
    key = (lambda r: (r['entry_ms'] or 0, r['id'])) if sort == 'time' else (lambda r: (r['pnl'] or 0, r['id']))
    rows.sort(key=key, reverse=direction == 'desc')
    account = value.get('account') if isinstance(value.get('account'), dict) else {}
    source = value.get('source') if isinstance(value.get('source'), dict) else {}
    chart = value.get('chartWindow') if isinstance(value.get('chartWindow'), dict) else {}
    quality = value.get('dataQuality') if isinstance(value.get('dataQuality'), dict) else {}
    first, last = min(rows, key=lambda r: r['entry_ms'] or 0), max(rows, key=lambda r: r['entry_ms'] or 0)
    start = first['entry_ms']
    end = last['entry_ms']
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d') if ms else None
    overview_metrics = overview.get('metrics') if isinstance(overview, dict) and isinstance(overview.get('metrics'), dict) else {}
    bound = _num(overview_metrics.get('dd_bound_pct'))
    limitation = f"历史回撤估值上界 {bound:.2f}%；12H 原始 K 线缺失时保持空档、不补值；{_ms(quality.get('tradesWithMissing12hBars')) or 0} 笔存在缺失 12H K 线。" if bound is not None else f"12H 原始 K 线缺失时保持空档、不补值；{_ms(quality.get('tradesWithMissing12hBars')) or 0} 笔存在缺失 12H K 线。"
    return dict(track=track, total=total, offset=offset, has_more=offset + limit < total, sort=sort, direction=direction,
                meta=dict(trades=total, pnl=_num(account.get('netPnl')), start=fmt(start), end=fmt(end), source='OKX',
                          note='历史资金单：1,252 笔',
                          basis='每笔按冻结成交回执扣费；无额外手续费或滑点；未计资金费。',
                          limitation=limitation),
                rows=rows[offset:offset+limit])


def _cloud_reference(price, title, color):
    price = _num(price)
    return dict(price=price, title=title, color=color) if price is not None else None


def project_cloud_candles(event, symbol_data, manifest_record, track, trade):
    """Project the event's exact 12H source bars; missing rows are deliberately never synthesized."""
    if track != 'live-cloud-v17' or not isinstance(event, dict) or event.get('schemaVersion') != 1:
        raise BookUnavailable('云带K线来源不匹配')
    if not isinstance(manifest_record, dict) or manifest_record.get('id') != f'cloud-v17-{trade:06d}':
        raise BookUnavailable('云带交易编号不匹配')
    symbol, side = event.get('symbol'), _side(event.get('side'))
    window = event.get('window') if isinstance(event.get('window'), dict) else {}
    start, end = _ms(window.get('startMs')), _ms(window.get('endMs'))
    if (event.get('id') != manifest_record.get('id') or symbol != manifest_record.get('symbol') or
            event.get('side') != manifest_record.get('side') or
            event.get('entryMs') != manifest_record.get('entryMs') or event.get('exitMs') != manifest_record.get('exitMs') or
            not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{1,32}', symbol) or side is None or
            window.get('timeframe') != '12h' or start is None or end is None or end <= start):
        raise BookUnavailable('云带事件窗口或币种不完整')
    for event_key, manifest_key in (('entryPrice', 'entryPrice'), ('exitPrice', 'exitPrice')):
        left, right = _num(event.get(event_key)), _num(manifest_record.get(manifest_key))
        if left is None or right is None or not math.isclose(left, right, rel_tol=1e-12, abs_tol=0.0):
            raise BookUnavailable('云带事件成交价与历史账本不匹配')
    url = event.get('symbolDataUrl')
    if not isinstance(url, str) or not re.fullmatch(r'\.\./symbols/[A-Z0-9]{1,32}\.json\.gz', url) or url.rsplit('/', 1)[-1] != symbol + '.json.gz':
        raise BookUnavailable('云带K线币种引用无效')
    expected_columns = ['timeMs', 'open', 'high', 'low', 'close', 'ema20', 'ema50', 'emaCount', 'emaReady']
    if (not isinstance(symbol_data, dict) or symbol_data.get('schemaVersion') != 1 or symbol_data.get('symbol') != symbol or
            symbol_data.get('timeframe') != '12h' or symbol_data.get('columns') != expected_columns or
            not isinstance(symbol_data.get('rows'), list)):
        raise BookUnavailable('云带 12H 行情字段不匹配')
    candles = []
    last_ms = None
    for row in symbol_data['rows']:
        if not isinstance(row, list) or len(row) != 9:
            raise BookUnavailable('云带 12H 行情行不完整')
        time_ms = _ms(row[0])
        # The frozen chart contract treats endMs as the opening timestamp of the final included bar.
        if time_ms is None or time_ms < start or time_ms > end:
            continue
        if last_ms is not None and time_ms <= last_ms:
            raise BookUnavailable('云带 12H 行情时间顺序无效')
        nums = [_num(row[i]) for i in range(1, 7)]
        if any(v is None for v in nums) or _num(row[7]) is None or not isinstance(row[8], bool):
            raise BookUnavailable('云带 12H 行情价格或云带值无效')
        opening, high, low, close, ema20, ema50 = nums
        if min(opening, high, low, close) <= 0 or high < max(opening, close) or low > min(opening, close):
            raise BookUnavailable('云带 12H OHLC 结构无效')
        candles.append(dict(time=time_ms, open=opening, high=high, low=low, close=close, volume=None,
                            ema20=ema20, ema50=ema50, ema_ready=row[8]))
        last_ms = time_ms
    if not candles:
        raise BookUnavailable('这段云带 12H 行情暂不可读')
    available = _ms(window.get('availableBars'))
    if available is None or available != len(candles):
        raise BookUnavailable('云带 12H 行情与冻结窗口根数不一致')
    evidence = event.get('entryEvidence') if isinstance(event.get('entryEvidence'), dict) else {}
    rails = evidence.get('rails') if isinstance(evidence.get('rails'), dict) else {}
    # The cloud ledger groups four funded child slots into one history row. Keep
    # the exact frozen fills alongside that summary so native replay can show
    # the entry and every actual partial/final exit on the source candles.
    try:
        from yoyo.monitor.strategy_live_cloud import exit_legs
        exits = exit_legs(event)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BookUnavailable('云带冻结成交流水不完整') from exc
    opening_quantity = math.fsum(leg['quantity'] for leg in exits)
    executions = [dict(time_ms=_ms(event.get('entryMs')), start_ms=_ms(event.get('entryMs')),
                       end_ms=_ms(event.get('entryMs')), price=_num(event.get('entryPrice')),
                       label='开仓', action='entry', phase='open', quantity=opening_quantity, share=1.0)]
    executions.extend(exits)
    lines = [item for item in (
        _cloud_reference(event.get('entryPrice'), '入场', 'up' if side == 'long' else 'down'),
        _cloud_reference(event.get('exitPrice'), '退出', 'accent'),
        _cloud_reference(evidence.get('fixedStopPrice'), '固定止损', 'down'),
        _cloud_reference(evidence.get('twoRPrice'), '2R', 'up'),
        _cloud_reference(rails.get('selectedSideRailPrice'), '信号侧周一轨道', 'cloud'),
    ) if item]
    expected = _ms(window.get('expectedBars')) or 0
    missing = max(0, expected - available)
    note = f"OKX 原始 12H K 线 · 北京时间 · 窗口 {available}/{expected} 根可用；缺少的K线保留为空档，不补值。"
    return dict(track=track, trade=_cloud_record_trade(manifest_record), bar='12H', source='OKX', note=note,
                candles=candles, weekly_ranges=[], references=lines, missing_ranges=window.get('missingRanges') or [],
                expected_bars=expected, available_bars=available, missing_bars=missing,
                execution_legs=executions)


def project_research(data, track):
    accounts = data.get('accounts') if isinstance(data, dict) else None
    r = accounts.get(track) if isinstance(accounts, dict) else None
    if not isinstance(r, dict):
        return None
    sections = []
    for x in r.get('sections') or []:
        table = _table(x.get('headers'), x.get('rows')) if isinstance(x, dict) else None
        if table:
            sections.append(dict(title=_text(x.get('title'), 60) or '', note=_text(x.get('note')), **table))
    controls = r.get('controls') if isinstance(r.get('controls'), dict) else None
    controls = dict(note=_text(controls.get('note')), **_table(controls.get('headers'), controls.get('rows'))) \
        if controls and _table(controls.get('headers'), controls.get('rows')) else None
    capital = _table(r.get('capital_headers'), r.get('capital_rows'))
    return dict(status=_text(r.get('status'), 40), note=_text(r.get('note')), detail=_text(r.get('detail')),
                table=_table(r.get('headers'), r.get('rows')), capital=capital, controls=controls, sections=sections[:8],
                methodology=_text(data.get('methodology'), 600), period_note=_text(data.get('period_note')))


def project_booklet(track, label, title, paper_raw, research, history, history_error=None):
    """Booklet for one paper account. ``history`` may be None (curve missing → state kept honest)."""
    raw = paper_raw if isinstance(paper_raw, dict) else {}
    config = raw.get(track) if isinstance(raw.get(track), dict) else {}
    has_account = isinstance(raw.get('tracks'), dict) and track in raw['tracks']
    return dict(key=track, label=label, title=title, kind='breadth' if _num(config.get('hold_days')) is not None else 'fib',
                paper=has_account, facts=facts(track, raw), flow=flow(track),
                history=history, history_error=history_error, research=project_research(research, track),
                backtest=True, notes=['历史为币安回测，前向为 OKX 模拟；路径差异须保留说明，不代表实盘收益。'])


def project_cloud_booklet(guide, overview, history_raw, include_hourly_curve=True):
    if not isinstance(guide, dict) or not isinstance(overview, dict) or not isinstance(overview.get('metrics'), dict):
        raise BookUnavailable('云带小册内容暂不可用')
    history = project_history(history_raw, 'live-cloud-v17')
    metrics = overview['metrics']
    max_bound = _num(metrics.get('dd_bound_pct'))
    if max_bound is not None:
        history['summary']['max_drawdown_bound_pct'] = max_bound
    intro = _text(guide.get('intro'), 1800)
    facts = []
    for item in guide.get('facts') or []:
        if not isinstance(item, dict) or not isinstance(item.get('label'), str):
            continue
        facts.append(_fact(item['label'][:80], str(item.get('value', '—'))[:80], _text(item.get('unit'), 40) or ''))
    # The amount payoff ratio and gross profit factor answer different questions; never merge them.
    amount_payoff = _num(metrics.get('payoff_usdt'))
    if amount_payoff is not None:
        facts.append(_fact('金额盈亏比', f'{amount_payoff:.2f}', '倍'))
    steps = [dict(title=_text(x.get('title'), 80) or '', text=_text(x.get('text'), 1800) or '')
             for x in guide.get('flow') or [] if isinstance(x, dict)]
    direction = guide.get('direction') if isinstance(guide.get('direction'), dict) else {}
    table = _table(direction.get('headers'), direction.get('rows'), 20)
    cases = [dict(title=_text(x.get('title'), 100) or '', text=_text(x.get('text'), 1800) or '')
             for x in guide.get('cases') or [] if isinstance(x, dict)]
    risks = [_text(x, 1800) for x in guide.get('risk') or [] if isinstance(x, str)]
    sources = [_text(x, 1800) for x in guide.get('sources') or [] if isinstance(x, str)]
    research = dict(status='OKX 12H 历史与前向模拟', note=intro, detail=None, table=table,
                    capital=None, controls=None, sections=[], methodology=None, period_note=None,
                    cases=cases, risk=risks, sources=sources)
    history_error = None
    if not include_hourly_curve:
        # Clients predating the hourly-marked curve contract render every curve as realized P&L.
        # Give them the same historical facts and rules, but withhold the chart until they can label it honestly.
        summary = history['summary']
        summary_rows = [
            ['期末资金', _num(metrics.get('final_usdt'))],
            ['历史净收益', _num(summary.get('pnl'))],
            ['小时阶段最大回撤', _num(metrics.get('dd_mark_pct'))],
            ['高低价估值回撤上界', _num(metrics.get('dd_bound_pct'))],
            ['金额盈亏比', _num(metrics.get('payoff_usdt'))],
            ['盈利因子', _num(summary.get('profit_factor'))],
            ['交易数', _num(metrics.get('trades'))],
            ['胜率 %', _num(metrics.get('win_pct'))],
        ]
        case_rows = [[item['title'], item['text']] for item in cases]
        research['table'] = dict(headers=['历史指标', '值'], rows=summary_rows)
        legacy_sections = []
        if table:
            legacy_sections.append(dict(title='入场条件 · 多空方向', headers=table['headers'], rows=table['rows']))
        legacy_sections.extend([
            dict(title='典型交易', headers=['案例', '说明'], rows=case_rows),
            dict(title='风险与边界', headers=['说明'], rows=[[line] for line in risks]),
            dict(title='数据来源与口径', headers=['说明'], rows=[[line] for line in sources]),
        ])
        research['sections'] = legacy_sections
        research['detail'] = '；'.join(risks)
        history = None
        history_error = '此版 App 暂不支持小时含浮盈亏曲线与原始 12H K 线回放；升级新版后可查看。历史汇总与完整规则见本页。'
    return dict(key='live-cloud-v17', label='live-cloud-v17', title=_text(guide.get('title'), 120) or '云带 v1.7 · 12H 逆向',
                kind='cloud', paper=True, facts=facts, flow=steps, history=history,
                research=research, history_error=history_error, backtest=True,
                notes=['实时账户独立从 1,000U 起步，不注入历史收益。', '云带信号只作观察，模拟账户不下单。'])


# ---- breadth-ema20 research booklet (breadth-booklet.js / breadth-book-history.js) -----------------------------

def _utc(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise BookUnavailable('全市场下轨账本时间无效')
    return int(datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp() * 1000)


def project_breadth_ema20(data):
    if not isinstance(data, dict) or data.get('schema_version') != 'breadth-ema20.v1' or data.get('strategy_id') != 'breadth-ema20':
        raise BookUnavailable('全市场下轨冻结账本版本不匹配')
    summaries = data.get('summaries') or {}
    full, funded = summaries.get('all_history_fee_only') or {}, summaries.get('2026_actual_funded') or {}
    curve = (data.get('equity_curves') or {}).get('all_history_fee_only_settlement') or {}
    rows = curve.get('points')
    if not isinstance(rows, list) or len(rows) < 2:
        raise BookUnavailable('全市场下轨结算曲线缺失')
    points, peak = [], 0.0
    for p in rows:
        equity = _num(p.get('combined_equity')) if isinstance(p, dict) else None
        if equity is None:
            raise BookUnavailable('全市场下轨结算曲线不完整')
        value = equity * 1000
        peak = max(peak, value)
        points.append([_utc(p.get('time')), value, (value / peak - 1) * 100 if peak else 0.0])
    years = [r for r in summaries.get('calendar_year_fee_only_reset') or [] if isinstance(r, dict)]
    rules = data.get('rules') or {}
    m, c, a = rules.get('btc_medium') or {}, rules.get('btc_case') or {}, rules.get('alt') or {}
    n = lambda v, d=0: '—' if _num(v) is None else f'{v:.{d}f}'.rstrip('0').rstrip('.') if d else f'{v:.0f}'
    flow_steps = [
        dict(title='数下轨', text='取纽约周一最高价 H 与最低价 L，周一结束后冻结下轨 L − 0.618 × (H − L)。每个币每周第一次触及下轨才计数，按 UTC 对齐的 5 分钟窗口汇总，窗口收盘后才知道数量。'),
        dict(title='普通 BTC', text=f"{n((_num(m.get('initial_window_min')) or 0)/60)} 小时窗口至少 {n(m.get('initial_touch_count_min'))} 个币首触下轨；再观察 {n(m.get('post_window_wait_min'))} 分钟，新增 {n(m.get('new_coins_max'))} 个且至少 {n((_num(m.get('reclaim_fraction_min')) or 0)*100)}% 成员收回；最近 4 小时 BTC 收盘 ≤ EMA20 时买 BTC，持有 {n((_num(m.get('max_hold_hours')) or 0)/24)} 天，无价格止损。"),
        dict(title='急跌 BTC', text=f"{n(c.get('initial_window_min'))} 分钟触价币数占近 28 天活跃币数 ≥ {n(c.get('market_share_28d_min_pct'), 1)}%；观察 {n(c.get('post_window_wait_min'))} 分钟，新增 ≤ 初始 {n(c.get('new_coins_to_initial_max_pct'), 1)}%、山寨涨幅中位数 ≥ {n(c.get('alt_move_min_pct'), 1)}%；买 BTC，6% 保护止损，到目标卖一半，最长 {n((_num(c.get('max_hold_hours')) or 0)/24)} 天。"),
        dict(title='山寨篮子', text=f"{n(a.get('initial_window_min'))} 分钟内至少 {n(a.get('initial_touch_count_min'))} 个币触轨且占比 ≥ {n(a.get('market_share_28d_min_pct'), 1)}%，窗口收盘等权买入初始成员（排除 BTC、ETH、SOL、USTC），持有 {n(a.get('hold_hours'))} 小时；两次入场至少间隔 24 小时。"),
        dict(title='仓位与成本', text='1,000 USDT 起步，BTC 与山寨各 500 独立复利、不跨侧调仓。市价单边 0.05%、限价 0.02%；全历史只扣手续费，2026 专项另计资金费。')]
    years_table = dict(headers=['年份', '事件', 'BTC', '山寨', '收益 %'],
                       rows=[[str(r.get('year')), _num(r.get('event_count')), _num(r.get('btc_event_count')),
                              _num(r.get('alt_event_count')), _num(r.get('equal_sleeves_growth_pct'))] for r in years])
    sleeves = full.get('returns_by_sleeve') if isinstance(full.get('returns_by_sleeve'), dict) else {}
    sleeve_rows = [[k, _num((v or {}).get('growth_pct')), _num((v or {}).get('max_drawdown_pct'))] for k, v in sleeves.items()
                   if isinstance(k, str)]
    final = points[-1][1]
    history = dict(start_ms=points[0][0], end_ms=points[-1][0],
                   note='同一冻结账本按平仓结算；全历史只扣手续费，不含资金费；最大回撤按每 5 分钟同步盯市。', refilled=False, unit='%',
                   summary=dict(start=1000.0, end=final, pnl=final - 1000, return_pct=_num(full.get('growth_pct')),
                                max_drawdown_pct=_num(full.get('max_drawdown_pct')), win_pct=None,
                                trades=_num(full.get('event_count')), profit_factor=None, no_losses=False, cash_added=0.0,
                                avg_win=None, avg_loss=None),
                   drawdown_basis='marked',
                   points=[[p[0], round(p[1], 4), round(p[2], 3)] for p in downsample(points)],
                   yearly=[[str(r.get('year')), _num(r.get('equal_sleeves_growth_pct'))] for r in years], monthly=[],
                   sides=dict(headers=['分支', '收益 %', '最大回撤 %'], rows=sleeve_rows) if sleeve_rows else None)
    sections = [dict(title='逐年 · 每年 1 月 1 日重置', note='仅扣手续费；每年两边各从 500 USDT 重新起步。', **years_table)]
    if funded:
        sections.append(dict(title='2026 · 含资金费', note='2026-01-01 至 2026-10-27，含已结算资金费。',
                             headers=['事件', 'BTC', '山寨', '胜率 %', '收益 %', '盯市回撤 %'],
                             rows=[[_num(funded.get('event_count')), _num(funded.get('btc_event_count')),
                                    _num(funded.get('alt_event_count')), _num(funded.get('win_rate_pct')),
                                    _num(funded.get('growth_pct')), _num(funded.get('max_drawdown_pct'))]]))
    return dict(key='breadth-ema20', label='全市场下轨反弹', title='BTC + 山寨篮子 · 研究小册', kind='research', paper=False,
                facts=[_fact('全历史收益', f"{_pct(full.get('growth_pct'), 1) or '—'}%"),
                       _fact('盯市回撤', f"{_pct(full.get('max_drawdown_pct'), 1) or '—'}%"),
                       _fact('事件', f"{n(full.get('event_count'))}", '次')],
                flow=flow_steps, history=history, history_error=None,
                research=dict(status='组合回测', note='回看至 2026-10-27 UTC 信号；规则与参数在同一段历史上研究过，不是独立样本外验证。',
                              detail=None, table=None, capital=None, controls=None, sections=sections,
                              methodology=None, period_note=None),
                backtest=False,
                notes=['只有研究小册，没有前向模拟账户；网页模拟盘另有前向观察进程。', '研究交易与 K 线回放目前只在网页版小册。'])


# ---- saved backtest trades (strategy_backtest.py page / candles) ------------------------------------------------

def _trade(r):
    if not isinstance(r, dict) or not isinstance(r.get('symbol'), str) or not COIN.fullmatch(r['symbol']):
        return None
    rules = r.get('entry_rules') if isinstance(r.get('entry_rules'), dict) else {}
    fills = r.get('fills') if isinstance(r.get('fills'), dict) else {}
    reason = r.get('exit_reason') if r.get('exit_reason') in EXIT_REASONS else None
    return dict(id=_ms(r.get('id')), symbol=r['symbol'], coin=r['symbol'][:-4],
                side='long' if r.get('side') == 1 else 'short' if r.get('side') == -1 else None,
                entry_ms=_ms(r.get('entry_ms')), exit_ms=_ms(r.get('exit_ms')), pnl=_num(r.get('pnl')),
                return_pct=_num(r.get('return_pct')), exit_reason=reason,
                score=_num(rules.get('score')), count=_num(rules.get('initial')), new_coins=_num(rules.get('new_coins')),
                kind=_text(rules.get('kind'), 20), entry_price=_num(fills.get('entry_price')), exit_price=_num(fills.get('exit_price')),
                entry_start_ms=_ms(fills.get('entry_start_ms') or r.get('entry_start_ms')), entry_end_ms=_ms(fills.get('entry_end_ms')),
                exit_start_ms=_ms(fills.get('exit_start_ms')), exit_end_ms=_ms(fills.get('exit_end_ms')))


def project_backtest(value, track):
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('track') != track:
        raise BookUnavailable('历史回测账本不匹配')
    meta = value.get('meta') or {}
    rows = [t for t in (_trade(r) for r in value.get('rows') or []) if t and t['id'] and t['side']]
    return dict(track=track, total=_ms(value.get('total')) or 0, offset=_ms(value.get('offset')) or 0,
                has_more=bool(value.get('has_more')), sort=_text(value.get('sort'), 10), direction=_text(value.get('direction'), 4),
                meta=dict(trades=_num(meta.get('trades')), pnl=_num(meta.get('pnl')), start=_text(meta.get('start'), 32),
                          end=_text(meta.get('end'), 32), source=_text(meta.get('source'), 20), note=_text(meta.get('note')),
                          basis=_text(meta.get('basis')), limitation=_text(meta.get('limitation'))),
                rows=rows)


def project_candles(value, track, trade):
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('track') != track or value.get('trade_id') != trade:
        raise BookUnavailable('回测K线与交易不匹配')
    candles = []
    for c in value.get('candles') or []:
        bar = [_num(c.get(k)) if isinstance(c, dict) else None for k in ('time', 'open', 'high', 'low', 'close', 'volume')]
        if any(x is None for x in bar):
            continue
        candles.append(dict(zip(('time', 'open', 'high', 'low', 'close', 'volume'), bar)))
    if not candles:
        raise BookUnavailable('这段历史行情暂不可读')
    ranges = []
    for w in value.get('weekly_ranges') or []:
        if not isinstance(w, dict) or w.get('state') not in (None, 'ready'):
            continue
        item = dict(week=_text(w.get('week'), 10), start_ms=_ms(w.get('start_ms')), end_ms=_ms(w.get('end_ms')),
                    expires_ms=_ms(w.get('expires_ms')), **{k: _num(w.get(k)) for k in ('high', 'low', 'upper', 'lower')})
        if all(item[k] is not None for k in ('start_ms', 'end_ms', 'high', 'low', 'upper', 'lower')):
            ranges.append(item)
    t = _trade(value.get('backtestTrade'))
    if not t:
        raise BookUnavailable('回测交易数据不完整')
    return dict(track=track, trade=t, bar=_text(value.get('bar'), 4), source=_text(value.get('source'), 20),
                note=_text(value.get('note')), candles=candles[-5000:], weekly_ranges=ranges[:20])
