# V12: Close-based failure of the frozen hourly MA boundary

## Decision, scope and hypothesis

The preceding goal turn made progress: V11 was implemented, replayed, independently
audited and rejected. Its60min/0.5R deadline cut slow-starting winners; this is not
evidence that a different exit will profit. Owner authorized continued research.
V12 tests one extra structural exit on the original V5 direct-K1 population, not
a new entry family or an optimized default. No V11 deadline is included.

Use only the already authorized2023--2024 BTC-USDT-SWAP development price prefix,
plus pre2023warmup. No2025+price materialization, holdout, new training, dependency
install, TradingView change, deployment, production switch, live order or VPS
writer. Main branch only. Commit builder, config, tests and this plan BEFORE first
price replay. Preserve every prior output and failure attempt.

## Frozen single-variable policy

Baseline: V5 original251 direct1h impulse/engulf MA-cross entries, native5m
SMA40(HL2) true aligned-to-opposite colour exit. Preserve entries/entryprices,
K1 extreme stops,20bp roundtrip,72h mother deadline and raw5 risk clock.
Candidate adds exactly `frozen_ma_exit=true`; no other policy or threshold change.

1. Freeze the event's own completed signal-hour `ma` at entry. It is the1h
   SMA40(HL2) on K1, not a later moving MA and not native5 management MA.
   Require finite positive numeric nonboolean MA; signal and decision timestamps
   must be hourly aligned and `signal_time+60min==decision_time`. Invalid evidence
   fails the study, never removes a request as if it never existed.
2. Observe only valid raw5 bars actually held in full after entry and surviving
   their initial resting stop. The first completed CLOSE satisfying
   `direction*(close-frozen_ma)<0` latches structural failure. Equality does not
   trigger. Entry/seed/previous-hour close and intrabar wick alone do not trigger.
   This is an opposite-close STATE test, not a required newly observed crossing.
3. Execute fully at the next real raw5 OPEN, earliest entry+5min, even if that open
   recovers to the favourable side. Do not consume that open's future high/low/close.
   At a shared timestamp original source/open validation, gap stop, truecolour
   exit and total72h deadline retain precedence. A stop touched in the trigger
   bar already won before the bar could supply a held close.
4. No synthetic nextopen or gap-filling. Raw missing/invalid price or discontinuity
   censors. Invalid management colour resets only the old transition logic; it
   does not erase a valid raw-close structural trigger. Floating nonfinite raw
   segment IDs are unknown in this opt-in branch. Existing modes stay unchanged.
5. Initial entry gap already beyond MA does NOT reject or retrospectively move
   the entry: the first actually held completed close decides. No reentry, partial
   exit, stop raise, buffers, extra confirmations, time grid or MA-length search.
6. Opt-in API accepts boolean true only. Reject false/numeric/string activation,
   non-native5/non-transition policy, cadence15 or any launch-deadline keys.

Frozen-boundary semantics are supplied by the caller, not learned from returns.
Pandas2.3.3 exact time arithmetic and all-field equality APIs are checked against
official docs; no library upgrade:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
Completed-bar availability follows the convention described in TradingView's
execution model, without asserting fill parity with Pine or live markets:
https://www.tradingview.com/pine-script-docs/language/execution-model/

## Prior-mechanism deduplication

Bounded V1--V11 code/config search found no complete equivalent. Existing exits
compare dynamic HL2/MA colour, slope, staged R, fixed3R, sampled or native slower
colour. V4/V6 wait before entry; V7 changes source-zone entry; V11 tests timed close
progress. A frozen hourly boundary can fail without dynamic5m colour flipping,
or remain intact while dynamic colour flips. This is an ADDITION to the original
exit, not a replacement intended to keep positions through every original flip.
No claim that a bounded search proves no related strategy exists anywhere.

## Cases, controls and entry-known geometry

Reuse byte-pinned original V4 mothers251/controls462/assignments and V5 baseline
contexts/outputs. Original154 triples remain unchanged;97 excess values unknown,
not zero. V10's alternative allocation is not used. Rebuild original entry/context
fields, then all six baseline ledgers and compare every saved old column before
candidate execution. Entries/risk fields must remain fixed across both arms.

