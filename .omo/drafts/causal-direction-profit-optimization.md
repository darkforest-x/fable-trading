---
slug: causal-direction-profit-optimization
status: drafting
intent: clear
pending-action: write .omo/plans/causal-direction-profit-optimization.md
approach: <fill: the approach you intend to plan>
---

# Draft: causal-direction-profit-optimization

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

## Findings (cited - path:lines)

## Decisions (with rationale)

## Scope IN

## Scope OUT (Must NOT have)

## Open questions

## Approval gate
status: drafting
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
intent: clear
review_required: false
status: approved-for-execution
approved_by: owner message 2026-07-11 "按照你的建议的方向开始优化吧"

## Decisions

- Additive challenger only; current dense detector, ACTIVE, TP5/SL2, h72 and costs stay frozen.
- One causal image ends at the signal bar; no future candle can appear in pixels or features.
- Universe is OKX USDT perpetual swaps, 15m, MA206 only.
- Candidate set is the de-duplicated union of existing expanded long and short masks.
- Fixed labels: long when only the existing long TP5/SL2 outcome wins, short when only the
  existing short outcome wins, otherwise no_trade.
- Global chronological 80/20 dev split with barrier purge; signals at/after the frozen holdout
  boundary are never rendered, trained, predicted or summarized.
- YOLO classification uses yolo11n-cls, imgsz 320, 20 epochs, all semantic augmentations zero.
- Fixed argmax policy; no confidence threshold search. Economic evaluation reports 0.06%, 0.2%
  and 0.3% costs and compares against a same-manifest numeric multiclass baseline.
- No automatic promotion. A positive val result is discovery evidence and may only create a
  prospective shadow candidate after the current E2.1b/SAHI chain is complete.

## Owner gates preserved

- Any ACTIVE, TP/SL, threshold, cost or live-order change.
- Any holdout evaluation.
- Any real/demo exchange credential.
