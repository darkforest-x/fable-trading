# A net loss with positive gross is a cost-line crossing, not an edge reversal

2026-09-17, from `analysis/p1_spike_v9_later_year_attribution_20260917.md`
(`exp-spike-v9-later-year-attribution-20260917-v1`).

## What happened

SPIKE V9's later year read −2,089.19R and looked like the signal had stopped
working. It had not reversed: gross was still +1,743.07R. Per trade, gross fell
from +0.1999R to +0.0348R while the fixed 0.2% cost rose from 0.0630R to
0.0766R. The edge did not go negative — it fell below the cost line and stayed
on the wrong side of it.

Both halves have to be said. Of the −0.179R per-trade fall, 92% is gross decay
and 8% is cost, so *the change* is a decay story. But the *sign* is a cost story:
without the fee the later year is positive. Reporting only one half sends the
next round after the wrong thing — "the signal broke" leads to more filters,
"fees ate it" leads to fee negotiation, and here neither was the actionable
answer.

## The decomposition that found the actual answer

`net R = gross R − cost R` on the published ledger, then each side split further:

**Cost in R is not constant even when the fee is.** Cost enters as
0.002 / stop-distance. Stop distances tightened from 3.68% to 3.15% as volatility
contracted, so the same fee got 22% more expensive in R. A fixed nominal cost
silently reprices itself whenever volatility moves — check it before blaming the
signal.

**The matched random control splits decay into market and signal.** Random
entries fell −0.0583R per trade over the same window, so 30% of the decline hit
every entry, not just this one; the signal's own excess lost the other 70%
(+0.1524 → +0.0188R). Without that control the whole fall gets charged to the
signal.

**Then find where it sits.** Split by timeframe: 30m was 61% of later-year trades
at +0.00013R gross per trade — a break-even fee of 0.03bp, i.e. no gross edge at
all — while 1H and 4H stayed net positive after the same 0.2%. The loss had one
address, and it was the bucket that fired twice as often as before (1,415 →
2,459 trades a month). Volume doubling into a zero edge with the fee still due
is the whole arithmetic.

## The break-even fee is the number to report

Per bucket, the nominal fee that exactly eats the gross edge:
`1e4 * cost * mean(gross_r) / mean(cost_r)` in bp. It converts "is this a cost
problem?" from an argument into a number: 0.03bp means free execution would not
save it, 62bp means there is real room. This project has reached the same
question from the other direction before (`f*` in the BB×Stoch rounds); it
belongs in any report where a positive gross ends as a negative net.

## Also worth checking before concluding a period "stopped working"

The earlier year was not a baseline. 66% of 30m's earlier-year gross came from
2025-05..07. The later year did not fall off a plateau — it fell off a
three-month event that was being treated as normal.

## Related

- [symmetric-asset-trim-separates-a-broad-edge-from-one-lucky-asset](symmetric-asset-trim-separates-a-broad-edge-from-one-lucky-asset.md) — same
  ledger, the concentration question; trimming could not rescue this period because
  the cause is a whole bucket at zero, not a few assets.
- [pool-internal-metrics-cannot-see-beta](pool-internal-metrics-cannot-see-beta.md)
- [volatility-level-and-volatility-expansion-are-opposite-axes](volatility-level-and-volatility-expansion-are-opposite-axes.md) — the
  volatility contraction that repriced the fee.
- [cost-risk-gates-can-remove-trend-tail-winners](cost-risk-gates-can-remove-trend-tail-winners.md)
