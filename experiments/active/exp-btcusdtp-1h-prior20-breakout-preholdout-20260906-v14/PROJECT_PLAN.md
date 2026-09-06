# V14 — Prior20 breakout support before outcome replay

Status: preregistered implementation; no real source run or result at writing.
The overall profitability goal remains active and unmet. This stage is a
deterministic support audit, not a replacement objective or profitability test.

## Hypothesis and previous evidence

Owner asks to implement a profitable hourly large-body/engulfing MA-cross entry
with lower-timeframe trend/colour exits. V13 pure prior4h colour did not improve
trade quality. The V13 NEXT_EXPERIMENT proposal asks for the exact isolated
prior20 structural-breakout contrast on original V5 opportunities.

This is NOT a new hypothesis: V1 already evaluated require_breakout20 after an
hourly-slope filter with the old colour exit. That bundled/conditioned test had
only16 trades and failed. V14 does not erase that negative result or call the
reused 2023--2024 period fresh validation. No rolling-length search is allowed.

## Frozen single change and retained specification

Keep original251 case mothers,462 original own controls,154 fixed triples and97
unmatched mothers. Do not select/rematch controls after observing the gate.
Remove V13 entry gate entirely. Add only a strict directional breakout at the
own K1 close: long close above max prior20 hourly highs; short close below min
prior20 lows. K1 itself is excluded. Fixed20 is from the existing hypothesis,
not outcome fitting. No MA slope, extra colour, volatility or exit change.

Source clock: K1 open S, decision S+1h. Expected prior starts S−20h to S−1h,
all complete contiguous60m bars; their boundary is available at S. Complete K1
close must independently equal the saved own signal_close. Equality with prior
boundary is abstain. Missing source or prior support is unknown, never zero or
false. Source snippets preserve missing rows; do not fill or interpolate.

Keep K1 extreme hard stop,72h horizon,20bp round-trip cost, native5m SMA40
aligned-to-opposite colour exit as inherited but NOT executed in this stage.
Known abstain would incur no trade/no fee in a subsequent policy simulation;
no realised return, including zero, is written by this support-only runner.

## Outcome-free stopping rule

Freeze all713 gate contexts and their own prior20+K1 source-hour snippets, then
support counts by case/control, four original halfyears, direction and all24
calendar months. Preserve154 matched groups, including unknown or abstentions.
Acceptable support requires ALL four inherited conditions: at least80 accepted
cases, at least12 accepted cases in EACH fixed halfyear, at least12 active
months, and at least3 active months per halfyear. These are practical inherited
minimums, not a prospective power calculation.

If any fails: status insufficient_support_no_outcomes; do not open old return
files, calculate MFE/PNL/PF, replay candidate exits, lower sample thresholds,
change20, or rank other lengths. Report outcome metrics not applicable/not run,
not zero or profitable. If all pass: support_pass_requires_separate_replay;
freeze the next stage before any outcome replay. It must retain all251 D and
same154 I, unchanged20bp economics and full old-field baseline parity.
Known matching coverage154/251 remains below.9 and is separately reported;
passing support does NOT pass overall acceptance or authorize deployment.

## Data boundary and execution order

1. Save code/config/tests/plan, register V14 and commit builder BEFORE run.
2. Validate pinned V1 base config plus only four V4 pre-entry files; never V5
   outcome hashes/files at this stage. Preserve immutable source price archive.
3. Timestamp/hash preflight then load only source prefix before2025-01-01;
   later archive prices are not materialized. Holdout consumption0.
4. Run pure causal context, freeze source/rows, independently verify support.
5. Write result source MD, immediately convert repository HTML, package full
   canonical artifact into same HTML with official renderer. Save notebook.
6. Record all failures, learning, hashes, scope and next bounded proposal.

No training, new dependencies, parameter promotion, TradingView replacement,
production API, deployment or real-money actions.

## Validation and controls

Synthetic tests cover strict equality, mirrored directions, own-control gates,
timestamp alignment, current K1 exclusion, missing signal hours, missing prior
hours, future-prefix invariance, empty context and retained order/index.
Independent stdlib saved-only verifier recomputes clock windows and max/min from
the exported entry-known hourly rows (not raw5m resampling), all gate states,
population/fold/month denominators, support gates and fixed triple membership.
Negative-control mutation includes accidentally labelling K1 as prior: strict
close breakout of a range including its own high/low is impossible. This catches
contamination, not alpha. Raw aggregation correctness is covered by source
receipt and synthetic resample tests, not falsely claimed independently rerun.

No directional economic test is performed here, so val AUC, ranking permutation
p, top-decile gross/net, win rate and equal-cost random-entry PNL are explicitly
not applicable. Original matched controls are retained for independent SUPPORT
comparison, not used as a substitute for net-profit evidence. All reported
counts must be exact and source-backed; no inferential p-value on fixed counts.

## Delivery plan and documentation decisions

Technical HTML report: title, answer-first technical summary; definitions before
comparative evidence; support by four halfyears with one native full-width bar
chart; exact support/unknown/matching audit; methodology and temporal boundary;
limitations/negative result; next steps/further questions. Reorder definitions
before findings to avoid ambiguous denominators. Preserve reproduction commands
because repository explicitly requires them. Other process notes live here.

Chart contract: four fixed halfyear categories, accepted CASE opportunities;
actual SQLite from counts.csv, keep total/abstain/unknown/rate as chart context.
One semantic entity across time periods, single-root blue, labels/order are
non-colour distinctions, zero baseline. Not a four-point trend line. Other
tables require exact lookup, so no redundant charts. Portable native renderer
only, no bespoke HTML/SVG or browser installation. Structural-only QA must be
disclosed if installed Chromium unavailable. Notebook is saved-evidence only;
plain-Python execution must not be called full Jupyter validation.

Reproduce initial stage: `.venv/bin/python -m yoyo.evaluation.hourly_impulse_prior_breakout_research`.
Report and verifier commands will be recorded in final report after actual run.
