# V35 source-clock and denominator review

## Scope and reviewer independence

A separate collaboration agent wrote only the synthetic aligner tests and later
reviewed the root-authored runner. This is a second-opinion code/recomputation
review, not an independent third-party review or an untouched validation set.
No trades, episodes, outcomes, prices, 2025+ data, or held-out labels were loaded.

The reviewer projected the saved mother/request/status identity and clock fields
and original control parent IDs, independently joining in pandas without using
the report SQL. Root separately rechecked all nine output SHA values and the
31 parents /93 original controls /25 emitted-control-request overlap.

| Fold | Case mothers | Case K2 | Matched mothers | Matched Case K2 |
|---|---:|---:|---:|---:|
| 2023H1 | 55 | 13 | 28 | 4 |
| 2023H2 | 66 | 19 | 40 | 12 |
| 2024H1 | 55 | 12 | 41 | 10 |
| 2024H2 | 75 | 11 | 45 | 5 |
| Total | 251 | 55 | 154 | 31 |

462 original controls =154 original mothers ×3, leaving97 unmatched case mothers.
55 case requests include31 matched and24 unmatched. The31 have93 original
controls, only25 of which independently emitted K2 requests. The89 total control
K2 requests therefore cannot be directly compared with the55 case K2 requests.
Keep all93 original controls when that conditional matched subset is evaluated;
do not retain only the25 emitted requests. No economic result was computed here.

All1001 window identities/clocks and all-mother K1 coverage agree. Eightquarter
counts independently recomputed:94/209/365/483/610/741/860/1001. Historical
availability passed; unknown observed delivery stays unavailable, not “all late”.

## Found and fixed before real materialization

Three representation-invariance tests showed pandas s/ms/us datetime integers
were incorrectly compared against a nanosecond grid. Root explicitly normalized
resolution to ns;116 aligner tests then passed. Initial111 tests had passed; the
extra resolution tests exposed a real interface edge rather than a data defect.

## Found and corrected before packaging

The label invalidated_ma_colour means rejected-candidate HL2 relative to MA,
not MA slope. Narrative/chart changed to HL2-side candle colour. Numerical
summary/report_data unchanged. Source at hourly_impulse_k2.py K2 geometry
precedes this intermediate-state failure check.

## Limits

55 requests/31 matched parents do not establish sufficient independent evidence.
Avoid repeated threshold search. This does not prove every exploratory test is
impossible: power depends on prespecified effect/variance; no power calculation
was run and no blanket sample-size profitability theorem is claimed.
