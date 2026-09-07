# Next bounded research: volume-weighted entry reference, support first

Proposal for a separately registered V26, NOT executed, not a profitable
configuration and not permission to relax V25. V25 remains support-insufficient
with9events and no outcome join. V24 and the V19 stop decision are unchanged.

## Why this mechanism / duplicate check

V1 already tested finite SMA/EMA,20/30/40/60,body/engulf strength,ordinary
volume ratio,priorcross count,efficiency,slope and20hbreakout coordinates.
V13 higher-timeframe colour,V14 prior20breakout,V20 persistent structure and
V21/V22 externalrank/change are not untested alternatives. V7–V9 also tried
first frozen-range launch. Do not repeat them as new discoveries.

One proposed change: entry reference SMA40(HL2) becomes VWMA40(HL2).
Formula sum(HL2*volume,40)/sum(volume,40), same40closed contiguous hours.
The question is whether equal-time weighting selects different opportunities
than participation weighting. This is not actual cost basis,taker flow,OI or a
claim that higher volume predicts profit.40 is held to isolate weighting,
not declared optimal. Keep all non-reference K1 thresholds unchanged, including
large-body/engulf/close-location conditions. Colour/side/slope/cross fields
depending on the entry reference must be coherently recomputed for that arm;
all other features stay frozen. Exit reference remains the old SMA.

Primary definition checked against TradingView:
https://www.tradingview.com/support/solutions/43000592293-volume-weighted-moving-average-vwma/
Official default example uses close; HL2 here intentionally preserves the
existing K1 source while isolating weighting. Do not call HL2 the vendor default.

Repository visual comparison already implements VWMA in
scripts/render_btcusdtp_ma_smoothness_comparison.py; this is not evidence of a
successful 1h strategy test. The current yoyo/data/hourly_impulse.py supports
SMA/EMA only. Do not edit that frozen source merely to add VWMA and invalidate
old byte contracts; implement a separately tested evaluation path with exact
SMA-arm parity and clearly traced downstream reference-dependent columns.

## First executable phase: saved-only entry/support audit

Register and commit a separate config/plan/implementation/tests before computing
any new reference or gate. Prefer V20 saved hourly_trace (same13-input lineage
style, including volume) rather than reopening raw or downloading. First verify
its schema/clock/volume definitions and complete coverage; otherwise stop the
phase and report missing source fields, not silently switch vendors.

Reconstruct BOTH entry arms on all admissible hourly opportunities in2023–2024,
not only the old251 cases. OldSMA arm must recover every original251 identity,
own clock,direction,K1 extremes and feature values. Differences must be resolved
before looking at new VWMA counts/outcomes. Keep the original shape-qualified
hour universe as a primary ledger, not just accepted trades.

40contiguous hours, finite nonnegative volume, positive40hvolume sum are
required for VWMA. Missing hour/invalid volume/zero sum is unknown; no fill,
skip-row rolling or converting unknown to abstain. SMA denominator must not
shrink just because a new-arm volume check fails. Own signal close/E clocks
and original fold end-minus72h restrictions remain.

Save all opportunity states and old/new accepted sets, common/old-only/new-only/
neither counts, perhalf/permonth/direction and unknown reasons. Relative-to-ATR
reference difference is descriptive only; do not use it to select another
weight/period after seeing results. If accepted identity sets are identical,
end as no-entry-change, not a reason to scan lengths.

Proposed support thresholds to freeze in V26 before real computation:
new-arm accepted>=80,>=12perhalf,>=12active months,>=3monthsperhalf. Practical
screens only, not a prospective power calculation. If fail, no new-arm outcomes.

## Background and economics are separate later preregistrations

Original V24 triples cannot cover newly created entries. If new entry support
passes, independently preregister a new whole-opportunity background support
and random allocation, preserving V23 month/UTC6h/causalvol matching rationale,
three controls per mother,no decision-time reuse,no fallback or outcome-based
selection. Report original opportunity,accepted and complete-matched denominators.
Require>=90% of all new-arm accepted cases matched; do not carry old226 absolute
threshold to a different-size cohort. Count feasibility is not random assignment.
Freeze candidate edges and one seed20260907 PCG64 allocation before labels.

Before any label join, preregister primary4hOPEN persistence-minus20bp and
matched excess,all original opportunities with honest abstain/unknown accounting,
24month dependence and fourhalf stability.1/12/24h descriptive only; do not select
the best clock afterward. If this supports continuation, run the existing frozen
V18 management path with next-real5mOPEN,K1extreme initial stop,20bp and72h cap;
do not retune the exit SMA or half-close fractions. Fixed-clock markout is not
stop-managed PnL. Require actual net positive plus matching advantage,time
stability,single-position,cost/slippage and later forward validation before use.

2023–2024 are reused development.2025–early2026 were also used by earlier
research; never relabel them pristine validation. An independent claim needs
new observations after a configuration is frozen;100fresh trades is a project
acceptance starting point,not guaranteed power or an assured time horizon.

## Deferred alternatives and authority

Session/weekly anchored VWAP changes reset clock,variable window and often price
source simultaneously, so do not bundle it into this single-variable test.
True taker buy/sell or OI would require separate source/time/availability audits;
existing normalized Binance OHLCV parser does not export taker fields. Do not
invent orderflow from generic volume delta. No re-opening V25gate,old exit scan,
cost reduction,holdout,new production defaults,TradingView replacement or orders.
Current authority permits bounded preholdout research preparation; consequential
live/ACTIVE/holdout operations still require their own explicit scope.
