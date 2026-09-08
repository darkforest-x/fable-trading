"""Causal event outcomes and one-instrument, unlevered research accounting.

Decision contract: exp-imacd-altcoin-trends-20260909-v1/PROJECT_PLAN.md.
Input bars have a strictly ordered UTC DatetimeIndex of OPEN times and finite
positive open/high/low/close columns. Features have the identical index: atr at
the signal close sets the immutable initial risk; md/sma60 at each later close
can request a next-open exit; chandelier uses held-bar high/low and that close's
atr, effective only on the NEXT bar. No future data are used for decisions.

An event enters next open, risks 2 signal ATR by default, and always retains its
protective stop. Same-bar stop/TP ambiguity goes to the stop. Stop gaps fill at
the less favorable open; fixed TP gaps conservatively receive only the target.
An intrabar exit's unknown remaining high/low is excluded from MFE/MAE: these
are evidenced path bounds, not guessed within-bar chronology. A time-limit or
fold-close mark includes its complete held bar. Boundary marks are censored.

Costs: 10 bp on ENTRY notional at each side, 20 bp total, no funding, spread,
slippage beyond explicit stop gaps, liquidation or market-impact model. The
portfolio uses one position per instrument with entry notional equal to its
pre-entry sleeve equity, no reinvestment until the position exits, and no new
entry after nonpositive equity. Returned close equity includes entry fees while
open. Its adverse stress series uses full OHLC extrema while a position was
present and may overstate excursions after an intrabar exit; it is not a traded
or synchronized cross-instrument path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


FEE_SIDE = 0.001
EXIT_RULES = {"fixed3r", "md", "chandelier", "sma60"}
EVENT_COLUMNS = [
    "event_id", "signal_i", "side", "signal_time", "decision_time", "signal_atr",
    "entry_i", "entry_time", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac", "target_price", "target_unreachable",
    "exit_i", "exit_time", "exit_price", "exit_at_open", "exit_timing", "exit_reason", "reason", "exit_rule",
    "valid", "invalid_reason", "natural_exit", "censored", "hold_bars", "hold_seconds",
    "gross_return", "fee_return", "net_return", "gross_bp", "net_bp", "gross_r", "net_r", "mfe_return", "mae_return",
    "mfe_r", "mae_r", "capture_ratio", "period_seconds",
]


@dataclass
class _Inputs:
    bars: pd.DataFrame
    prices: np.ndarray
    times: np.ndarray
    period_ns: int
    gaps: np.ndarray


def _inputs(bars: pd.DataFrame, first_i: int, last_i: int) -> _Inputs:
    if not isinstance(bars, pd.DataFrame) or not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars require a UTC DatetimeIndex of bar OPEN times")
    if str(bars.index.tz) not in {"UTC", "utc", "GMT", "Etc/UTC", "Etc/GMT", "UTC+00:00"}:
        raise ValueError("bars index must explicitly use UTC")
    if len(bars) < 2 or not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError("bars need at least two strictly increasing, unique timestamps")
    if isinstance(first_i, bool) or isinstance(last_i, bool) or int(first_i) != first_i or int(last_i) != last_i:
        raise ValueError("fold positions must be integers")
    if not 0 <= first_i <= last_i < len(bars):
        raise ValueError("invalid inclusive first_i/last_i")
    required = ["open", "high", "low", "close"]
    if any(name not in bars for name in required):
        raise ValueError("bars require open/high/low/close")
    prices = bars[required].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC must be finite and positive")
    o, h, l, c = prices.T
    if (h < np.maximum(o, c)).any() or (l > np.minimum(o, c)).any() or (h < l).any():
        raise ValueError("inconsistent OHLC bounds")
    # pandas 2.x preserves datetime64[ms/us] resolution in asi8. Normalize
    # explicitly before nanosecond grid, gap and holding-deadline arithmetic.
    times = bars.index.as_unit("ns").asi8
    differences = np.diff(times)
    # Period is an input contract, never a statistic chosen from future gaps.
    if "period_seconds" in bars.attrs:
        period_ns = int(bars.attrs["period_seconds"] * 1_000_000_000)
    elif bars.index.freq is not None:
        period_ns = int(bars.index.freq.nanos)
    else:
        period_ns = int(differences[0])
    if period_ns not in {900_000_000_000, 3_600_000_000_000, 14_400_000_000_000}:
        raise ValueError("only 15m, 1H and 4H bars are supported; set attrs['period_seconds'] if needed")
    if (times % period_ns).any() or (differences % period_ns).any():
        raise ValueError("bar timestamps must lie on the UTC period grid")
    return _Inputs(bars, prices, times, period_ns, differences != period_ns)


def _rule(value: str) -> str:
    value = value.lower()
    aliases = {"3r": "fixed3r", "fixed_3r": "fixed3r", "fixed": "fixed3r", "tp": "fixed3r", "sma": "sma60"}
    value = aliases.get(value, value)
    if value not in EXIT_RULES:
        raise ValueError("exit_rule must be fixed3r, md, chandelier or sma60")
    return value


def _event_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    for col in ["signal_time", "decision_time", "entry_time", "exit_time"]:
        frame[col] = pd.to_datetime(frame[col], utc=True).dt.as_unit("ns")
    frame["reason"] = frame["exit_reason"].where(frame["valid"].eq(True), frame["invalid_reason"])
    frame["gross_bp"] = pd.to_numeric(frame["gross_return"], errors="coerce") * 10_000
    frame["net_bp"] = pd.to_numeric(frame["net_return"], errors="coerce") * 10_000
    return frame


def evaluate_events(bars: pd.DataFrame, features: pd.DataFrame, signal_indexes, sides, *,
                    first_i: int, last_i: int, exit_rule: str = "md", stop_atr: float = 2,
                    trail_atr: float = 3, tp_r: float = 3, max_hold_days: float = 30) -> pd.DataFrame:
    """Evaluate independent candidates; this does NOT impose portfolio overlap.

    Fold positions include both ends. Signal and next-open entry must both be
    inside the fold. Invalid risk, missing required feature values, a traversed
    bar gap, or unavailable entry produce explicit valid=False rows. OHLC/index
    schema errors reject the call. No filtering silently removes candidates.
    signal_indexes are integer positions; sides are +1/-1. Duplicate candidates
    are allowed for event studies, but the portfolio deterministically accepts
    only one candidate at each available entry open.
    Intrabar exits use the enclosing close for exit_time/hold_seconds, an upper
    bound on duration; exit_timing='intrabar_unknown' preserves this limitation.
    """
    data = _inputs(bars, first_i, last_i)
    rule = _rule(exit_rule)
    for name, value in [("stop_atr", stop_atr), ("trail_atr", trail_atr), ("tp_r", tp_r), ("max_hold_days", max_hold_days)]:
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be finite and positive")
    if not isinstance(features, pd.DataFrame) or not features.index.equals(bars.index):
        raise ValueError("features must have the identical bars index")
    names = {"atr"} | ({"md"} if rule == "md" else {"sma60"} if rule == "sma60" else set())
    if any(name not in features for name in names):
        raise ValueError("features require " + ", ".join(sorted(names)))
    values = {name: features[name].to_numpy(dtype=float) for name in names}
    signals = list(signal_indexes)
    directions = list(sides)
    if len(signals) != len(directions):
        raise ValueError("signal_indexes and sides must have equal lengths")
    if any(isinstance(i, (bool, np.bool_)) or not isinstance(i, (int, np.integer)) for i in signals):
        raise ValueError("signal_indexes must be integer positions")
    if any(isinstance(s, (bool, np.bool_)) or s not in (-1, 1) for s in directions):
        raise ValueError("sides must be +1 or -1")
    hold_ns = int(max_hold_days * 86_400_000_000_000)
    if hold_ns % data.period_ns:
        raise ValueError("max_hold_days must correspond to a whole number of bars")
    rows = []
    for event_id, (signal_i, side) in enumerate(zip(signals, directions)):
        i, side = int(signal_i), int(side)
        row = {name: np.nan for name in EVENT_COLUMNS}
        row.update(event_id=event_id, signal_i=i, side=side, exit_rule=rule, valid=False,
                   invalid_reason="", natural_exit=False, censored=False, exit_at_open=False,
                   target_unreachable=False,
                   exit_reason="", exit_timing="", fee_return=2 * FEE_SIDE,
                   period_seconds=data.period_ns / 1e9)
        if not first_i <= i <= last_i:
            row["invalid_reason"] = "signal_outside_fold"
            rows.append(row)
            continue
        row.update(signal_time=bars.index[i], decision_time=bars.index[i] + pd.Timedelta(data.period_ns, unit="ns"))
        entry_i = i + 1
        if entry_i > last_i:
            row["invalid_reason"] = "no_entry_bar"
            rows.append(row)
            continue
        if data.gaps[i]:
            row["invalid_reason"] = "gap_before_entry"
            rows.append(row)
            continue
        entry = float(data.prices[entry_i, 0])
        signal_atr = float(values["atr"][i])
        risk = stop_atr * signal_atr
        row.update(entry_i=entry_i, entry_time=bars.index[entry_i], entry_price=entry,
                   signal_atr=signal_atr, initial_risk=risk)
        if not math.isfinite(risk) or not 0 < risk < entry:
            row["invalid_reason"] = "invalid_initial_risk"
            rows.append(row)
            continue
        initial_stop = entry - side * risk
        target = entry + side * risk * tp_r
        if rule == "fixed3r" and not math.isfinite(target):
            row["invalid_reason"] = "nonfinite_target"
            rows.append(row)
            continue
        # A negative short TP is unreachable, not grounds for deleting the
        # candidate from just one exit arm. Protective/time/fold exits remain.
        row.update(initial_stop=initial_stop, initial_risk_frac=risk / entry,
                   target_price=target if rule == "fixed3r" else np.nan,
                   target_unreachable=rule == "fixed3r" and target <= 0)
        effective_stop = initial_stop
        running_high = entry
        running_low = entry
        mfe = 0.0
        mae = 0.0
        pending_reason = None
        deadline = int(data.times[entry_i]) + hold_ns
        exit_i, exit_price, reason, at_open, timing = None, None, None, False, None
        for j in range(entry_i, last_i + 1):
            if j > entry_i and data.gaps[j - 1]:
                row["invalid_reason"] = "gap_during_holding"
                break
            o, h, l, c = data.prices[j]
            # All levels here became effective before this bar opened.
            gap_stop = o <= effective_stop if side == 1 else o >= effective_stop
            if gap_stop:
                exit_i, exit_price, at_open, timing = j, float(o), True, "open"
                reason = "stop_gap" if effective_stop == initial_stop else "chandelier_gap"
                break
            if pending_reason is not None:
                exit_i, exit_price, reason, at_open, timing = j, float(o), pending_reason, True, "open"
                break
            gap_tp = rule == "fixed3r" and (o >= target if side == 1 else o <= target)
            if gap_tp:
                exit_i, exit_price, reason, at_open, timing = j, target, "take_profit_gap", True, "open"
                break
            hit_stop = l <= effective_stop if side == 1 else h >= effective_stop
            if hit_stop:
                exit_i, exit_price, timing = j, effective_stop, "intrabar_unknown"
                reason = "initial_stop" if effective_stop == initial_stop else "chandelier_stop"
                break
            hit_tp = rule == "fixed3r" and (h >= target if side == 1 else l <= target)
            if hit_tp:
                exit_i, exit_price, reason, timing = j, target, "take_profit", "intrabar_unknown"
                break
            # This complete bar was definitely held; both extrema are admissible.
            mfe = max(mfe, side * ((h if side == 1 else l) - entry) / entry)
            mae = max(mae, -side * ((l if side == 1 else h) - entry) / entry)
            close_time = int(data.times[j]) + data.period_ns
            if close_time >= deadline:
                exit_i, exit_price, reason, timing = j, float(c), "max_hold", "close"
                break
            if j == last_i:
                exit_i, exit_price, reason, timing = j, float(c), "boundary_mark", "close"
                break
            if rule == "md":
                md = float(values["md"][j])
                if not math.isfinite(md):
                    row["invalid_reason"] = "missing_md_while_held"
                    break
                if md * side <= 0:
                    pending_reason = "md"
            elif rule == "sma60":
                ma = float(values["sma60"][j])
                if not math.isfinite(ma) or ma <= 0:
                    row["invalid_reason"] = "missing_sma60_while_held"
                    break
                if side * (c - ma) < 0:
                    pending_reason = "sma60"
            elif rule == "chandelier":
                atr = float(values["atr"][j])
                if not math.isfinite(atr) or atr <= 0:
                    row["invalid_reason"] = "missing_atr_while_held"
                    break
                running_high = max(running_high, float(h))
                running_low = min(running_low, float(l))
                proposed = running_high - trail_atr * atr if side == 1 else running_low + trail_atr * atr
                effective_stop = max(effective_stop, proposed) if side == 1 else min(effective_stop, proposed)
        if row["invalid_reason"]:
            rows.append(row)
            continue
        gross = side * (exit_price / entry - 1)
        # Known exit is part of the path; unknown exit-bar extremes are not.
        mfe = max(mfe, gross, 0.0)
        mae = max(mae, -gross, 0.0)
        exit_ns = int(data.times[exit_i]) + (0 if at_open else data.period_ns)
        net = gross - 2 * FEE_SIDE
        risk_frac = risk / entry
        row.update(valid=True, exit_i=exit_i, exit_time=pd.Timestamp(exit_ns, tz="UTC"),
                   exit_price=exit_price, exit_at_open=at_open, exit_reason=reason, exit_timing=timing,
                   natural_exit=reason != "boundary_mark", censored=reason == "boundary_mark",
                   hold_bars=exit_i - entry_i + (0 if at_open else 1),
                   hold_seconds=(exit_ns - int(data.times[entry_i])) / 1e9,
                   gross_return=gross, net_return=net, gross_r=gross / risk_frac, net_r=net / risk_frac,
                   mfe_return=mfe, mae_return=mae, mfe_r=mfe / risk_frac, mae_r=mae / risk_frac,
                   capture_ratio=gross / mfe if mfe > 0 else np.nan)
        rows.append(row)
    return _event_frame(rows)


def compound_portfolio(bars: pd.DataFrame, events: pd.DataFrame, first_i: int, last_i: int):
    """Return (close_equity, selected_events, diagnostics) for one 1x sleeve.

    Equity starts at 1.0. Signals enter only at their recorded entry open. An old
    position exiting at that open can release its equity for another candidate;
    an intrabar/close exit cannot. Same-open candidates use stable input order.
    Boundary marks liquidate for comparable fold accounting and pay the same
    fixed entry-notional exit fee; they remain explicitly censored in results.
    diagnostics['adverse_equity'] is a separate conservative OHLC stress bound.
    """
    data = _inputs(bars, first_i, last_i)
    required = {"valid", "entry_i", "exit_i", "entry_price", "exit_price", "exit_at_open", "side", "signal_i", "net_return"}
    if not required.issubset(events.columns):
        raise ValueError("events missing accounting columns")
    candidates = []
    rejected_invalid = 0
    for sequence, (_, source) in enumerate(events.iterrows()):
        if not isinstance(source["valid"], (bool, np.bool_)):
            raise ValueError("event valid flag must be boolean")
        if not bool(source["valid"]):
            rejected_invalid += 1
            continue
        row = source.to_dict()
        entry_i, exit_i = int(row["entry_i"]), int(row["exit_i"])
        if entry_i != row["entry_i"] or exit_i != row["exit_i"] or not first_i <= entry_i <= exit_i <= last_i:
            raise ValueError("event positions are invalid or outside the fold")
        if int(row["signal_i"]) != entry_i - 1 or row["side"] not in (-1, 1):
            raise ValueError("event must enter the next open after its signal")
        if not isinstance(row["exit_at_open"], (bool, np.bool_)):
            raise ValueError("event exit_at_open must be boolean")
        if row["exit_at_open"] and entry_i == exit_i:
            raise ValueError("a newly entered event cannot already exit at the same open")
        if not math.isclose(float(row["entry_price"]), data.prices[entry_i, 0], rel_tol=1e-12, abs_tol=0):
            raise ValueError("event entry price does not equal its bar open")
        for name in ("entry_price", "exit_price"):
            if not math.isfinite(float(row[name])) or row[name] <= 0:
                raise ValueError("event prices must be finite and positive")
        expected_net = row["side"] * (row["exit_price"] / row["entry_price"] - 1) - 2 * FEE_SIDE
        if not math.isclose(float(row["net_return"]), expected_net, abs_tol=1e-12):
            raise ValueError("event return conflicts with fixed entry-notional fees")
        row["_sequence"] = sequence
        candidates.append(row)
    candidates.sort(key=lambda row: (row["entry_i"], row["_sequence"]))
    by_entry = {}
    for row in candidates:
        by_entry.setdefault(int(row["entry_i"]), []).append(row)
    cash = 1.0
    active = None
    selected = []
    close_values = []
    adverse_values = []
    skipped_overlap = 0
    skipped_nonpositive = 0
    total_fees = 0.0

    def position_value(row, price, exit_fee=False):
        value = row["portfolio_entry_equity"] + row["portfolio_quantity"] * row["side"] * (price - row["entry_price"]) - row["portfolio_entry_fee"]
        return value - (row["portfolio_exit_fee"] if exit_fee else 0.0)

    for j in range(first_i, last_i + 1):
        stress = cash if active is None else position_value(active, data.prices[j, 0])
        if active is not None and int(active["exit_i"]) == j and bool(active["exit_at_open"]):
            cash = position_value(active, active["exit_price"], exit_fee=True)
            active["portfolio_exit_equity"] = cash
            total_fees += active["portfolio_exit_fee"]
            stress = min(stress, cash)
            active = None
        for candidate in by_entry.get(j, []):
            if active is not None:
                skipped_overlap += 1
                continue
            if cash <= 0:
                skipped_nonpositive += 1
                continue
            active = dict(candidate)
            active.pop("_sequence")
            active.update(portfolio_entry_equity=cash, portfolio_entry_notional=cash,
                          portfolio_quantity=cash / active["entry_price"],
                          portfolio_entry_fee=FEE_SIDE * cash, portfolio_exit_fee=FEE_SIDE * cash,
                          portfolio_exit_equity=np.nan)
            total_fees += active["portfolio_entry_fee"]
            selected.append(active)
            stress = min(stress, cash - active["portfolio_entry_fee"])
        if active is not None:
            adverse_price = data.prices[j, 2] if active["side"] == 1 else data.prices[j, 1]
            stress = min(stress, position_value(active, adverse_price))
            if int(active["exit_i"]) == j:
                cash = position_value(active, active["exit_price"], exit_fee=True)
                active["portfolio_exit_equity"] = cash
                total_fees += active["portfolio_exit_fee"]
                stress = min(stress, cash)
                active = None
                current = cash
            else:
                current = position_value(active, data.prices[j, 3])
        else:
            current = cash
        close_values.append(current)
        adverse_values.append(min(stress, current))
    index = bars.index[first_i:last_i + 1].as_unit("ns") + pd.Timedelta(data.period_ns, unit="ns")
    equity = pd.Series(close_values, index=index, name="equity")
    adverse = pd.Series(adverse_values, index=index, name="adverse_equity")
    peaks = np.maximum.accumulate(np.r_[1.0, equity.to_numpy()])[1:]
    drawdowns = equity.to_numpy() / peaks - 1
    stress_drawdowns = adverse.to_numpy() / peaks - 1
    portfolio_columns = ["portfolio_entry_equity", "portfolio_entry_notional", "portfolio_quantity",
                         "portfolio_entry_fee", "portfolio_exit_fee", "portfolio_exit_equity"]
    selected_frame = pd.DataFrame(selected, columns=list(dict.fromkeys(list(events.columns) + portfolio_columns)))
    diagnostics = dict(initial_equity=1.0, final_equity=float(equity.iloc[-1]),
                       net_return=float(equity.iloc[-1] - 1), max_drawdown=float(-drawdowns.min()),
                       adverse_max_drawdown_bound=float(-stress_drawdowns.min()), adverse_equity=adverse,
                       selected_events=len(selected), invalid_events=rejected_invalid,
                       skipped_overlap=skipped_overlap, skipped_nonpositive_equity=skipped_nonpositive,
                       total_fees=total_fees, nonpositive_equity=bool((equity <= 0).any()),
                       cost_model="10bp of entry notional per side; funding/market-impact excluded",
                       adverse_bound_note="Full bar extrema may occur after intrabar exit; not a realized or synchronized path")
    return equity, selected_frame, diagnostics
