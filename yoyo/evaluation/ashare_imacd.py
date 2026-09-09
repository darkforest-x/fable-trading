"""Causal daily A-share IMACD and a long-only cash portfolio replay.

Source: LazyBear IMACD / imacd_dense_mtf_v2_7.pine and this experiment's
PROJECT_PLAN.md. Features use only daily OHLC through the decision close;
formation uses the preceding 12 bars, ATR-band qualification uses ATR[1],
and a qualified segment freezes that band. No cross-layer execution imports.
HFQ prices are continuous total-return coordinates; raw prices determine
lots, commissions and price-limit constraints. Corporate-action returns
assume reinvestment, not a literal dividend/share-delivery cash ledger.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Parameters:
    ma_length: int = 34
    signal_length: int = 9
    focus_bars: int = 12
    band_atr: float = .10
    quality: int = 1
    structure_bars: int = 10
    buffer_atr: float = .5
    stop_atr: float = 3.
    trail_atr: float = 4.
    trail_activation_r: float = 1.5


@dataclass(frozen=True)
class Costs:
    commission: float = .00025
    minimum: float = 5.
    slippage: float = .0005

    def fee(self, amount: float, date: str, sell: bool) -> float:
        transfer = .00002 if date < '2022-04-29' else .00001
        stamp = (.001 if date < '2023-08-28' else .0005) if sell else 0.
        return max(self.minimum, amount * self.commission) + amount * (transfer + stamp)


def rma(values, length):
    """SMA-seeded SMMA of finite observations, matching the frozen Pine source."""
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) >= length:
        out[length - 1] = np.mean(values[:length])
        for i in range(length, len(values)):
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def indicators(frame: pd.DataFrame, p: Parameters) -> pd.DataFrame:
    """OHLC[0..t]; MA 20/60/120, ATR14, prior12 widths, frozen-band focus.

    Suspended/no-volume rows are excluded before recurrences, as no observed
    trading bar exists there. Calendar suspension handling belongs to replay.
    The frozen zone excludes its release bar. Appending future rows cannot
    change any existing feature or signal.
    """
    f = frame.copy()
    f['date'] = f['date'].astype(str).str[:10]
    if f['date'].duplicated().any() or not f['date'].is_monotonic_increasing:
        raise ValueError('dates must be sorted and unique')
    f = f.loc[(f['tradestatus'].astype(str) == '1') & (f['volume'] > 0)].reset_index(drop=True)
    if len(f) == 0:
        return f.assign(signal=False, ready=False)
    o, h, l, c = [f[x].to_numpy(float) for x in ('open', 'high', 'low', 'close')]
    if not np.isfinite(np.c_[o, h, l, c]).all() or np.any(l <= 0) or np.any(h < np.maximum(o, c)) or np.any(l > np.minimum(o, c)):
        raise ValueError('invalid adjusted OHLC')
    src = pd.Series((h + l + c) / 3)
    e1 = src.ewm(span=p.ma_length, adjust=False).mean()
    mi = (2 * e1 - e1.ewm(span=p.ma_length, adjust=False).mean()).to_numpy()
    hi, lo = rma(h, p.ma_length), rma(l, p.ma_length)
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0.))
    sb = pd.Series(md).rolling(p.signal_length).mean().to_numpy()
    tr = np.maximum(h-l, np.maximum(abs(h-np.r_[c[0], c[:-1]]), abs(l-np.r_[c[0], c[:-1]])))
    atr = rma(tr, 14)
    f['md'], f['sb'], f['atr'] = md, sb, atr
    ma = []
    for n in (20, 60, 120):
        f[f'sma{n}'] = pd.Series(c).rolling(n).mean()
        f[f'ema{n}'] = pd.Series(c).ewm(span=n, adjust=False).mean()
        ma.extend([f[f'sma{n}'].to_numpy(), f[f'ema{n}'].to_numpy()])
    stack = np.vstack(ma)
    f['rope_high'], f['rope_low'] = np.max(stack, axis=0), np.min(stack, axis=0)
    f['width'] = (f['rope_high']-f['rope_low'])/atr
    f['prior_width'] = f['width'].shift(1).rolling(12).mean()
    f['structure_stop'] = pd.Series(l).rolling(p.structure_bars).min() - p.buffer_atr * atr
    f['atr_pct'] = atr / c
    # A causal relative-volatility bucket, evaluated from earlier observations.
    prior_vol = f['atr_pct'].shift(1)
    q = np.column_stack([prior_vol.rolling(120, min_periods=60).quantile(x) for x in (.25,.5,.75)])
    f['vol_bucket'] = (f['atr_pct'].to_numpy()[:, None] > q).sum(axis=1)
    f['score'] = -f['prior_width']  # Fixed quality ranking, never an outcome.
    n = len(f)
    warmup = max(340, p.ma_length * 10)
    f['ready'] = np.arange(n) >= warmup
    raw = np.zeros(n, bool); signal = np.zeros(n, bool)
    runs = np.zeros(n, int); qualified_out = np.zeros(n, bool)
    bands = np.full(n, np.nan); zone_high = np.full(n, np.nan); zone_low = np.full(n, np.nan)
    run, qualified, band, top, bottom = 0, False, np.nan, np.nan, np.nan
    for i in range(warmup, n):
        magnitude = max(abs(md[i]), abs(sb[i]))
        candidate = p.band_atr * atr[i-1]
        if qualified:
            if magnitude <= band:
                run += 1; top = max(top,h[i]); bottom = min(bottom,l[i])
            else:
                raw[i] = md[i] > band
                runs[i], bands[i], zone_high[i], zone_low[i] = run, band, top, bottom
                signal[i] = raw[i] and (p.quality == 0 or (c[i] > f['rope_high'].iat[i] and f['prior_width'].iat[i] <= 3.))
                if p.quality == 2:
                    signal[i] &= c[i] > top
                run, qualified, band, top, bottom = 0, False, np.nan, np.nan, np.nan
                continue
        elif magnitude <= candidate:
            run += 1; top = h[i] if run == 1 else max(top,h[i]); bottom = l[i] if run == 1 else min(bottom,l[i])
            if run >= p.focus_bars:
                qualified, band = True, candidate
        else:
            run, top, bottom = 0, np.nan, np.nan
        runs[i], qualified_out[i], bands[i], zone_high[i], zone_low[i] = run, qualified, band, top, bottom
    f['release'], f['signal'] = raw, signal & (f['isST'].astype(str) == '0').to_numpy()
    f['focus_count'], f['qualified'], f['frozen_band'] = runs, qualified_out, bands
    f['zone_high'], f['zone_low'] = zone_high, zone_low
    return f


def limit_fraction(board: str, date: str, is_st: bool) -> float:
    board = board.lower()
    if board == 'star' or board == 'chinext' and date >= '2020-08-24':
        return .20
    return .05 if is_st and date < '2026-07-06' else .10


def price_limits(row: dict) -> tuple[float,float]:
    rate = limit_fraction(row['board'], row['date'], str(row['isST']) == '1')
    # Exchange prices round positive halves up to RMB cents.
    ref, fraction = Decimal(str(row['raw_preclose'])), Decimal(str(rate))
    return tuple(float((ref * multiple).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                 for multiple in (1-fraction, 1+fraction))


def buy_quantity(raw_budget: float, raw_risk: float, risk_budget: float, board: str) -> int:
    quantity = int(min(raw_budget, risk_budget / raw_risk)) if raw_risk > 0 else 0
    return (quantity if quantity >= 200 else 0) if board.lower() == 'star' else quantity // 100 * 100


def prepare_books(frames: dict[str,pd.DataFrame], start: str, end: str) -> dict:
    """Reuse per-fold immutable daily records across matched-control replays.

    Only the supplied fold is converted. Callers must rebuild after changing
    features or parameters; simulate never mutates these records.
    """
    return {code: {r['date']:r for r in f.loc[f.date.between(start,end)].to_dict('records')}
            for code,f in frames.items()}


def simulate(frames: dict[str,pd.DataFrame], p: Parameters, start: str, end: str,
             costs: Costs = Costs(), signals: Optional[dict[str,set[str]]] = None,
             initial: float = 1_000_000., max_positions: int = 10,
             risk_fraction: float = .0075, allocation_fraction: float = .15,
             prepared: Optional[dict] = None) -> dict:
    """Daily close decisions -> next market-session open; enforce settled shares.

    Input frames contain only causal indicators. Dates from the union of source
    daily rows form exchange sessions; holidays are not fabricated. No close
    signal is filled retrospectively. Entry orders expire after that session.
    Open exits precede open entries; later intraday stops cannot fund earlier
    purchases. Stops/trails use previous-close state; daily OHLC fills remain
    an approximation. Slippage is a separate cash cost, not an impossible
    execution price outside exchange limits. Terminal values use the last
    available close net of estimated exit costs, separately flag stale marks,
    and disclose a zero-recovery stress result for that uncertain exposure.
    """
    books = prepare_books(frames,start,end) if prepared is None else prepared
    dates = sorted({d for rows in books.values() for d in rows if start <= d <= end})
    if not dates:
        raise ValueError('empty replay period')
    cash = initial; positions = {}; pending = {}; trades = []; equity = []; skips = {}
    marks = {}; last_dates = {}

    def skip(reason):
        skips[reason] = skips.get(reason,0)+1

    def mark_equity():
        return cash + sum(pos['units'] * marks[code] for code,pos in positions.items())

    def sell(code, row, adjusted_price, reason, session, *, terminal=False, stale_days=0):
        nonlocal cash
        pos = positions.pop(code)
        gross = pos['units'] * adjusted_price
        fee = costs.fee(gross, row['date'], True) if gross > 0 else 0.
        slippage = gross * costs.slippage
        proceeds = gross-fee-slippage
        cash += proceeds
        pnl = proceeds-pos['spent']
        trades.append(dict(code=code,board=pos['board'],signal_date=pos['signal_date'],entry_date=pos['entry_date'],
                           exit_date=row['date'],entry=pos['entry'],exit=adjusted_price,stop=pos['initial_stop'],
                           risk=pos['risk'],quantity=pos['quantity'],risk_cash=pos['units']*pos['risk'],
                           return_gross=gross/pos['notional']-1,return_net=pnl/pos['spent'],
                           pnl_gross=gross-pos['notional'],pnl=pnl,
                           entry_notional=pos['notional'],exit_notional=gross,
                           entry_fee=pos['entry_fee'],exit_fee=fee,fees=pos['entry_fee']+fee,
                           entry_slippage=pos['entry_slippage'],exit_slippage=slippage,
                           slippage=pos['entry_slippage']+slippage,
                           reason=reason,holding_days=session-pos['session'],
                           peak_r=(pos['best_close']-pos['entry'])/pos['risk'],score=pos['score'],
                           atr_pct=pos['atr_pct'],vol_bucket=pos['vol_bucket'],
                           is_terminal=terminal,stale_days=stale_days,
                           terminal_uncertain=terminal and stale_days>0))
        return proceeds

    for session,date in enumerate(dates):
        today = {code:rows[date] for code,rows in books.items() if date in rows}
        # Mark overnight gaps before allocating; exits release cash at open.
        for code,row in today.items():
            marks[code] = row['open']; last_dates[code] = date
        for code in list(positions):
            pos = positions[code]; row = today.get(code)
            if row is None:
                continue
            lower,_ = price_limits(row)
            blocked = row['raw_open'] <= lower + .005
            if pos['exit_pending']:
                if blocked:
                    skip('exit_open_limit_down'); continue
                sell(code,row,row['open'],pos['exit_pending'],session)
            elif session > pos['session'] and row['open'] <= pos['initial_stop']:
                if blocked:
                    pos['exit_pending'] = 'stop_delayed_limit_down'; skip('stop_open_limit_down')
                else:
                    sell(code,row,row['open'],'initial_stop',session)

        # Unfilled purchase instructions are valid only at this next open.
        for code,signal in sorted(pending.items()):
            row = today.get(code)
            if row is None:
                skip('entry_suspended_or_missing'); continue
            if code in positions or len(positions) >= max_positions:
                skip('position_capacity'); continue
            _,upper = price_limits(row)
            if str(row['isST']) != '0' or row['raw_open'] >= upper-.005:
                skip('entry_st_or_limit_up'); continue
            base_stop = signal['structure_stop']
            if not np.isfinite(base_stop) or base_stop <= 0 or row['open'] <= base_stop:
                skip('entry_gap_invalidates_structure'); continue
            fill = row['open']
            factor = row['raw_open']/row['open']
            raw_stop = Decimal(str(min(base_stop,fill-p.stop_atr*signal['atr'])*factor))
            stop = float(raw_stop.quantize(Decimal('.01'),rounding=ROUND_FLOOR))/factor
            risk = fill-stop
            if stop <= 0 or risk <= 0:
                skip('invalid_risk'); continue
            wealth = mark_equity()
            amount = min(cash,wealth*allocation_fraction)
            raw_fill = fill*factor
            quantity = buy_quantity(amount/raw_fill,risk*factor,wealth*risk_fraction,row['board'])
            decrement = 1 if row['board'].lower()=='star' else 100
            minimum = 200 if row['board'].lower()=='star' else 100
            while quantity >= minimum and (quantity*raw_fill*(1+costs.slippage)
                                            +costs.fee(quantity*raw_fill,date,False)>amount):
                quantity -= decrement
            if quantity < minimum:
                skip('lot_or_cash'); continue
            notional = quantity*raw_fill
            entry_fee = costs.fee(notional,date,False)
            entry_slippage = notional*costs.slippage
            spent = notional+entry_fee+entry_slippage
            cash -= spent
            positions[code] = dict(units=quantity*factor,quantity=quantity,spent=spent,entry=fill,
                                   notional=notional,entry_fee=entry_fee,entry_slippage=entry_slippage,
                                   initial_stop=stop,risk=risk,best_close=fill,trail=stop,
                                   trail_active=False,exit_pending='',session=session,entry_date=date,
                                   signal_date=signal['date'],board=row['board'].lower(),score=signal['score'],
                                   atr_pct=signal['atr_pct'],vol_bucket=int(signal['vol_bucket']))
            if row['low'] <= stop:
                positions[code]['exit_pending'] = 'entry_day_stop_T1'
                skip('entry_day_stop_waits_T1')
        pending = {}

        # These prices occur after the open allocation above. In particular,
        # later stop proceeds and freed position slots cannot finance it.
        for code in list(positions):
            pos = positions[code]; row = today.get(code)
            if row is None or session <= pos['session'] or pos['exit_pending']:
                continue
            if row['low'] <= pos['initial_stop']:
                lower,_ = price_limits(row)
                raw_stop = pos['initial_stop'] * row['raw_open']/row['open']
                if row['raw_open'] <= lower+.005 or raw_stop <= lower+.005:
                    pos['exit_pending'] = 'stop_delayed_limit_down'; skip('stop_limit_down')
                else:
                    sell(code,row,pos['initial_stop'],'initial_stop',session)

        # At close test yesterday's active trail, THEN ratchet today's level.
        for code,pos in positions.items():
            row = today.get(code)
            if row is None:
                continue
            if not pos['exit_pending'] and pos['trail_active'] and row['close'] <= pos['trail']:
                pos['exit_pending'] = 'trend_close_exit'
            pos['best_close'] = max(pos['best_close'],row['close'])
            if not pos['exit_pending'] and pos['best_close'] >= pos['entry']+p.trail_activation_r*pos['risk']:
                pos['trail_active'] = True
                candidate = pos['best_close']-p.trail_atr*row['atr']
                if candidate < row['close']:
                    pos['trail'] = max(pos['trail'],pos['initial_stop'],candidate)
        for code,row in today.items():
            marks[code] = row['close']
            is_signal = bool(row['signal']) if signals is None else date in signals.get(code,set())
            if code not in positions and is_signal and row.get('ready', True) and str(row['isST'])=='0' and np.isfinite(row['atr']) and np.isfinite(row['structure_stop']):
                pending[code] = row
        equity.append(dict(date=date,equity=mark_equity(),cash=cash,positions=len(positions)))
        if cash < -.001:
            raise AssertionError('portfolio borrowed cash')

    # A final liquidation valuation is separate from natural exit statistics.
    final_positions = len(positions)
    uncertain_proceeds = 0.
    for code in list(positions):
        row = dict(books[code][last_dates[code]],date=dates[-1])
        stale_days = (pd.Timestamp(dates[-1])-pd.Timestamp(last_dates[code])).days
        proceeds = sell(code,row,marks[code],
                        'terminal_stale_valuation' if stale_days else 'period_end_valuation',
                        len(dates)-1,terminal=True,stale_days=stale_days)
        if stale_days:
            uncertain_proceeds += proceeds
    equity[-1]['equity'] = cash
    equity[-1]['cash'] = cash; equity[-1]['positions'] = 0
    e = pd.DataFrame(equity)
    metrics = summarize(e,trades,initial)
    stress_equity = e.copy()
    stress_equity.loc[stress_equity.index[-1],['equity','cash']] = cash-uncertain_proceeds
    stress_peak = np.maximum.accumulate(np.r_[initial,stress_equity['equity']])[1:]
    metrics['zero_recovery_net_return'] = float((cash-uncertain_proceeds)/initial-1)
    metrics['zero_recovery_max_drawdown'] = float(np.max(1-stress_equity['equity'].to_numpy()/stress_peak))
    metrics['terminal_uncertain_value'] = float(uncertain_proceeds)
    metrics['open_at_end'] = final_positions
    metrics['signal_count'] = sum(int(f.loc[f['date'].between(start,end),'signal'].sum()) for f in frames.values()) if signals is None else sum(sum(start<=d<=end for d in ds) for ds in signals.values())
    metrics['skips'] = skips
    return dict(metrics=metrics,equity=e,trades=pd.DataFrame(trades),parameters=asdict(p),
                zero_recovery_equity=stress_equity)


def summarize(equity, trades, initial):
    arr = equity['equity'].to_numpy()
    peak = np.maximum.accumulate(np.r_[initial,arr])[1:]
    natural = [t for t in trades if not t.get('is_terminal',False)
               and t['reason'] != 'period_end_valuation' and not t['reason'].startswith('terminal_')]
    pnl = np.array([t['pnl'] for t in natural])
    days = max(1,(pd.Timestamp(equity.date.iloc[-1])-pd.Timestamp(equity.date.iloc[0])).days)
    loss = -pnl[pnl<0].sum()
    return dict(net_return=float(arr[-1]/initial-1),cagr=float((max(arr[-1],.01)/initial)**(365.25/days)-1),
                max_drawdown=float(np.max(1-arr/peak)),trades=len(natural),
                win_rate=float(np.mean(pnl>0)) if len(pnl) else None,
                profit_factor=float(pnl[pnl>0].sum()/loss) if loss>0 else None,
                mean_holding_days=float(np.mean([t['holding_days'] for t in natural])) if natural else None,
                unpriceable=sum(bool(t.get('terminal_uncertain',False)) for t in trades),
                terminal_uncertain=sum(bool(t.get('terminal_uncertain',False)) for t in trades),
                terminal_stale_days_max=max((t.get('stale_days',0) for t in trades),default=0),
                fees=float(sum(t.get('fees',0.) for t in trades)),
                slippage=float(sum(t.get('slippage',0.) for t in trades)),
                exposure=float(np.mean(1-equity['cash']/equity['equity'])))


def randomized_signals(frames, start, end, seed):
    """Permute event timing within stock/quarter/prior120 ATR-volatility bucket.

    Random draws are controls, never the parameter/time split. Eligibility
    uses current isST and preceding history only. No future outcome is used.
    """
    rng = np.random.default_rng(seed); result = {}
    for code,f in sorted(frames.items()):
        block = f.loc[f.date.between(start,end)&f.ready&(f.isST.astype(str)=='0')&f.prior_width.notna()].copy()
        block['quarter'] = pd.to_datetime(block.date).dt.to_period('Q').astype(str)
        selected = set()
        for _,group in block.groupby(['quarter','vol_bucket'],sort=True):
            count = int(group.signal.sum())
            if count:
                selected.update(rng.choice(group.date.to_numpy(),size=count,replace=False).tolist())
        result[code] = selected
    return result
