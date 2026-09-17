"""Turn the trendline break into trades, and price a take-profit / stop grid.

The owner asked for a V2 that is a strategy with searched TP/SL. The entry is
frozen at whatever `trendline_v2_signals.detect` says -- this study never
touches an indicator parameter, so the one variable that moves is the exit
(CLAUDE.md rule 4).

Execution model, chosen to be pessimistic where the data cannot decide:

  entry      next bar's open after the close-confirmed break, never the break
             bar's own close. A break is only known at that bar's close.
  risk unit  R = sl_mult x ATR(14) at the SIGNAL bar. ATR is causal, so the
             barrier distance is fixed before the trade exists.
  stop       entry - R, filled at min(stop, that bar's open): a gap through the
             stop fills at the gap, not at the stop.
  target     entry + tp_mult x ATR(14) at the signal bar, filled at
             max(target, that bar's open): a limit that gaps in fills better.
  same bar   `adverse_first` resolves a bar touching both barriers as the stop.
             `favorable_first` is the opposite bound; it is reported as a
             sensitivity, never as the headline. Nothing in 15m/1h/4h OHLC can
             say which came first, so the honest answer is the interval.
  time stop  close of the last bar of a fixed `max_hold` window.
  cost       0.1% of executed notional per side, i.e. the project's frozen 0.2%
             round trip. Not a parameter of this study.

Metrics are per signal, not per serial trade. Two reasons: the matched control
is drawn per signal, and a serial "one position at a time" filter makes the set
of taken trades depend on the exit parameters, which is exactly the comparison
the grid is trying to make. The serial equity path is computed separately, for
the selected cells only, as a realizability check.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import itertools
import numpy as np
import pandas as pd

FEE_PER_SIDE = 0.001  # CLAUDE.md's frozen 0.2% round trip. Not searchable.


@dataclass(frozen=True)
class ExitGrid:
    """The searched axis. Multiples of ATR(14) at the signal bar."""

    sl_mults: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
    tp_mults: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0)

    def cells(self):
        return list(itertools.product(self.sl_mults, self.tp_mults))


def causal_volatility_bucket(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Tercile of SMA14 true range / close against its own preceding 120 values.

    Within-symbol and strictly backward-looking: the quantiles are taken from
    values shifted one bar, so the bar being bucketed is not in its own
    reference window. A ranking window that reaches into the window being traded
    is the mistake in
    docs/learnings/symbol-ranking-window-must-end-before-the-trading-window.md.

    Returns (bucket, defined) where `defined` is False during the warmup.
    """
    previous = frame.close.shift()
    tr = pd.concat([frame.high - frame.low,
                    (frame.high - previous).abs(),
                    (frame.low - previous).abs()], axis=1).max(axis=1)
    vol = tr.rolling(14, min_periods=14).mean() / frame.close
    prior = vol.shift().rolling(120, min_periods=120)
    q1, q2 = prior.quantile(1 / 3), prior.quantile(2 / 3)
    bucket = np.where(vol <= q1, 0, np.where(vol <= q2, 1, 2))
    defined = (q1.notna() & q2.notna() & vol.notna()).to_numpy()
    return bucket.astype(np.int64), defined


