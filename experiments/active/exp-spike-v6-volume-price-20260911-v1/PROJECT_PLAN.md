# SPIKE V6 volume-price structure evidence — frozen plan

## Question

Can a V1 directional candle shape be preserved as causal evidence inside the
already-valid V5 provenance-to-final-confirmation window without restoring V1's
same-bar RV≥4 or TR/ATR≥3 filters?

## Single change

V6 starts from V5. Each V4 legacy confirmation opens the same V5 pending parent
range. It seeds an evidence latch when any candle in that confirmation's current
or two preceding bars has V1 directional candle quality: direction-consistent,
body/range ≥0.55 and direction-side close location ≥0.75. The seed uses V5's
already-required current three-bar progress (absolute direction ≥1.5 ATR) and
three-bar volume ratio (≥1.5). While pending, only a current candle with that
same existing V5 envelope can latch later evidence. V6 emits on the first
closed bar where both latched evidence and all V5 structural completion
conditions hold, then consumes the parent and evidence.

No new RV or TR threshold exists. V1's quiet-window, six-bar opportunity clock,
and same-bar force conjunction do not return. V5 risk constants, reference
ownership, display defaults, both directions, alerts, and no-extra-label policy
remain unchanged.

## Causal/failure rules

- Long/short are exact mirrors.
- Only `barstate.isconfirmed` state changes can seed, latch, emit, or clear.
- A new V4 provenance replaces its predecessor and resets evidence.
- A data gap, unknown structural input, or frozen-parent break clears it.
- A structurally eligible V5 bar may wait for evidence; V6 emits only at the
  first later bar where both evidence and all V5 completion conditions hold.
  That final candle need not itself have the directional shape when evidence was
  latched earlier.
- A V5-like completion with no evidence is explicitly rejected as
  `await_launch_evidence`; it is not silently treated as retained V5 baseline.

## Validation

1. Synthetic contracts cover current/prior-two seed, delayed completion,
   missing evidence rejection, later evidence, range break, gap, short mirror,
   prefix causality, and V5 display/risk source contracts.
2. After the implementation and this plan are committed, run one small frozen
   PEPE 1H known-positive functional replay through the supplied-feature oracle.
   It reports V5→V6 signal retention/movement only; no PnL, ranking, parameter
   search, or two-year backtest is allowed in this experiment.
3. Native Pine compilation and private TradingView save belong to the root task.

## Non-goals and status

This is a causal software hypothesis, not a profitability, recall, deployment,
or production-signal claim. Holdout is not read. The full two-year backtest is
owned by a separate task and is pending here.
