# V20 — Confirmed structure on the fixed direct-hourly entry

Status: preregistered, before real feature generation or outcome access.
The overall profitability goal remains active and unmet. A support audit is
only the first gate of this experiment, not a substitute success criterion.

## Question and prior negative evidence

Does a persistent confirmed hourly structure direction improve the fixed251
BTC direct K1 intentions under the latest unchanged V18 exit specification?
The owner requested other confluence signals and a source-level ChartPrime
audit. Market Break Analytics motivates this particular state representation.
This is NOT an entirely new hypothesis: the old multiasset K1/K2 atlas tested
aligned structure (54 coins, waiting for K2) and failed out of sample:2025
n909 net−40.2bp/excess−35.5bp;2026MarApr n135 net−14.6bp/excess−2.1bp.
See analysis/p0_two_key_candle_ma_retest_deep_dive_20260904.md. We do not erase
that result. V20 tests a different fixed BTC direct-entry cohort, not a new
claim of discovery. The repeated2023–2024 period is development, not fresh OOS.

## One fixed change

At each own K1 close, accept iff the persistent confirmed hourly structure
state equals its own direction. Opposite is observed abstain; unavailable or
unestablished state is unknown. Do not require the actual break on K1 itself.
No MA/ATR/volume/resonance conjunction, extra entry score or length sweep.

Native UTC1h requires all12 raw5m observations; no interpolation. A missing
hour resets state AND levels. Fixed pivots10-left/10-right: centre high/low
equals the21-hour max/min, including ties. Pivot origin is the centre OPEN;
availability is the confirmation hour CLOSE,10 hours after the centre close.
Latest confirmed level price must equal its previous-hour value. An actual
close cross establishes/reverses state only when unknown or opposite; state
persists otherwise. Same-priced replacement pivots can pass the stable-price
condition. No backpainting. This explicit Python approximation is NOT exact
Pine builtin tie parity or TradingView runtime certification. Unlike source
calc_bars_count5000 chart-history initialization, all arms use the fixed full
causal prefix; gap reset is an explicit safety adaptation.

All251 mothers,462 own controls,154 fixed triples and97 unmatched remain.
Gate controls using their OWN timestamps and prices; never case context.
The V18 exit remains15m SMA40HL2 native true-color reversal, existing profitable
fast5m half-exit and failed-confirm2 full exit, K1 extreme stop,72h absolute
horizon,20bp round-trip. No changes to the prepared/unrun V19 engine variant.

## Frozen sequential workflow

1. Commit builder/config/tests/plan and register experiment before market read.
2. Verify V1 config and V4 pre-entry hashes; timestamp-only archive preflight
   followed by nrows OHLCV prefix strictly before2025-01-01. Existing pre2023
   warmup is allowed. No2025+ market prices or holdout outcomes materialized.
3. Freeze all713 contexts, complete causal hourly OHLC/state trace (not just21
   recent hours), counts by four original halfyears/direction/all24months and
   original triple membership. Write context_frozen.json BEFORE V18 outcome
   file hashes or prices/returns from the saved result tables are touched.
4. Inherited practical support thresholds all required:80 accepted mothers,
   at least12 per halfyear,12 active months,3 active months per halfyear. These
   are NOT a prospective power guarantee. If any fails, do not access outcomes,
   lower thresholds or try pivot-length grids; report support failure.
5. If all pass, the already-declared second stage reuses the byte-pinned V18
   independent EPISODE ledgers. Accepted rows retain every old episode field;
   abstain has no entry/fee and zero return, releasing occupancy at decision;
   unknown retains NaN and conservatively reserves to mother+72h. Unknown is
   not a simulated real position. Recompute serial occupancy separately for
   both case and control arms. Saved baseline matching and serial parity must
   pass. Reuse is valid only because no entry/exit/stop/fee/account-capital rule
   changes; this is cached fixed-exit accounting, NOT an independent replay.
6. Independently verify hourly-state/pivot clocks, all713 gates, matched triples
   and any economic accounting. Then record report, negative results and learning.

## Estimands and acceptance

D: candidate minus baseline return over ALL251 opportunities; abstentions are
zero, unknowns remain separately counted. I: paired case-minus-own-three-control
excess change on the ORIGINAL154 groups.97 unmatched I stay unknown. Show both
arms' absolute matched excess, not just D. Serial difference also uses all251
source intentions with fresh selection masks; do not reuse old portfolio masks.
Also report accepted executed trade quality, per-halfyear, own control outcomes,
avoided losers and missed winners; summed event-bp is NOT account equity return.

Use inherited9999 seeded20260906 calendar-month bootstrap95% CI and month sign
flip for D/I; they must jointly have positive mean/lowerCI and p<.01 to support
further progress. This is an intersection requirement, not a choice of whichever
p looks best. No removal of outliers or posthoc gate search. Report descriptive
mean/median/SD/IQR and temporal dependence; no IID t-test on overlapping trades.
Repeated development reuse and past strategy searches make all p-values
exploratory, not confirmatory; no familywide alpha claim. Four halfyears are
chronological robustness cuts, not four independent untouched validation sets.
Positive after-cost economics and original90% matching coverage remain required
for overall acceptance. Known coverage154/251=61.35% already fails; do not weaken
it or call a positive subgroup deployable. Binary gate has no ranked topdecile
or classifier AUC: explicitly N/A, equal-cost fixed random controls are the
single-feature/no-gate comparison. If support fails, all economic tests N/A.

## Deliverable and non-goals

Technical HTML report with answer-first summary, scope/definitions before
evidence, four-halfyear support and conditional outcome comparisons, source
clock/method validation, honest uncertainty, next bounded step and open questions.
Reorder definitions before findings to make denominators unambiguous. Exact
audit tables are preferable to decorative charts for this small fixed contrast;
if a quantitative chart is used its contract and source must be explicit.
Repository-required reproduction commands remain in source report. Publish no
website, no TradingView replacement, dependency install, training, promotion,
new repository/worktree/branch, production change or real-money action.
