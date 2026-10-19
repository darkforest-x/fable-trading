# V1 Plus: evidence audit and enhancement specification

## Authorized scope

The owner asked on 2026-09-12 to use lighter subagents to inspect the supplied
Notion analysis, explain the long-only backtest, and determine how to build a
stronger V1. This phase reconciles existing reports, ledgers, and source code;
it does not rerun returns, tune parameters, change the frozen strategy, deploy
notifications, or execute trades. The main agent integrates two bounded Luna
Max audits, with no nested delegation.

## Sources and identity

- Notion page: 3d88856479af806d8abcf00b1c393d85, title 数据呈现的规律.
- Retrieved page snapshot: output/qa/v1_notion_audit_20260912/notion_source.md.
- Two-year source: exp-spike-v1-twoyear-allmarkets-20260911-v1.
- Three-year source: exp-spike-burst-three-year-20260910-v1.
- Frozen V1 source: yoyo/evaluation/pine/spike_burst_v1.pine.
- Display derivative: yoyo/evaluation/pine/spike_burst_v1_display.pine.

This is this audit's first authorized read of already evaluated historical
evidence, including post-2026-05-04 rows. New strategy holdout evaluations: zero.
The historical experiments retain their original consumption counts. All
periods discussed by the Notion analysis have been seen; they cannot become a
new untouched out-of-sample set by renaming them.

## Acceptance

1. Link each direction/time-span claim to its actual report/configuration.
2. Reconcile headline counts and finite realized R with the source ledger.
3. Distinguish observable features, future outcomes, and untested mechanisms.
4. Specify long/short symmetry, executable risk clocks, stop precedence, and
   independent single-change comparisons before any enhancement is backtested.
5. Preserve the original signal gates and present thresholds as hypotheses,
   not optimized values. Preserve a tail-capture criterion alongside drawdown.
6. Deliver Markdown plus rendered HTML, source/evidence hashes, and a learning.

No predictive model is fitted. AUC, randomized strategy controls, and trading
returns for the proposed enhancement are not produced in this evidence-only
phase. The null checks are direct source recomputation and contradictions of
the supplied numerical/causal claims, not new economic performance claims.

Training eligible: false. Production eligible: false.
