"""Causal, long-only Donchian / directional EWMAC research accounting.

This deliberately isolated research engine implements the frozen 2026-09-10
experiment contract. It neither imports execution layers nor reads data. EWMAC
is a direction-only adaptation of Carver's rule, not his complete forecast-sized
portfolio. Fees are 0.1% of entry notional on each side, including forced exits;
funding and additional slippage are not modelled. Initial stops are fixed at two
signal-bar ATRs. No profit target, pyramiding, or time-based holding cap exists.

All input timestamps identify UTC 4H bar OPENs. A feature on row i is available
at that row's close, so an event can enter only at row i+1's open. Event studies
can overlap; ``portfolio_from_events`` explicitly rejects overlapping positions.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


BAR = pd.Timedelta(hours=4)
FEE_PER_SIDE = 0.001
EVENT_COLUMNS = [
    "signal_i", "entry_i", "exit_i", "signal_time", "entry_time", "exit_time",
    "entry_price", "exit_price", "signal_atr", "stop_price", "initial_risk_frac",
    "exit_at_open", "exit_reason", "valid", "natural_exit", "censored",
    "gross_return", "net_return", "gross_bp", "net_bp", "mfe_return",
    "mae_return", "capture_ratio", "hold_bars", "hold_hours", "exit_rule",
]


def _validate_bars(bars: pd.DataFrame) -> None:
    """Reject missing bars rather than silently carrying features across gaps."""
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("bars require a timezone-aware UTC DatetimeIndex")
    if str(bars.index.tz) != "UTC":
        raise ValueError("bars index timezone must be UTC")
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("bar timestamps must be unique and increasing")
    timestamps_ns = bars.index.as_unit("ns").asi8
    if len(bars) and ((timestamps_ns % BAR.value) != 0).any():
        raise ValueError("bar opens must align to UTC 00/04/08/12/16/20")
    if len(bars) > 1 and not (np.diff(timestamps_ns) == BAR.value).all():
        raise ValueError("bars must be continuous; split gaps before computing features")
    missing = {"open", "high", "low", "close", "volume"} - set(bars.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    values = bars[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV must be finite")
    if len(values) and ((values[:, :4] <= 0).any() or (values[:, 4] < 0).any()):
        raise ValueError("prices must be positive and volume nonnegative")
    if len(values) and (
        (values[:, 1] < values[:, [0, 2, 3]].max(axis=1)).any()
        or (values[:, 2] > values[:, [0, 1, 3]].min(axis=1)).any()
    ):
        raise ValueError("invalid OHLC range")


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Build close-time causal features from one continuous 4H OHLCV segment.

    Inputs used: high/low/close for ATR14; previous close supplies true-range
    gaps, and the first bar's TR is high-low. ATR is seeded with the arithmetic
    mean of the first 14 TRs, then Wilder recursion (13*previous+TR)/14.
    Donchian uses high of the previous 20 rows and low of the previous 10 rows,
    excluding the signal row. Volume only establishes complete OHLCV input.

    Only UTC days containing all six 4H bars supply daily close features. The
    daily close becomes visible at next UTC midnight, including the 20:00 bar's
    close. EMA8/32 and EMA32/128 use pandas adjust=True; daily close differences'
    EW standard deviation uses span=35, adjust=True, bias=False, min_periods=35.
    Each normalized pair is scaled by 5.3 / 2.65, averaged, then clipped to
    [-20,20]. EMA50/200 background flags use this same complete daily close.
    ``daily_above_ema200`` describes the input instrument: the caller must select
    BTC's feature frame to obtain BTC market background.

    Volatility buckets 0..4 are ceil(5*rank_pct)-1, using the average tie rank of
    the current ATR/close within the last 240 rows including the current row.
    All arms share 256 complete consecutive daily bars of warmup and finite ATR,
    EWMAC, channels, and volatility bucket. No estimated forecast scalar,
    backward fill, or future data enters any feature.
    """
    _validate_bars(bars)
    out = pd.DataFrame(index=bars.index)
    close = bars["close"].astype(float)
    previous = close.shift()
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - previous).abs(),
        (bars["low"] - previous).abs(),
    ], axis=1).max(axis=1).to_numpy(float)
    atr = np.full(len(bars), np.nan)
    if len(bars) >= 14:
        atr[13] = tr[:14].mean()
        for i in range(14, len(bars)):
            atr[i] = (13.0 * atr[i - 1] + tr[i]) / 14.0
    out["atr"] = atr
    out["prior_high20"] = bars["high"].shift().rolling(20).max()
    out["prior_low10"] = bars["low"].shift().rolling(10).min()
    out["donchian_signal"] = close > out["prior_high20"]
    relative_atr = out["atr"] / close
    percentile = relative_atr.rolling(240, min_periods=240).rank(pct=True)
    out["vol_bucket"] = (np.ceil(5.0 * percentile) - 1).clip(0, 4)

    daily_group = bars.resample("1D", closed="left", label="left")
    daily_close = daily_group["close"].last()[daily_group.size().eq(6)].astype(float)
    daily = pd.DataFrame(index=daily_close.index + pd.Timedelta(days=1))
    daily_values = daily_close.reset_index(drop=True)
    vol = daily_values.diff().ewm(span=35, adjust=True, min_periods=35).std(bias=False)
    fast = daily_values.ewm(span=8, adjust=True).mean()
    medium = daily_values.ewm(span=32, adjust=True).mean()
    slow = daily_values.ewm(span=128, adjust=True).mean()
    forecast = (5.3 * (fast - medium) / vol + 2.65 * (medium - slow) / vol) / 2
    daily["ewmac_forecast"] = forecast.where(vol.gt(0)).clip(-20, 20).to_numpy()
    daily["daily_count"] = np.arange(1, len(daily) + 1)
    for span in (50, 200):
        ema = daily_values.ewm(span=span, adjust=True, min_periods=span).mean()
        flag = (daily_values > ema).astype("boolean").mask(ema.isna())
        daily[f"daily_above_ema{span}"] = flag.to_numpy()
    daily["daily_source_close_time"] = daily.index
    mapped = daily.reindex(bars.index + BAR, method="ffill")
    mapped.index = bars.index
    for name in daily.columns:
        out[name] = mapped[name]
    for name in ("daily_above_ema50", "daily_above_ema200"):
        out[name] = out[name].astype("boolean")
    out["ewmac_signal"] = out["ewmac_forecast"] > 0
    finite = np.isfinite(out[["atr", "prior_high20", "prior_low10", "ewmac_forecast", "vol_bucket"]]).all(axis=1)
    out["ready"] = out["daily_count"].ge(256) & finite & out["atr"].gt(0)
    return out


