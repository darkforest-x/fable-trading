"""Cache exact OKX 1m paths for order-sensitive SHORT cap-4 H1 windows.

This is a data refinement step only. It does not replay trades or select an
exit. Each retained hour has 60 confirmed OKX 1m candles and reconciles to the
frozen H1 open/high/low/close before its minutes are emitted.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.month_first_week_alt_entries import ROOT, prices, universe
from yoyo.evaluation.month_first_week_alt_minute_alert import CACHE, request_window


OUT = ROOT / 'experiments/active/exp-alt-month-first-week-be2-20261110-v1'
INPUT = ROOT / 'experiments/active/exp-alt-month-first-week-near-target-20261110-v1/trades.csv.gz'
MINUTES_OUT = OUT / 'refined_minutes.csv.gz'
COVERAGE_OUT = OUT / 'minute_coverage.csv.gz'
RECEIPT_OUT = OUT / 'minute_receipt.json'
MANIFEST_OUT = OUT / 'minute_source_manifest.json'
SYMBOL_STATUS = OUT / 'minute_symbol_status.json'
HOUR = pd.Timedelta(hours=1)
MINUTE = pd.Timedelta(minutes=1)
TRIGGER_FRACTION = 0.02
TAKER_FEE = 0.0005
REQUEST_INTERVAL = 1 / 7
EXPECTED_TRADES = 4_846
EXPECTED_SIGNALS = 2_423
EXPECTED_CONTROLS = 2_423
EXPECTED_WINDOWS = 1_915


def _truth(value):
    return bool(value) and not pd.isna(value)


def _window_key(symbol, hour):
    return symbol, pd.Timestamp(hour).tz_convert('UTC')


def _add_role(windows, symbol, hour, role, trade_id, is_control, h1_row):
    key = _window_key(symbol, hour)
    if key not in windows:
        windows[key] = dict(
            symbol=symbol,
            hour=key[1],
            h1_open=float(h1_row.open),
            h1_high=float(h1_row.high),
            h1_low=float(h1_row.low),
            h1_close=float(h1_row.close),
            roles=set(),
            trade_ids=set(),
            signal_ids=set(),
            control_ids=set(),
            role_trade_ids={},
        )
    row = windows[key]
    row['roles'].add(role)
    row['trade_ids'].add(trade_id)
    (row['control_ids'] if is_control else row['signal_ids']).add(trade_id)
    row['role_trade_ids'].setdefault(role, set()).add(trade_id)


def plan_windows():
    """Plan only held SHORT cap-4 hours that have an order-sensitive touch."""
    trades = pd.read_csv(INPUT, dtype={'cap': str})
    trades['entry_time'] = pd.to_datetime(trades.entry_time, utc=True)
    trades['exit_time'] = pd.to_datetime(trades.exit_time, utc=True)
    short_cap4 = trades[(trades.cap == '4') & trades.side.eq('upper')].copy()
    signals = int((~short_cap4.is_control).sum())
    controls = int(short_cap4.is_control.sum())
    if (len(short_cap4), signals, controls) != (
        EXPECTED_TRADES, EXPECTED_SIGNALS, EXPECTED_CONTROLS
    ):
        raise ValueError(
            f'Frozen SHORT cap-4 input changed: n={len(short_cap4)}, '
            f'signals={signals}, controls={controls}'
        )

    specs = {row.symbol: row for row in universe().itertuples()}
    windows = {}
    symbol_sources = {}
    activation_order_trades = set()
    be_target_order_trades = set()
    original_dual_trades = set()

    for symbol, group in short_cap4.groupby('symbol', sort=True):
        if symbol not in specs:
            raise ValueError(f'No H1 source specification for {symbol}')
        spec = specs[symbol]
        bars = prices(spec)
        symbol_sources[symbol] = dict(
            h1_file=str(spec.file.relative_to(ROOT)),
            h1_cohort=spec.cohort,
            h1_cutoff_utc=spec.cutoff.isoformat(),
            h1_hours_loaded=int(len(bars)),
            h1_first_utc=bars.index.min().isoformat() if len(bars) else None,
            h1_last_utc=bars.index.max().isoformat() if len(bars) else None,
        )

        for trade in group.itertuples(index=False):
            entry = trade.entry_time
            exit_candidate = trade.exit_time
            # Timeout is a final H1 open liquidation. Its high/low are not held.
            held_end = exit_candidate - HOUR if trade.exit_reason == 'timeout' else exit_candidate
            held = bars.loc[(bars.index >= entry) & (bars.index <= held_end)]
            if held.empty or entry not in held.index:
                raise ValueError(f'No held H1 bars for {trade.trade_id}')

            is_control = _truth(trade.is_control)
            if _truth(trade.ambiguous_sl_tp):
                if exit_candidate not in bars.index:
                    raise ValueError(f'Dual H1 exit bar missing for {trade.trade_id}')
                original_dual_trades.add(trade.trade_id)
                _add_role(windows, symbol, exit_candidate, 'original_dual_sl_tp',
                          trade.trade_id, is_control, bars.loc[exit_candidate])

            entry_price = float(trade.entry_fill)
            trigger = entry_price * (1 - TRIGGER_FRACTION)
            fee_be = entry_price * (1 - TAKER_FEE) / (1 + TAKER_FEE)
            target = float(trade.target_price)
            # For a short, the target is farther than 2% only below the trigger.
            if target >= trigger:
                continue

            activated = held[held.low <= trigger]
            if activated.empty:
                continue
            activation_hour = activated.index[0]
            activation_bar = held.loc[activation_hour]

            # Open remained above the trigger, so the relative minute order of
            # activation and the fee-covering stop is not implied by H1 OHLC.
            if float(activation_bar.open) > trigger and float(activation_bar.high) >= fee_be:
                activation_order_trades.add(trade.trade_id)
                _add_role(windows, symbol, activation_hour, 'activation_vs_fee_be',
                          trade.trade_id, is_control, activation_bar)

            after_activation = held.loc[held.index >= activation_hour]
            overlap = after_activation[
                (after_activation.high >= fee_be) & (after_activation.low <= target)
            ]
            for hour, bar in overlap.iterrows():
                be_target_order_trades.add(trade.trade_id)
                _add_role(windows, symbol, hour, 'fee_be_vs_target',
                          trade.trade_id, is_control, bar)

    planned = list(windows.values())
    # A window shared by signal and control trades is served in the actual-first
    # pass; otherwise signals are completed before control-only windows.
    planned.sort(key=lambda row: (
        0 if row['signal_ids'] else 1,
        row['symbol'],
        row['hour'],
    ))
    if len(planned) != EXPECTED_WINDOWS:
        raise ValueError(f'Frozen H1 candidate plan changed: {len(planned)} windows')

    plan_summary = dict(
        trades=len(short_cap4), signals=signals, controls=controls,
        symbols=int(short_cap4.symbol.nunique()),
        original_dual_trade_hours=len(original_dual_trades),
        activation_order_trade_hours=len(activation_order_trades),
        fee_be_target_trade_hours=len(be_target_order_trades),
        unique_symbol_hours=len(planned),
        role_windows={
            role: sum(role in row['roles'] for row in planned)
            for role in ('original_dual_sl_tp', 'activation_vs_fee_be', 'fee_be_vs_target')
        },
        multi_role_windows=sum(len(row['roles']) > 1 for row in planned),
        first_hour=min(row['hour'] for row in planned).isoformat(),
        last_hour=max(row['hour'] for row in planned).isoformat(),
    )
    return planned, short_cap4, symbol_sources, plan_summary


def _raw_path(symbol, hour):
    return CACHE / symbol / (pd.Timestamp(hour).strftime('%Y%m%dT%H') + '.json.gz')


def _read_payload(path):
    with gzip.open(path, 'rt') as source:
        return json.load(source)


def _save_payload(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with gzip.open(tmp, 'wt') as target:
        json.dump(payload, target)
    tmp.replace(path)


def _read_symbol_status():
    if not SYMBOL_STATUS.exists():
        return {}
    return json.loads(SYMBOL_STATUS.read_text())


def _write_symbol_status(status):
    tmp = SYMBOL_STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + '\n')
    tmp.replace(SYMBOL_STATUS)


def _paced_request(symbol, hour):
    """Use the existing shared request-window limiter and retry behavior."""
    return request_window(symbol, pd.Timestamp(hour) + HOUR)


def _validate(payload, window):
    code = str(payload.get('code', 'missing'))
    if code != '0':
        return f'api_{code}', [], str(payload.get('msg', ''))
    data = payload.get('data') or []
    bars = {}
    for item in data:
        if len(item) >= 9 and str(item[8]) == '1':
            try:
                bars[int(item[0])] = item
            except (TypeError, ValueError):
                continue
    hour = window['hour']
    start_ms = int(hour.timestamp() * 1000)
    stamps = range(start_ms, start_ms + 3_600_000, 60_000)
    if any(stamp not in bars for stamp in stamps):
        return 'incomplete_1m', [], f'confirmed={len(set(stamps) & set(bars))}/60'
    rows = []
    try:
        for stamp in stamps:
            item = bars[stamp]
            o, h, low, close = (float(value) for value in item[1:5])
            if (not np.isfinite([o, h, low, close]).all() or min(o, h, low, close) <= 0
                    or h < max(o, close, low) or low > min(o, close)):
                return 'invalid_1m_ohlc', [], f'invalid bar at {stamp}'
            rows.append(dict(
                symbol=window['symbol'], hour=hour.isoformat(),
                time=pd.Timestamp(stamp, unit='ms', tz='UTC').isoformat(),
                open=o, high=h, low=low, close=close,
            ))
    except (IndexError, TypeError, ValueError) as error:
        return 'invalid_1m_payload', [], str(error)

    minute = np.asarray([[r['open'], r['high'], r['low'], r['close']] for r in rows])
    got = [minute[0, 0], minute[:, 1].max(), minute[:, 2].min(), minute[-1, 3]]
    expected = [window['h1_open'], window['h1_high'], window['h1_low'], window['h1_close']]
    if not np.allclose(got, expected, rtol=1e-7, atol=0):
        deltas = [float(a - b) for a, b in zip(got, expected)]
        return 'ohlc_mismatch', [], json.dumps(dict(minute=got, h1=expected, delta=deltas))
    return 'validated', rows, ''


def _coverage_row(window, status, source, detail='', validated=0):
    return dict(
        symbol=window['symbol'], hour=window['hour'].isoformat(),
        roles='|'.join(sorted(window['roles'])),
        trade_count=len(window['trade_ids']),
        signal_trade_count=len(window['signal_ids']),
        control_trade_count=len(window['control_ids']),
        trade_ids='|'.join(sorted(window['trade_ids'])),
        h1_open=window['h1_open'], h1_high=window['h1_high'],
        h1_low=window['h1_low'], h1_close=window['h1_close'],
        status=status, source=source, detail=detail, validated_minutes=validated,
    )


def _permanent_unavailable(symbol, hour):
    return dict(
        instrument_id=f'{symbol}-USDT-SWAP',
        permanent_unavailable=True,
        first_error_code='51001',
        first_error_hour=pd.Timestamp(hour).isoformat(),
        reason='OKX instrument not found; do not retry on later runs',
    )


def _process_symbol(symbol, windows, offline, initial_status):
    """Process one symbol serially; independent symbols run concurrently."""
    local_status = dict(initial_status or {})
    coverage = []
    minutes = {}
    request_count = cache_hits = permanent_skips = 0

    for window in windows:
        path = _raw_path(symbol, window['hour'])
        payload = None
        source = 'cache'
        detail = ''
        if path.exists():
            try:
                payload = _read_payload(path)
                cache_hits += 1
                if str(payload.get('code', '')) == '51001':
                    local_status = _permanent_unavailable(symbol, window['hour'])
            except (OSError, ValueError, json.JSONDecodeError) as error:
                source = 'cache_read_error'
                detail = str(error)

        if payload is None:
            if offline:
                coverage.append(_coverage_row(window, 'cache_miss', source, detail))
                continue
            if _truth(local_status.get('permanent_unavailable')):
                permanent_skips += 1
                coverage.append(_coverage_row(
                    window, 'api_51001_cached_symbol', 'symbol_status',
                    'contract previously returned instrument not found',
                ))
                continue
            payload = _paced_request(symbol, window['hour'])
            request_count += 1
            source = 'okx_api'
            _save_payload(path, payload)
            if str(payload.get('code', '')) == '51001':
                local_status = _permanent_unavailable(symbol, window['hour'])

        status, valid_minutes, detail = _validate(payload, window)
        if status == 'api_51001':
            local_status = _permanent_unavailable(symbol, window['hour'])
        coverage.append(_coverage_row(window, status, source, detail, len(valid_minutes)))
        if valid_minutes:
            minutes[(symbol, window['hour'])] = valid_minutes

    return dict(
        symbol=symbol, coverage=coverage, minutes=minutes,
        request_count=request_count, cache_hits=cache_hits,
        permanent_skips=permanent_skips, symbol_status=local_status,
    )


def _write_outputs(plan, coverage, minute_rows, receipt, symbol_sources, symbol_status):
    coverage_frame = pd.DataFrame(coverage)
    coverage_frame.to_csv(COVERAGE_OUT, index=False, compression='gzip')
    minute_frame = pd.DataFrame(
        minute_rows, columns=['symbol', 'hour', 'time', 'open', 'high', 'low', 'close']
    )
    minute_frame.to_csv(MINUTES_OUT, index=False, compression='gzip')
    manifest = dict(
        input_trades=str(INPUT.relative_to(ROOT)),
        input_trades_rows=int(receipt['input']['selected_trades']),
        selected_direction="SHORT (frozen side='upper', sign=-1 in source engine)",
        h1_sources=symbol_sources,
        minute_source=dict(
            provider='OKX public market API',
            endpoint='https://www.okx.com/api/v5/market/history-candles',
            instId_template='{symbol}-USDT-SWAP', bar='1m',
            cache_root=str(CACHE.relative_to(ROOT)),
            min_request_interval_seconds=REQUEST_INTERVAL,
            bounded_workers=8,
            accepted_rule='60 confirmed minutes; exact H1 OHLC reconciliation (rtol=1e-7, atol=0)',
        ),
        symbol_api_status=symbol_status,
        window_status_counts=coverage_frame.status.value_counts().to_dict()
        if len(coverage_frame) else {},
    )
    tmp = MANIFEST_OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    tmp.replace(MANIFEST_OUT)
    tmp = RECEIPT_OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    tmp.replace(RECEIPT_OUT)
    _write_symbol_status(symbol_status)


def run(offline=False):
    plan, trades, symbol_sources, summary = plan_windows()
    print('planned', json.dumps(summary, sort_keys=True), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    symbol_status = _read_symbol_status()
    coverage_by_key = {
        _window_key(row['symbol'], row['hour']): _coverage_row(row, 'pending', 'not_processed')
        for row in plan
    }
    minutes_by_key = {}
    request_count = 0
    cache_hit_count = 0
    permanent_skip_count = 0
    processed_count = 0

    receipt = dict(
        status='in_progress', input=dict(
            path=str(INPUT.relative_to(ROOT)),
            selected_trades=len(trades),
            signals=int((~trades.is_control).sum()),
            controls=int(trades.is_control.sum()),
            symbols=int(trades.symbol.nunique()),
            side='upper / SHORT', cap='4',
            exit_reasons=trades.exit_reason.value_counts().to_dict(),
        ),
        plan=summary,
        rule=dict(
            favorable_trigger_pct=TRIGGER_FRACTION * 100,
            fee_be_price='entry * (1 - 0.0005) / (1 + 0.0005)',
            fee_assumption='0.05% taker on entry and fee-breakeven stop exit',
            trigger_condition='target farther than 2%; first held H1 low <= trigger',
            ambiguity_conditions=[
                'first trigger H1 open > trigger and high >= fee breakeven',
                'after activation through original exit candidate: H1 high >= fee breakeven and low <= original target',
                'original dual stop/target H1 hours',
            ],
            timeout='final timeout H1 open only; final timeout hour extrema excluded',
            purpose='minute path evidence only; no trade replay',
        ),
        source=dict(provider='OKX public API; shared request_window helper',
                    min_request_interval_seconds=REQUEST_INTERVAL,
                    bounded_workers=8,
                    offline=bool(offline)),
        progress=dict(processed_windows=0, request_count=0, cache_hits=0,
                      validated_windows=0, validated_minutes=0),
    )

    phases = [
        ('actuals', [row for row in plan if row['signal_ids']]),
        ('controls', [row for row in plan if not row['signal_ids'] and row['control_ids']]),
    ]
    next_checkpoint = 100
    for phase_name, phase_windows in phases:
        grouped = {}
        for row in phase_windows:
            grouped.setdefault(row['symbol'], []).append(row)
        # One worker owns a symbol within each phase. That keeps failure state
        # local to the worker and avoids duplicate calls after code 51001.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_process_symbol, symbol, windows, offline, symbol_status.get(symbol, {})): symbol
                for symbol, windows in grouped.items()
            }
            for future in as_completed(futures):
                result = future.result()
                symbol = result['symbol']
                if _truth(result['symbol_status'].get('permanent_unavailable')):
                    symbol_status[symbol] = result['symbol_status']
                for row in result['coverage']:
                    coverage_by_key[_window_key(row['symbol'], row['hour'])] = row
                minutes_by_key.update(result['minutes'])
                request_count += result['request_count']
                cache_hit_count += result['cache_hits']
                permanent_skip_count += result['permanent_skips']
                processed_count += len(result['coverage'])

                if processed_count >= next_checkpoint or processed_count == len(plan):
                    coverage = [coverage_by_key[key] for key in sorted(coverage_by_key)]
                    minute_rows = [
                        row for key in sorted(minutes_by_key) for row in minutes_by_key[key]
                    ]
                    receipt['progress'] = dict(
                        phase=phase_name, processed_windows=processed_count,
                        planned_windows=len(plan), request_count=request_count,
                        cache_hits=cache_hit_count,
                        validated_windows=sum(r['status'] == 'validated' for r in coverage),
                        validated_minutes=len(minute_rows),
                        permanent_symbol_failures=sum(
                            _truth(v.get('permanent_unavailable')) for v in symbol_status.values()
                        ),
                    )
                    _write_outputs(plan, coverage, minute_rows, receipt, symbol_sources, symbol_status)
                    print('progress', json.dumps(receipt['progress'], sort_keys=True), flush=True)
                    next_checkpoint = ((processed_count // 100) + 1) * 100

    coverage = [coverage_by_key[key] for key in sorted(coverage_by_key)]
    minute_rows = [row for key in sorted(minutes_by_key) for row in minutes_by_key[key]]
    receipt['status'] = 'complete'
    receipt['progress'] = dict(
        processed_windows=processed_count, planned_windows=len(plan), request_count=request_count,
        cache_hits=cache_hit_count,
        validated_windows=sum(r['status'] == 'validated' for r in coverage),
        validated_minutes=len(minute_rows),
        permanent_symbol_failures=sum(
            _truth(v.get('permanent_unavailable')) for v in symbol_status.values()
        ),
        permanent_window_skips=permanent_skip_count,
    )
    _write_outputs(plan, coverage, minute_rows, receipt, symbol_sources, symbol_status)
    print('complete', json.dumps(receipt['progress'], sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='use saved raw OKX cache only')
    args = parser.parse_args()
    run(offline=args.offline)


if __name__ == '__main__':
    main()
