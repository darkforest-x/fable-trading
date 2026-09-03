# Future review context must be reserved before causal ranking

## Problem

A human-review chart needs candles after a historical signal, but the detector,
gate, deduplication, and Top-K ranking must not see those candles. Merely saying
"future bars are review-only" is too weak when the same in-memory frame later
feeds feature calculation or ranking.

## Reliable pattern

Split the frozen source by construction:

1. retain one complete source frame for later review;
2. remove a fixed future suffix before creating any model task;
3. generate proposals, deterministic gates, events, and Top-K identities from
   the shorter prefix only;
4. reject any pre-selection row that contains a future/outcome-like field;
5. freeze Top-K identities and order;
6. only then reattach the reserved suffix to draw charts and calculate clearly
   labelled review-only moves.

For `exp-1h-okx-model-first-standing-top10-20260904-v1`, every usable symbol
had 396 confirmed 1h rows. The last 96 rows were physically removed before all
65,760 W18/W19 model inputs were built. Selection used only event confidence,
availability time, symbol, and class. The four-day suffix was reattached after
the ten identities were frozen.

## Verification

- The selection function fails closed if an event row contains names such as
  `future`, `outcome`, `return`, `mfe`, `mae`, or `review_`.
- An independent verifier rebuilt the Top-10 order from the pre-outcome event
  ledger.
- It mutated every post-signal OHLCV row for all ten selections and reproduced
  every gate decision.
- It replayed all ten exact model-input pixel hashes and all 274 candle-file
  hashes.

## Why this matters

Future context is useful for human judgement but dangerous to pipeline
lineage. Physical prefix/suffix separation makes the causal boundary testable,
not rhetorical. This is the inference-review analogue of
`human-review-future-context-must-be-physically-separated-from-training-input.md`.