def evaluate_events(
    bars: pd.DataFrame,
    features: pd.DataFrame,
    signal_indexes: Iterable[int],
    first_i: int,
    last_i: int,
    exit_rule: str = "D",
) -> pd.DataFrame:
    """Evaluate independent long events; ``first_i:last_i`` is inclusive.

    ``signal_indexes`` contains positional rows selected by the caller. D exits
    after close < previous 10 lows; D_E and E exit after EWMAC <= 0. The caller
    controls their differing entry rules. All entries occur at next open and use
    a fixed entry-2*signal_ATR stop. At each open, stop gaps have priority over a
    pending rule exit; otherwise pending exits fill at open before intrabar
    stops. A low touching the stop fills at stop. D_E does not veto a Donchian
    entry whose EWMAC is already nonpositive: its first held close can schedule
    an exit. A last-bar close exit is censored unless a stop already exited.

    Excursions include only held bars and the actual exit price. Stop bars use
    only entry/open and stop fill, not that bar's unknown high/low chronology;
    this conservative MFE convention avoids claiming highs reached after exit.
    At-open exits exclude the exit bar's range. Fees total exactly 0.002 of entry
    notional. ``hold_bars`` counts bar intervals held (an intrabar stop counts
    one); intrabar stops use the bar close timestamp for coarse duration only.
    """
    _validate_bars(bars)
    if not features.index.equals(bars.index):
        raise ValueError("features and bars indexes must match exactly")
    if exit_rule not in {"D", "D_E", "E"}:
        raise ValueError("exit_rule must be D, D_E, or E")
    signals = list(signal_indexes)
    if not len(bars):
        if signals:
            raise ValueError("cannot evaluate signals against empty bars")
        return pd.DataFrame(columns=EVENT_COLUMNS)
    if not (0 <= first_i <= last_i < len(bars)):
        raise ValueError("invalid inclusive fold boundaries")
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = features["atr"].to_numpy(float)
    ready = features["ready"].fillna(False).to_numpy(bool)
    channel = features["prior_low10"].to_numpy(float)
    forecast = features["ewmac_forecast"].to_numpy(float)
    rows = []
    for supplied in signals:
        if isinstance(supplied, (bool, np.bool_)) or not isinstance(supplied, (int, np.integer)):
            raise ValueError("signal_indexes must contain integer positions")
        signal = int(supplied)
        entry = signal + 1
        row = dict.fromkeys(EVENT_COLUMNS, np.nan)
        row.update(signal_i=signal, entry_i=entry, exit_i=-1, exit_rule=exit_rule,
                   valid=False, natural_exit=False, censored=False, exit_at_open=False)
        if signal < first_i or signal > last_i:
            row["exit_reason"] = "signal_outside_fold"
            rows.append(row)
            continue
        row["signal_time"] = bars.index[signal] + BAR
        if entry > last_i:
            row["exit_reason"] = "no_next_open_in_fold"
            rows.append(row)
            continue
        if not ready[signal] or not np.isfinite(atr[signal]) or atr[signal] <= 0:
            row["exit_reason"] = "features_not_ready"
            rows.append(row)
            continue
        price = o[entry]
        stop = price - 2.0 * atr[signal]
        if not np.isfinite(stop) or stop <= 0:
            row["exit_reason"] = "nonpositive_stop"
            rows.append(row)
            continue
        pending = False
        maximum, minimum = price, price
        for i in range(entry, last_i + 1):
            maximum, minimum = max(maximum, o[i]), min(minimum, o[i])
            at_open = False
            if o[i] <= stop:
                fill, reason, at_open = o[i], "stop_gap", True
            elif pending:
                fill, reason, at_open = o[i], "donchian_exit" if exit_rule == "D" else "ewmac_exit", True
            elif lo[i] <= stop:
                fill, reason = stop, "initial_stop"
            else:
                maximum, minimum = max(maximum, h[i]), min(minimum, lo[i])
                if i == last_i:
                    fill, reason = c[i], "fold_boundary"
                else:
                    if exit_rule == "D":
                        pending = np.isfinite(channel[i]) and c[i] < channel[i]
                    else:
                        pending = np.isfinite(forecast[i]) and forecast[i] <= 0
                    continue
            maximum, minimum = max(maximum, fill), min(minimum, fill)
            gross = fill / price - 1.0
            net = gross - 2.0 * FEE_PER_SIDE
            mfe, mae = maximum / price - 1.0, minimum / price - 1.0
            censored = reason == "fold_boundary"
            exit_time = bars.index[i] + (pd.Timedelta(0) if at_open else BAR)
            row.update(
                entry_time=bars.index[entry], exit_time=exit_time,
                entry_price=price, exit_price=fill, signal_atr=atr[signal],
                stop_price=stop, initial_risk_frac=2.0 * atr[signal] / price,
                exit_i=i, exit_at_open=at_open, exit_reason=reason, valid=True,
                natural_exit=not censored, censored=censored,
                gross_return=gross, net_return=net, gross_bp=1e4*gross,
                net_bp=1e4*net, mfe_return=mfe, mae_return=mae,
                capture_ratio=net/mfe if mfe > 0 else np.nan,
                hold_bars=i-entry+int(not at_open),
                hold_hours=float((exit_time-bars.index[entry])/pd.Timedelta(hours=1)),
            )
            break
        rows.append(row)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def portfolio_from_events(
    bars: pd.DataFrame,
    events: pd.DataFrame,
    first_i: int,
    last_i: int,
    risk_fraction: float = 0.01,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay one cash-funded instrument, starting each fold at equity 1.

    Entry fraction is min(1, risk_fraction/initial_risk_frac, 1/(1+entry_fee)).
    Quantity is then held unchanged, without rebalancing. Both side fees use
    that original entry notional. Close equity includes cash plus held quantity
    times close. Exposure therefore respects available cash, without borrowing.
    Stops and event exits occur before close marking. No entry is accepted on
    the same bar as an existing position's exit, including an at-open exit.

    Every supplied event remains in the returned trades ledger, with accepted
    and rejection_reason fields. Zero-size and invalid candidates never trade.
    A fold starts flat and must end flat through the event's censored exit.
    """
    _validate_bars(bars)
    if not np.isfinite(risk_fraction) or risk_fraction <= 0 or risk_fraction > 1:
        raise ValueError("risk_fraction must be finite and in (0, 1]")
    columns = ["equity", "exposure", "notional", "cash", "size", "entry_fee", "exit_fee"]
    ledger = events.copy().reset_index(drop=True)
    ledger["accepted"] = False
    ledger["rejection_reason"] = ""
    for name in ("allocated_fraction", "size", "entry_notional", "entry_equity", "entry_fee",
                 "exit_fee", "pnl", "realized_return_on_equity", "initial_risk_money"):
        ledger[name] = np.nan
    if not len(bars):
        if len(events):
            raise ValueError("cannot replay events against empty bars")
        return pd.DataFrame(columns=columns, index=bars.index), ledger
    if not (0 <= first_i <= last_i < len(bars)):
        raise ValueError("invalid inclusive fold boundaries")
    selected = []
    prior_exit = first_i - 1
    if len(ledger):
        for k in ledger.sort_values(["entry_i", "signal_i"], kind="stable").index:
            event = ledger.loc[k]
            if not bool(event["valid"]):
                ledger.loc[k, "rejection_reason"] = "invalid_event"
                continue
            entry, end = int(event["entry_i"]), int(event["exit_i"])
            if entry < first_i or end > last_i or entry > end:
                ledger.loc[k, "rejection_reason"] = "event_outside_fold"
                continue
            if entry <= prior_exit:
                ledger.loc[k, "rejection_reason"] = "overlapping_position_or_same_exit_bar"
                continue
            risk = float(event["initial_risk_frac"])
            if not np.isfinite(risk) or risk <= 0:
                ledger.loc[k, "rejection_reason"] = "invalid_initial_risk"
                continue
            selected.append(k)
            prior_exit = end
            ledger.loc[k, "accepted"] = True
    entries = {int(ledger.loc[k, "entry_i"]): k for k in selected}
    cash, quantity = 1.0, 0.0
    current = None
    marks = []
    closes = bars["close"].to_numpy(float)
    for i in range(first_i, last_i + 1):
        entry_fee = exit_fee = 0.0
        if i in entries:
            current = entries[i]
            event = ledger.loc[current]
            fraction = min(1.0, risk_fraction/float(event["initial_risk_frac"]), 1.0/(1.0+FEE_PER_SIDE))
            entry_equity = cash
            notional = entry_equity * fraction
            quantity = notional / float(event["entry_price"])
            entry_fee = notional * FEE_PER_SIDE
            cash -= notional + entry_fee
            # Numerical cancellation at the fee-constrained cash limit is zero.
            if abs(cash) < 1e-14:
                cash = 0.0
            ledger.loc[current, ["allocated_fraction", "size", "entry_notional", "entry_equity",
                                 "entry_fee", "exit_fee", "initial_risk_money"]] = [
                fraction, quantity, notional, entry_equity, entry_fee, notional*FEE_PER_SIDE,
                notional*float(event["initial_risk_frac"]),
            ]
        if current is not None and int(ledger.loc[current, "exit_i"]) == i:
            event = ledger.loc[current]
            original_notional = float(event["entry_notional"])
            exit_fee = original_notional * FEE_PER_SIDE
            proceeds = quantity * float(event["exit_price"])
            cash += proceeds - exit_fee
            pnl = proceeds - original_notional - float(event["entry_fee"]) - exit_fee
            ledger.loc[current, "pnl"] = pnl
            ledger.loc[current, "realized_return_on_equity"] = pnl / float(event["entry_equity"])
            quantity, current = 0.0, None
        notional = quantity * closes[i]
        equity = cash + notional
        if cash < -1e-12 or equity <= 0:
            raise ArithmeticError("cash-funded long portfolio became insolvent")
        marks.append([equity, notional/equity, notional, cash, quantity, entry_fee, exit_fee])
    return pd.DataFrame(marks, index=bars.index[first_i:last_i+1], columns=columns), ledger
