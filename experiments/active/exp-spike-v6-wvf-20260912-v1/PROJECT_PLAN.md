# SPIKE V6 + original CM Williams Vix Fix reclaim — frozen plan

## Question and scope

Does one fixed, causal long-only WVF background rule improve the accounting of
the current `SPIKE V6 reference-exit-r2` signal without changing its signal,
stop, trailing, cost, or exit rules? This is a research-only study. It does not
modify Pine, TradingView, monitoring, notifications, models, execution, or any
production setting.

## Pre-registered variants

- **A**: all original V6 long and short signals.
- **B12**: only a V6 **long entry** needs the strict original-WVF reclaim in
  the preceding 12 closed bars. Shorts remain unfiltered. An unfiltered
  opposite V6 confirmation still exits every occupied position.
- **C24**: identical B logic except for a 24-bar lookback. It is an announced
  sensitivity result, never a winner selected after the results are seen.

WVF is `((highest(close, 22)-low)/highest(close, 22))*100`. Its band is
`SMA20 + 2 * population_std20`; the alternative extreme is
`WVF >= rolling_max50 * 0.85`. Here 0.85 is a rolling-maximum multiplier,
not an 85th percentile. At a long V6 signal close, the latest preceding WVF
extreme must fall inside the fixed lookback, current WVF must be non-extreme,
no close since that extreme may be below that bar's low, and current close must
be strictly above its high. Indicator state resets at every data gap.

No future confirmation, arrow backfill, delayed entry, stop widening, cost
change, risk scaling, or short-side WVF mirror belongs to this experiment.

## Execution and accounting

Inputs are complete closed bars. An admitted event enters at the next observed
open. The initial stop uses the signal bar's frozen last-five-bar extreme plus
or minus 0.2 ATR and at least 2 ATR from the signal close. A close at or above
2 actual-fill R arms the 4-ATR trail for the next bar. The previously active
stop is checked before current-bar high/low profit bookkeeping. A raw,
**unfiltered** opposite V6 confirmation closes an occupied position at the
next tradable open; same-direction signals do not reset it.

The prior Freqtrade bridge is not an exit oracle: it has `use_exit_signal=False`
and an empty `populate_exit_trend`. This study therefore implements the common
reference-exit-r2 opposite-confirmation rule directly and does not claim old
bridge exits equal current V6 exits.

Every run must retain (1) a raw signal ledger with filter reasons and current /
recent WVF state, and (2) a complete single-position trade ledger with signal,
entry, exit, side, price, initial stop/risk, net R, MFE R and account equity.
Account returns are sequential independent 1x-notional returns per coin and
period; aggregate R is reported separately and never presented as NAV.

## Data, splits and controls

The root agent will freeze the existing-cache source mapping before any result
run. Splits are chronological, never random. Any rows at or after 2026-05-04
are recorded as this configuration's authorized research evaluation #1; they
are already-readable research history and cannot be called a fresh blind
holdout. No parameter changes follow either result.

Each A/B12/C24 result includes long/short signal and executed-trade counts,
win rate, profit factor, net R, gross/net return, and 1x-notional account
maximum drawdown. It separately reports baseline net-R>=10 retained/missed,
MFE>=10R, failed-exclusion rate and deleted winners. The economic null is 99
deterministic random-entry controls matched by coin, time block and causal
volatility bucket, using the same obstacle/cost rules. AUC, ranking permutation
p, and top-decile return are not applicable because the experiment makes no
continuous prediction score; the report states that explicitly rather than
inventing them.

At least four charts will show: retained success, rejected failure, rejected
profitable candidate, and direct breakout without a panic background. Each
uses OHLC, six MAs, V6 signal and WVF; any post-signal bars appear only as
outcome visualization, not filter input.

## Verification before a formal run

Synthetic tests cover population standard deviation, gap reset, rolling extreme
expiry, no current-extreme admission, low-break/high-reclaim logic, raw signal
ledger retention, next-open entry, entry-bar stop precedence, unfiltered
opposite exits, and prefix/future perturbation isolation. Formal run remains
blocked on the root agent's frozen data mapping and a committed builder/source.
