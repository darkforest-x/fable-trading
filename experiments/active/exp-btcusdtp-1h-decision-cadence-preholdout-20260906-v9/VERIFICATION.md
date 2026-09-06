# V9 validation report

## Assessment: share with caveats; reject the profitability claim

The first and sole V9 price run completed with
`rejected_development_no_audit`. Preregistration, configuration, study builder,
L3 implementation and synthetic tests were committed in12df0a0 before the run.
The fixed20bp cost,72h horizon, K1 extreme stop, native5m SMA40(HL2) features,
286cases/849controls/959zones and original control allocations did not change.
Only completed-colour decision sampling changed from5m to15m; protection and
data-quality checks still operate every5m.

The entry family is V7 frozen-source breakout, not the owner's literal hourly
MA-cross family. This bounded mechanism result cannot validate that original
system. No candidate nomination, audit opening or independent profit validation.

## Data and chronology

- Source receipt:219551 price rows including pre-window feature warmup, through
  2024-12-31T23:55Z; evaluated entries cover2023--2024. No2025+ price materialization,
  holdout price rows0. Physical archive metadata/hash is not a later-price audit.
- Source archive SHA256:
  767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac.
- All12 parent input hashes,28 study source/config/plan receipts, frozen config
  and base config independently matched both working files and builder commit.
  All56 result-file hashes stayed unchanged during independent review.
- Case contexts286x43 and control contexts849x47 are exact across arms, including
  decompressed CSV bytes. All original native5m entry states are aligned.
- Full V7 baseline parity: case/control trades286x71/849x75, request outcomes
  286x51/849x55, matched requests286x7 and serial intentions959x27. All old columns
  agree, not merely means or newly appended management diagnostics.
- Presentation adapter fix and mechanics-source provenance committed78f7243;
  final portable artifact regenerated after that commit. Earlier unpublished
  preview is not the delivered artifact. No historical V1--V8 report rewritten.
- Independent CSV verifier and51 synthetic counterexamples committed046b92a
  before its successful execution on saved V9 records.

## Re-runnable independent saved-ledger verification

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_cadence_verify
```

This checker does not import Study or L3 simulation helpers and reads no raw
prices. It independently recomputes gross/net/netR/risk/holding formulas from
saved fills, checks entries, both clocks, control groups and a separate greedy
single-position selection loop. Actual execution returned `status: passed`:

| Arm / population | Trades | Mean net bp | Colour exits | Hard stops | Off-quarter hard stops |
|---|---:|---:|---:|---:|---:|
| Original5m / case | 286 | -17.78780723 | 263 | 23 | 15 |
| Original5m / control | 849 | -22.45527126 | 811 | 38 | 28 |
| Sampled15m / case | 286 | -16.43306544 | 260 | 26 | 15 |
| Sampled15m / control | 849 | -21.97512946 | 795 | 54 | 35 |

All2270 trades closed. Original colour edges use adjacent5m observations;
sampled edges use the entry-phase seed then15m-separated available times.
The newest native5m raw open+5m equals its availability and actual exit time.
Off-quarter hard stops confirm that sampling did not postpone risk checks.
Resting stops precede colour fills; fees remain20bp, partial fills remain zero.

All286 case deltas are finite: mean+1.3547417852bp. Matched excess delta retains
286 rows with283 finite and3 unknown, mean+.8282550332bp. All959 intention deltas
are finite, mean+.4040210121bp. Both arms independently select all959 zones,
execute286 trades and skip0; mean per intention is-5.3048100805 to-4.9007890685bp.
No denominator switch, control reassignment, missing-as-zero or winner removal.

## Independent result/report reconciliation

- D monthly-block95% interval[-1.59597675,+5.04314329]bp,p=.2269;
  I interval[-2.41319108,+4.37461522]bp,p=.3229. Both improvement gates fail.
- All13 arm gates and16 final gates independently recomputed and matched.
  Positive-point matched excess is not significant net profitability.
- Win/loss migration207/7/4/68 exactly reconciles286. New losses211 contain
  27 overlapping MFE>=1R cases: giveback14,costflip12,hardstop1. The other184
  did not reach1R during their actual holding periods; later trends are unknown.
- All four half-year case/control counts and net means match saved trades.
  Three unmatched cases occur in2023H1; no false all-case minus control pairing.
- Four examples are the chronologically first in their migration classes;
  entry timestamps, returns, holding time and MFE match saved mechanics.
- Paired-D ShapiroW=.3799495984,p=4.259299723e-30 independently reproduced with
  existing SciPy. Skill helper import failed for missingseaborn; no dependency
  installation, test-method switch or outlier trimming followed that failure.

## Tests and report QA

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py -q
```

Observed **1342 passed in16.72s**. Includes old-path regressions, sampled-clock
phase/seeding/gap/priority causality, frozen contexts, report provenance/fenced
directive handling and independent-verifier corruption counterexamples.

The canonical report preserves all authored text as14 native markdown blocks,
plus one12-bin native distribution. Bins contain all286 cases:75 improve,
92 worsen,119 zero,0 unknown; widths are unequal and displayed as counts,
not density. SQLite counts and source identities were reconciled independently.
Old complete V1--V7 and V8 MD/HTML/canonical artifacts are byte-identical to998136b.

Official portable receipt: validation passed, package passed,
verification `structural_only`;15blocks/1chart, source dialog and interaction
not_verified, viewports empty. Compatible Chromium headless-shell is unavailable.
No browser installation or bespoke visual automation was attempted. Structural
payload/semantic fallback verification is not desktop/mobile or light/dark QA.

## Limitations and next decision

Saved-ledger verification is not an independent second raw-OHLC/MA replay and
cannot itself prove the first real intrabar barrier or first actual colour edge.
Synthetic tests and historical baseline parity support the implementation but
do not substitute for new forward data. Repeated2023--2024 development and24
monthly blocks are not pristine confirmation; p values do not account for every
past exploratory attempt. Funding, order-book slippage and live latency are not
individually reconstructed. Mean event returns are not compounded equity returns.

V9 remains negative. Return to the original hourly MA-cross entry family and
diagnose its pre-entry matching support before testing entry persistence. Do not
filter by future MFE, the seven rescued winners, or replace the original entry
with source-zone rules silently. No TradingView, production, training, VPS,
forward logs, account risk or live-order changes. Profit goal remains active.