def barrier_outcomes(opens: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                     signal_i: np.ndarray, atr_at_signal: np.ndarray,
                     sl_mult: float, tp_mult: float, max_hold: int,
                     path: str = "adverse_first", chunk: int = 8192) -> dict:
    """Long-only barrier replay for many signals at once.

    Every signal must already have `signal_i + max_hold` bars available; the
    caller drops the ones that do not, and reports how many, rather than
    censoring them into the statistics.
    """
    if path not in ("adverse_first", "favorable_first"):
        raise ValueError("path must be adverse_first or favorable_first")
    entry_i = signal_i + 1
    entry = opens[entry_i]
    risk = sl_mult * atr_at_signal
    if not np.all(risk > 0):
        raise ValueError("a signal has non-positive risk; ATR warmup leaked in")
    stop = entry - risk
    target = entry + tp_mult * atr_at_signal

    n_events = len(signal_i)
    exit_i = np.empty(n_events, dtype=np.int64)
    exit_price = np.empty(n_events, dtype=float)
    reason = np.empty(n_events, dtype=object)

    high_win = np.lib.stride_tricks.sliding_window_view(high, max_hold)
    low_win = np.lib.stride_tricks.sliding_window_view(low, max_hold)
    for start in range(0, n_events, chunk):
        sl_ = slice(start, min(start + chunk, n_events))
        e = entry_i[sl_]
        run_high = np.maximum.accumulate(high_win[e], axis=1)
        run_low = np.minimum.accumulate(low_win[e], axis=1)
        hit_stop = run_low <= stop[sl_, None]
        hit_tp = run_high >= target[sl_, None]
        any_stop, any_tp = hit_stop.any(axis=1), hit_tp.any(axis=1)
        k_stop = np.where(any_stop, hit_stop.argmax(axis=1), max_hold)
        k_tp = np.where(any_tp, hit_tp.argmax(axis=1), max_hold)

        stop_first = k_stop < k_tp
        tp_first = k_tp < k_stop
        both = (k_stop == k_tp) & any_stop & any_tp
        take_stop = stop_first | (both if path == "adverse_first" else False)
        take_tp = tp_first | (both if path == "favorable_first" else False)
        timed = (k_stop == max_hold) & (k_tp == max_hold)

        k = np.where(take_stop, k_stop, np.where(take_tp, k_tp, max_hold - 1))
        m = e + k
        px = np.where(take_stop, np.minimum(stop[sl_], opens[m]),
                      np.where(take_tp, np.maximum(target[sl_], opens[m]), close[m]))
        exit_i[sl_] = m
        exit_price[sl_] = px
        reason[sl_] = np.where(take_stop, "stop", np.where(take_tp, "target", "time"))
        if not np.all(take_stop | take_tp | timed):
            raise AssertionError("an outcome was neither stop, target nor time")

    gross = exit_price - entry
    fees = (entry + exit_price) * FEE_PER_SIDE
    net = gross - fees
    return dict(entry_i=entry_i, entry=entry, stop=stop, target=target, risk=risk,
                exit_i=exit_i, exit_price=exit_price, reason=reason,
                gross_pnl=gross, fees=fees, net_pnl=net,
                gross_r=gross / risk, net_r=net / risk,
                net_return=net / entry, cost_r=fees / risk, notional_per_r=entry / risk,
                hold_bars=(exit_i - entry_i + 1))


def summarise(net_r: np.ndarray, gross_r: np.ndarray, reason: np.ndarray,
              net_return: np.ndarray, cost_r: np.ndarray, notional_per_r: np.ndarray) -> dict:
    """Headline numbers for one cell.

    Two profit metrics, because neither alone is honest about this grid:

      mean net R      what a fixed-fractional-risk account earns per trade. It
                      is the natural objective, but R is *defined by the cell*:
                      a 6-ATR stop is six times the risk unit of a 1-ATR stop,
                      so the fee, which is a fixed fraction of notional, is six
                      times smaller measured in R. Wide stops therefore look
                      better on this axis for a real reason -- smaller position,
                      smaller fee per unit of risk -- and the reader has to be
                      told that is where the improvement comes from.
      mean net return the same trades as a fraction of notional, in basis
                      points. Unit-free, no sizing assumption, comparable across
                      every cell and every timeframe.

    `notional_per_r` is the leverage the cell demands: risking a fraction f of
    equity needs f x notional_per_r of the account as position. A cell wanting
    600 is asking for 6x leverage at 1% risk, which is why the selection rule
    caps it instead of letting the grid drift into cells nobody can fund.

    Drawdown belongs to an ordered path, so it is reported only for the serial
    walk, never for this unordered pool.
    """
    net_r = np.asarray(net_r, dtype=float)
    gross_r = np.asarray(gross_r, dtype=float)
    net_return = np.asarray(net_return, dtype=float)
    wins, losses = net_r[net_r > 0], net_r[net_r < 0]
    counts = pd.Series(reason).value_counts().to_dict()
    empty = not len(net_r)
    return dict(
        n=int(len(net_r)),
        net_r=float(net_r.sum()),
        mean_net_r=None if empty else float(net_r.mean()),
        gross_r=float(gross_r.sum()),
        mean_gross_r=None if empty else float(gross_r.mean()),
        mean_net_return_bp=None if empty else float(net_return.mean() * 1e4),
        mean_cost_r=None if empty else float(np.asarray(cost_r, dtype=float).mean()),
        median_notional_per_r=None if empty else float(np.median(np.asarray(notional_per_r, dtype=float))),
        win_rate=None if empty else float((net_r > 0).mean()),
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else None,
        avg_win_r=float(wins.mean()) if len(wins) else None,
        avg_loss_r=float(losses.mean()) if len(losses) else None,
        exit_reasons={str(k): int(v) for k, v in counts.items()},
    )


