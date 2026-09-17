# Symmetric asset trimming separates a broad edge from one lucky asset

2026-09-17, from `analysis/p1_spike_v1_v9_asset_trim_20260917.md`
(`exp-spike-v1-v9-asset-trim-20260917-v1`).

## What happened

Two SPIKE versions were being compared on per-trade R and PF. V1 looked better
(+0.1397R per trade, PF 1.184) than V9 (+0.0337R, PF 1.050). Removing the single
best and single worst asset — 23 of 6,116 trades — turned V1 into −238.03R and
PF 0.948. The same operation on V9 removed 554 trades and left +2,593.52R. At
k=10 (4,444 trades removed) V9 still held +2,022.50R and its matched-control
excess barely moved, +0.06281 → +0.06229R per trade.

The two systems were not two points on one scale. One was a distribution, the
other was a single asset wearing a distribution's clothes: V1's best asset was
139.7% of its total net R, so the other 750 assets summed negative, and the
median asset was −1.567R.

## The method

Rank assets by realised net R, drop the top-k and bottom-k together, recompute.
Sweep k rather than picking one — a single k invites the reply that you chose it.

Two details decide whether the result means anything:

**Dropping the best asset always lowers the total, so the drop is not evidence
by itself.** The null is a random drop of the same shape: sample 2 assets at
random, matched on combined closed-trade count (±25%, because the concentrated
winner may be a 7-trade asset and an unmatched null would be drawn from
high-count assets that cannot move the total), 2,000 draws, and report where the
observed lands. V1: 0/2000 draws reached it. V9: 2.25%, at the edge and still
positive. That contrast is the finding; the raw trimmed totals are not.

**Rank once, on the full period, then apply the same list to every sub-period.**
Re-ranking inside the later year selects that year's winners with that year's
outcomes and manufactures a result.

## Why this is not a selection rule

The ranking reads realised outcomes. Nothing here says which asset will be next
year's RAVE, and a trimmed universe must never be promoted, forward-tested or
turned into a blacklist. It answers one question only — *is this total the sum of
many small edges, or one lucky asset* — and that question is worth a cheap
post-hoc pass before any expensive comparison is believed.

Trimming also does not repair a failing period. V9's later year was negative at
every k and drifted further negative as more assets were cut, because the later
year's winners were removed faster than its losers.

## Related

- [pool-internal-metrics-cannot-see-beta](pool-internal-metrics-cannot-see-beta.md) — the
  other way a pool total hides what is actually producing it.
- [extreme-winners-must-be-reconciled-with-the-whole-cashbook](extreme-winners-must-be-reconciled-with-the-whole-cashbook.md)
- [leave-one-winner-is-insufficient-for-tiny-right-tail-samples](leave-one-winner-is-insufficient-for-tiny-right-tail-samples.md)
- [symbol-ranking-window-must-end-before-the-trading-window](symbol-ranking-window-must-end-before-the-trading-window.md) — what a
  real asset-selection rule would have to satisfy.
- [filter-improvement-needs-winner-concentration-audit](filter-improvement-needs-winner-concentration-audit.md)
