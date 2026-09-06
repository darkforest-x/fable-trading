# V4: K1 intention, first K2, and separate stop relocation

## Decision before prices

V1--V3 did not establish positive post-cost expectancy. Most losing direct
impulses never reached 1R while held. Test entry timing on original mother K1
requests, not on a retrospectively filtered successful-K2 pool. This finite
protocol and all builders must be committed before new path outcomes.

A: direct K1, K1 extreme stop. B: wait for first hourly K2, SAME K1 stop.
C: exact same first K2 and waiting path as B, K2 extreme stop. No additional
entry grid, slope filter, 4h context, exit grid, partials or cost changes.
The signal mothers are regenerated using V1 baseline make_entries plus its
four halfyear time masks, NOT loaded from a closed-trade result file.

## Source-derived K2 versus deliberate changes

Reference is the owner-causal-v2 Pine f_findBestK1 geometry (lines98--174).
Completed current-hour SMA40(HL2), not a frozen K1 MA price: rejection wick
>=25% of range, body<=50%, directional close location>=25%, actual touch
depth0..1.5 ATR14, full body on the trend side INCLUDING boundary equality.
Positive finite range/ATR required. K2 can itself have opposite HL2 colour
because of a long rejection wick. Therefore evaluate candidate K2 first;
only a nonqualifying bar becomes an intermediate close/colour check. Wrong
intermediate close or HL2 colour terminates the mother without an entry.

Deliberate differences: first K2 per fixed mother replaces best-K1 backward
selection; gap1..8 replaces the old2..8 to allow an immediate next-hour retest.
These define a waiting-policy contrast, NOT a literal old-strategy replay.
No pending K1-extreme cancellation: there is no position during waiting and
the source has no such rule. No hidden fee-to-risk/cooldown/quality filter.

Every mother gets a terminal status. No replacement by later K1s. Missing raw
5m bars or incomplete observation are unknown, never zero. Fully observed
wrong-close/colour, timeout, or invalid next-open risk is a non-trade zero.
A-invalid-at-mother-open does not cancel later B/C; it may only make random
risk transfer unavailable. B-valid/C-invalid after the SAME K2 is retained.

Entry is exact next5m OPEN after K2 CLOSE. No fills at the earlier wick/MA.
All arms use 5m native40 first opposite completed colour, immutable initial
stop and20bp round-trip cost. Absolute deadline is motherdecision+72h, so a
delay of d hours leaves72-d hours in position. Embargo final72h of each fold.
5m stop collisions and gaps retain the original conservative simulator rules.

## Control assignment and causal comparison

Before waiting/outcomes, assign three control mothers by same symbol/month,
UTC6h bucket, preceding720h ATR-tercile (minimum168), known5m colour, known1h
colour and directional slope sign. Direction and maternal next-open risk/ATR
are transferred, using each control's own maternal ATR/open. Require source
continuity; do not compare resampled-grid segment IDs with raw5m IDs. Ban
only already-known current/prior-hour crossings. No future-K2-success key,
no widening strata and no control-time reuse. Missing matches remain visible.

All arms use the SAME assigned control mothers. Each control independently
undergoes the same waiting, stop-source and exit policy as its paired case.
Report assigned-mother coverage separately from all-three-observed coverage.
No successful-K2-only rematching, which would condition on the future.

Primary denominator is every original mother opportunity, including observed
non-entry zeros. Secondary table is actual completed trades: count, mean,
gross/net, PF, win rate, folds, range-only AUC/top-decile, loss taxonomy and
extra10bp/leave-top-two. Missing outcomes are never zero-filled. Paired A→B
and B→C differences include missed A winners and avoided A losers at their
actual realised returns, not hypothetical maximum excursions. Event sums are
not portfolio returns. A separate serial pending-plus-position ledger locks
from mother acceptance, not from delayed entry; current terminal event precedes
same-time new mothers. Unknown states reserve their full maternal horizon.

## Statistical assumptions and gates

Overlapping market events violate IID assumptions. Use fixed calendar-month
cluster bootstrap9999 draws for95% effect intervals, with SD/IQR and monthly
lag1 correlation descriptive diagnostics. One-sided monthly sign flips and
Holm over the two paired mechanism contrasts are exploratory; neither removes
historical data-mining bias or proves cross-month independence. Fixed available
historical span is not a powered confirmatory design. Keep every valid outlier;
leave-top-two is a separate robustness calculation.

At least80 actual completed trades and12 per fold; all4 fold means positive;
PF>1.1;12 active months,3 per fold;all-three-observed controls>=90% of original
mothers;positive net and matched excess;positive serial trade mean;positive
extra10bp and leave-top-two;zero unknown mothers. All gates precede any further
verification decision. No audit entry point, even if development passes.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse_k2.py tests/test_hourly_impulse_k2_matching.py tests/test_hourly_impulse_k2_research.py
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.hourly_impulse_k2_research
```

No training, live account operation, Pine/TradingView change, ACTIVE/frozen,
forward, VPS or deployment mutation. No holdout or2025+ prices loaded.