Controls use their OWN completed signal-hour MA, not the case's price-level MA
and not a transferred MA distance. Their synthetic initial stop still transfers
the case's initialrisk/ATR and scales it by the control's own ATR/actual entry.
Random controls need not contain a true crossing K1 and can start on either side
of their own MA. This is an explicit limitation, not a reason to delete controls,
rearm them differently, rematch or silently change the counterfactual contract.

BEFORE any arm outcome save all713 entry_geometry rows using only entry open,
initialstop,signalATR,completed signalclose and frozenMA. Include signed entry
and signalclose side, distance/ATR, initialR and signed MA distance/initialR g.
Fixed descriptive bins: g<0, g=0,0<g<1,g=1,g>1. g>=1 places the boundary at/beyond
the initial stop, usually unable to exit before that stop. Publish full251,
matched154,unmatched97 and462control counts/distributions, not just good buckets.
No bin becomes a new entry gate. Equality bins use exact arithmetic, not a
data-selected tolerance. New invalid boundary evidence stops before any outcome.

## Estimands, inference and decision gates

D251 is the paired new-minus-old net return of ALL original cases. Co-required
I154 is each matched case's D minus the mean D of its SAME three controls.
I measures the fixed-support own-MA policy difference, not isolated K1 morphology
or a matched boundary-distance effect. Never subtract all251 D from154 control
changes. Preserve unmatched97 and all zero/unknown outcomes explicitly.

Reuse predetermined9999 calendar-month cluster bootstrap draws and month sign
flips,seed20260906;95% intervals, one-sided positive p<.01. D and I must BOTH have
positive means/interval lower bounds and pass p; conjunction, not selection.
Report actual mean/SD/median/quantiles/extrema/missingness, signed-bin count chart
and monthly autocorrelation. No outlier trimming or normality-driven method
shopping. Fixed251/154 support, no post-hoc power chasing. Month dependence and
sequential reuse make all inference exploratory, not independent confirmation.

Unchanged development gates:80events,12perhalfyear,all4positive halfyears,
PF>=1.1,12active months/3perfold,90%matched support, net/excess uncertainty gates;
20bp costs with inherited additional10bp stress and leave-best-two checks.
Fixed range/ATR single-feature AUC/topdecile gross/net is descriptive only;
no modelvalAUC because no model is trained. Compare controls alongside every
financial result. V10 proved61.35% coverage, so this whole-population candidate
cannot be accepted even if a local mechanism improves. Do not lower the gate.
Fresh independent validation is still missing; previously examined2025+ data
cannot be renamed pristine. No audit or deployment from this experiment.

## Outputs and checks

Save baseline/candidate trade,episode,matching,single-position and full failure
ledgers; pre-outcome geometry; all251/154/serial differences, monthly rows,
paired mechanisms for both populations, input/output hashes and source commits.
Every changed known trade must be a frozen-MA exit at least5min after entry,
strictly earlier than its original exit. All other known trades preserve every
old output field, including economics/time and old colour diagnostics. Trigger
open+5min=available=filltime,triggerclose strictly wrongside,boundary fixed.

Synthetic long/short,strict equality,initialwrongside,preseed,wick-only,
dynamicMA disturbance,rawgap/invalidOHLC/nextopenNaN/nonfinite segment,
management-only missingness,old-exit precedence,stop-first,totaldeadline,
cutoff,unseen suffix perturbation,invalidoptions,empty/defaultparity tests.
Runner mocks prove bad config/hash/boundary/context/baseline stops before
candidate returns. Independent saved-ledger checks are not an independent raw
replay; state their scope rather than claiming financial truth from green tests.

Chinese source report plus generated portable HTML is the owner delivery; all
negative findings and limitations retained. Follow existing report/artifact
workflow, registry defaults false, and leave an extract-approach learning for
any nontrivial conclusion. If rejected, no threshold repairs on selected
winners; record the next evidence-grounded question separately.
