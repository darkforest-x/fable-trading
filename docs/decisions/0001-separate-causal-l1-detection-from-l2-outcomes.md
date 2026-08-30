# ADR 0001: Separate causal L1 detection from future L2 outcomes

## Status

Accepted

## Date

2026-08-31

## Owner authorization

“把你刚刚讲的方案全部做一遍”。Scope is the 5-minute MA-launch repair only.

## Context

The 2026-08-30 5-minute outcome datasets mixed three different facts into one
YOLO target: a rule-proposed visual core, its later TP/SL result, and several
rendered views of the same event. Most rendered inputs extended beyond the
declared entry point, while the economic evaluator could enter again from a
later image. Per-image bootstrapping then treated correlated views as
independent evidence. A good validation number under that pipeline cannot
distinguish visual recognition from future visibility, repeated-event weighting,
or a later re-entry.

This is a contract and data-lineage failure. It is not evidence that the raw
K-line source is corrupt, and it does not authorize reading the frozen holdout.

## Decision

1. Freeze one timeline for this 5-minute diagnostic:

   - `core_end_i` is the last bar of the rule proposal.
   - `decision_i = core_end_i + 2`.
   - The model-visible image ends at `decision_i`.
   - Entry uses the completed `decision_i` close.
   - Barrier resolution begins at `decision_i + 1`; the decision bar's earlier
     high/low can never be an exit.

2. Preserve the existing 5-minute economic assumptions for this repair:
   TP = 5 ATR, SL = 2 ATR, horizon = 144 five-minute bars, conservative SL for
   a same-bar TP/SL collision, barrier-price gap treatment, and 0.2% round-trip
   cost. Changing any of those remains an owner decision and a separate
   experiment.

3. Separate layer meanings:

   - L1 Gold answers only “is the owner-defined shape visible now, and where?”
     A TP result cannot promote a rule proposal to Gold, and an SL result cannot
     prove that the shape is absent.
   - The future TP/SL/timeout ledger is an L2 outcome artifact. Future bars are
     allowed only there.
   - The rebuilt outcome-conditioned YOLO pack is explicitly a diagnostic
     artifact. It remains `training_eligible=false` and
     `production_eligible=false`; it is not a substitute for owner-adjudicated
     L1 Gold.

4. Use one rendered image per `event_id`. Split by decision time at
   2025-12-01 UTC with a symmetric 450-bar purge band. Drop exact duplicate
   pixels deterministically and drop every member of a contradictory duplicate
   group.

5. Label generation and economic evaluation must call the same
   close-entry/next-bar outcome resolver. Any future repeated views collapse to
   the earliest causal proposal per event; maximum confidence may not select a
   later entry. Matched controls and bootstrap units are also events.

6. Old datasets and weights remain immutable historical evidence. No new model
   is trained, promoted, deployed, or evaluated on holdout until the current
   P0/P1 gates pass and the owner separately authorizes that action.

## Consequences

- The repair can establish that bytes, labels, source indices, split placement,
  and visible timing are internally causal. It cannot establish that a proposed
  box is semantically correct; that remains an owner-review gate.
- Historical metrics from the leaky/repeated-view pipeline are not comparable
  to a future causal model score and may not be used for promotion.
- The diagnostic pack may be useful for investigating shortcuts, but its role
  and eligibility flags must travel with every copy.
- A full audit must re-render every image from its pre-holdout K-line prefix and
  compare pixels, rather than trusting manifest timestamps alone.

## Alternatives considered

- Cropping old images in place: rejected because it preserves ambiguous
  label/entry lineage and destroys historical evidence.
- Keeping eight views and merely grouping the split: rejected because training
  and evaluation would still overweight events and allow score-selected later
  entries.
- Treating TP as visual truth and SL as background truth: rejected for L1 Gold;
  outcome and shape are different labels.
- Training immediately after rebuilding: rejected by the repository's current
  P0/P1 gate and because owner shape adjudication is still pending.
