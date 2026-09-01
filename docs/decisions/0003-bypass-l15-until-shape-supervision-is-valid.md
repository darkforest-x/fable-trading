# ADR 0003: Bypass L1.5 until shape supervision is valid

## Status

Accepted

## Date

2026-09-01

## Owner authorization

“L1.5是啥？不行就去掉行不行？” followed by “去做”.

## Context

L1.5 was introduced as an optional global-morphology classifier between the
frozen local YOLO detector and the side-specific economic L2 regressor. Its
purpose was to reject locally plausible boxes whose preceding 128-bar context
did not resemble a complete launch setup.

The first implementation exposed post-core confirmation progress that also
defined the weak launch/no-launch label. A single progress feature achieved AUC
1.0, proving label reconstruction rather than morphology learning. The repaired
implementation physically ended every input at `core_end`; LONG passed its
classification gate, but SHORT final false-positive rate was 15.70% against a
12% cap. More importantly, applying the filter reduced 242 final independent L1
events to 146 while changing their net mean from +7.35 bp to -7.43 bp.

The L2-only arm already existed inside the factorial experiment, but its
training path consumed a ledger augmented with L1.5 columns even though the arm
did not select on them. That is mathematically sufficient but leaves avoidable
lineage ambiguity when the declared architecture no longer contains L1.5.

## Decision

1. The default research topology is now:

   `frozen L1 candidates -> dependency episode collapse -> side-specific L2`.

2. L1.5 is bypassed, not deleted. Its code, preregistrations, failed models,
   receipts, reports and learning remain immutable historical evidence.

3. The independent bypass runner must read the original frozen L1 episode
   ledger with an explicit column allow-list. It may not import the global-shape
   module or either L1.5 experiment runner, and it may not read an L1.5 score,
   threshold, keep flag or model during training or scoring.

4. The first bypass run changes topology only. It preserves the 17 L2 features,
   LONG/SHORT separation, deterministic LightGBM parameters, time splits,
   dependency representatives, tune-q90 rule, TP5/SL2/72 outcome, 0.2% cost and
   eight matched-control assignments. It must reproduce the prior L2-only arm
   model hashes, per-event scores, thresholds and selected IDs.

5. Reproduction of the prior L2-only arm proves the bypass, not economic
   success. That arm remains rejected because permutation p=0.1921 and its LONG
   frozen-q90 subset loses money. No ACTIVE bundle, promotion, deployment,
   forward write or order is authorized.

6. L1.5 may return only as a separately preregistered experiment after its
   supervision target is replaced with valid global-shape truth. Re-enabling it
   requires a new owner decision; an automatic weak-label AUC is insufficient.

## Consequences

- Future L2 research has a shorter and auditable lineage with no hidden
  dependency on a rejected morphology filter.
- Current detections are not made production-ready by removing L1.5. The L2
  economic gate still has to pass on a genuinely unseen pre-holdout period
  before any holdout request can be considered.
- Historical L1.5 results remain reproducible and cannot be silently rewritten
  as if the layer never existed.
- Global-chart quality remains unsolved rather than falsely delegated to a
  classifier trained on protocol-generated weak labels.

## Alternatives considered

- Delete L1.5 code and artifacts: rejected because it destroys failure lineage
  and makes future agents repeat the same shortcut.
- Keep L1.5 but lower its SHORT threshold: rejected because the final period has
  already been observed and threshold tuning would not fix the negative
  L1.5-only economics.
- Call the existing factorial L2-only arm the new pipeline without rebuilding:
  rejected because its input ledger still carries L1.5 fields, leaving a
  preventable lineage ambiguity.
- Promote the profitable-looking aggregate L2-only subset: rejected because its
  p-value and LONG side fail the frozen economic gate.