def draw_control_indices(symbol: str, signal_times: pd.DatetimeIndex, signal_i: np.ndarray,
                         pool_i: np.ndarray, pool_month: np.ndarray, pool_bucket: np.ndarray,
                         signal_month: np.ndarray, signal_bucket: np.ndarray,
                         n_per_signal: int, seed: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Matched random entries: same symbol, same UTC month, same causal tercile.

    The per-signal RNG is seeded from sha256(seed, symbol, signal time), so the
    draw depends on the signal's identity and not on how many signals were
    processed before it. A global RNG advanced in a loop reproduces only if the
    whole loop replays in the same order, which is the weaker of the two designs
    compared in yoyo/evaluation/matched_controls.py.

    Returns (owner_signal_i, control_i, unmatched) where `unmatched` counts
    signals whose stratum could not supply `n_per_signal` distinct bars. Those
    signals are dropped from the paired comparison rather than matched on fewer
    axes; a fallback control stops being matched on the axis that was missing,
    which is usually the axis that mattered.
    """
    frame = pd.DataFrame({"i": pool_i, "month": pool_month, "bucket": pool_bucket})
    by_key = {key: group["i"].to_numpy() for key, group in frame.groupby(["month", "bucket"], sort=False)}

    owners, controls, unmatched = [], [], 0
    for position, bar in enumerate(signal_i):
        stratum = by_key.get((signal_month[position], int(signal_bucket[position])))
        if stratum is None:
            unmatched += 1
            continue
        stratum = stratum[stratum != bar]
        if len(stratum) < n_per_signal:
            unmatched += 1
            continue
        stamp = pd.Timestamp(signal_times[position]).isoformat()
        digest = sha256(f"{seed}|{symbol}|{stamp}".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        chosen = rng.choice(stratum, n_per_signal, replace=False)
        owners.extend([int(bar)] * n_per_signal)
        controls.extend(int(j) for j in chosen)
    return np.asarray(owners, dtype=np.int64), np.asarray(controls, dtype=np.int64), unmatched


def month_block_signflip(month: np.ndarray, excess: np.ndarray, seed: int,
                         max_permutations: int = 9999) -> dict:
    """One-sided month-block sign-symmetry test on the paired excess.

    The block is a UTC month so that a whole month of correlated trades flips
    together; flipping single trades would treat a persistent regime as many
    independent observations and return a p value that is far too small.
    """
    if not len(excess):
        return dict(months=0, p=None)
    blocks = pd.Series(excess).groupby(pd.Series(month)).sum().to_numpy()
    observed = blocks.sum()
    if len(blocks) <= 16:
        signs = np.array(list(itertools.product([-1, 1], repeat=len(blocks))))
        null = signs @ blocks
        p = float(np.mean(null >= observed - 1e-12))
        method = "exact_month_block_sign_symmetry_one_sided"
    else:
        rng = np.random.default_rng(seed)
        # float64 on both sides: the mixed int/float matmul path raises spurious
        # invalid/overflow flags on this numpy build.
        signs = rng.choice([-1.0, 1.0], (max_permutations, len(blocks)))
        null = signs @ blocks
        p = float((1 + (null >= observed - 1e-12).sum()) / (1 + len(null)))
        method = "seeded_month_block_sign_symmetry_one_sided"
    return dict(months=int(len(blocks)), p=p, method=method, null_draws=int(len(null)),
                observed_block_sum=float(observed),
                positive_months=int((blocks > 0).sum()))


def serial_path(signal_i: np.ndarray, exit_i: np.ndarray, net_r: np.ndarray,
                net_pnl: np.ndarray) -> dict:
    """One position at a time, in bar order: the realisable version of the pool.

    The pooled per-signal mean is the cleaner estimator of edge, but it lets
    overlapping signals be counted as if they were separately fundable. This
    walks them in order and skips anything that opens before the previous exit.
    """
    order = np.argsort(signal_i, kind="stable")
    taken, earliest = [], -1
    for position in order:
        if signal_i[position] <= earliest:
            continue
        taken.append(position)
        earliest = exit_i[position]
    taken = np.asarray(taken, dtype=np.int64)
    values = net_r[taken]
    equity = np.r_[0.0, values.cumsum()]
    streak = best = 0
    for value in values:
        streak = streak + 1 if value < 0 else 0
        best = max(best, streak)
    return dict(n=int(len(taken)), skipped=int(len(signal_i) - len(taken)),
                net_r=float(values.sum()),
                mean_net_r=float(values.mean()) if len(values) else None,
                net_pnl_per_unit=float(net_pnl[taken].sum()),
                max_drawdown_r=float((np.maximum.accumulate(equity) - equity).max()),
                max_loss_streak=int(best), taken_positions=taken)
