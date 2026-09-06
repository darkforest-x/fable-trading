# V7: frozen source zone and its first qualified release

## Status and authorization

2026-09-06; preregistration before new price reads, requests, assignments or P/L.
Owner asks to develop the BTCUSDT.P hourly impulse system toward profitability,
with autonomous research. This is a single **entry-family substitution**, not an
isolated numeric parameter claim. It replaces static SMA40 crossing/MA-side gates
with a previously frozen price boundary and first-release consumption. Original
large-body/real-engulf morphology, K1 extreme, next5m open, 5m nativeSMA40 true
colour-transition exit,20bp cost and72h maximum holding remain fixed.
No live orders, TradingView changes, training, deployment or automatic promotion.

## Why this contrast, and what it cannot establish

V1--V6 are complete negative evidence, not fresh experiments. V6's24 genuinely
delayed executions all lost;19 shared the old exit time and price. Repeated
confirmation may spend the favourable path rather than create an entry edge.
Static length/volume/ATR/body/efficiency/crossing/slope/rolling20-breakout gates
and prior ETH/XAU compression/release/acceptance votes have already been tested.
The new feature here is a **frozen source and irreversible first-release clock**,
not the generic claim that compression or breakouts are new or profitable.
The4+4/8h specification is one unoptimized starting hypothesis. No grid follows
a failure in this experiment. This family removes the hourly MA-side entry gate
as well as the SMA-cross reference; its effect cannot be attributed separately
to either part. MA40 is still recorded for diagnosis and used on5m for management.

All2023--2024 development is reused. Random matching controls measured state,
not unmeasured confounding; this is not randomized treatment or clean confirmation.
The originalV5 direct251 trades are a **descriptive historical benchmark only**.
Their opportunity population differs; no paired delta against them is valid.

## Frozen state machine

1. Start independently in each of the four registered halfyears. Indicator
   warmup may precede the fold, but source candles must open at/after foldstart.
   Use complete contiguous UTC1h OHLCV only. Features are the existing causal
   ATR14/Pine RMA, body fraction, directional close position and genuine engulf.
2. When idle, inspect the most recent8 complete new hours: first4 envelope
   [minimum low,maximum high] must strictly contain the last4 envelope on both
   ends. Freeze the latter bounds. `source_start/end` are first/last source-bar
   opens; `zone_arm_time=source_end+1h`; deadline=arm+8h. Arm only if strictly
   before foldend−80h, allowing8h waiting and72h subsequent holding.
3. Inspect future complete hourly bars1..8. First close strictly above upper
   or below lower immediately consumes the source, even if morphology fails.
   Touch/equality is not release. At the8h boundary evaluate release before expiry.
4. That first released K1 alone can emit a request: strict directional body
   crossing of frozen boundary, directional close_location>=.70 and either
   body_ratio>=.65 with range/ATR>=1, or genuine directional engulf with
   range/ATR>=.65. ATR must be finite and positive. No MA-side or MA-cross gate,
   no volume/slope/extension addition, and no waiting for a second release.
5. Request at release close; fill only real next5m open at that same boundary.
   Stop is that K1 low(long)/high(short), never the source-zone boundary.
   Actual risk is repriced at actual open; nonpositive risk rejects once.
6. Rearming uses8 entirely new completed hours whose opens are >=the source
   terminal time. Terminal is first release, observed expiry or data-gap
   censorship, **never future position exit**. No nested/moving source or
   same-source retries. Independent source generation may overlap prior positions;
   executable pending/position occupancy is checked separately.
7. Missing hour/segment break terminates pending observation as unknown at the
   missing hour's expected close; do not skip to a later favourable release.
   Prefix exhaustion before a known terminal is unknown, not non-entry zero.
   Newly incomplete/future bars cannot be read to manufacture a terminal.

## Units, clocks and evidence denominators

- All armed source zones get one immutable zone_id and one terminal row:
  request_emitted, first_release_unqualified, expired_no_release,
  censored_source_gap or censored_source_end. Known nonentry0; unknownNaN.
- Signal requests use original directional K1 ids plus zone_id/source geometry.
  Complete all requests exactly once. Missing entry or required OHLC execution
  path evidence is unknown, not an observed cancellation. Invalid actual risk is
  observed nonentry0. A missing management colour during an otherwise observed
  holding path inherits V5: reset the transition chain while retaining the hard
  stop and timeout; it does not create a new exit or censor the entire trade.
- Trade statistics use all closed executions, with request/rejection/censor counts
  disclosed. Zone intention returns use all armed zones and are **not** per-trade
  returns. They are not compared to the random-entry controls as same-unit returns.
- Case-control excess uses **all entry requests** as coverage denominator; three
  assigned controls are required and must have observed outcomes, including known
  invalid-risk0, with unknowns excluded from finite estimates but retained in rows.
- Serial diagnostic allows one pending source or position, beginning at source
  arm. Known nonentry releases occupancy at terminal; valid executions at actual
  exit; unknown paths conservatively occupy arm+80h. Process prior terminal first
  on equal timestamps. Never change independent source generation using P/L.

## Outcome-free control allocation

Exactly3 controls for each case request, assigned per fold BEFORE simulation.
Candidate and case decisions lie in[foldstart,foldend−72h). Match BTCUSDT.P,
UTCcalendar month, UTC6h session, causal ATR-fraction tercile and last complete
native5m MA40 side. ATR fraction=current completedhourATR/close; thresholds use
prior720 same-hourly-segment values shifted1, minimum168. Direction is the case's
direction, and case's actual entry-open risk/ATR is transferred to control's own
current open and completedhourATR. Nonpositive stop or invalid inputs cannot match.
Native5m colour must be available exactly at decision and source continuity known;
do not compare counter labels between1h and5m grids. Missing support is explicit.

Do not match on source-zone qualification, hourly colour/slope or oldMA-cross;
those would change the tested alternative or retain unrelated V4 exclusions.
Exclude actual V7 entry decision timestamps already known at their own decision,
not future event windows. No future returns, closed flags, best entries, source
success labels or future-cross exclusions. Candidate risk feasibility uses only
current open/ATR. Hash(seed20260906,parent_id,candidate_time) orders eligible
candidates;3 distinct controls or none, no fallback. Globally unique control time
within fold, even across parents/directions. Original unmatched cases stay intact.
Month clustering is only an approximation for overlapping windows; reuse/history
and cross-month residual dependence remain explicit caveats.

## Prespecified gates, then economics

StageA saves all source/request/assignment/context evidence and request counts
without simulating P/L. Need >=80 requests, >=12 in every halfyear, >=90% exact
three-control assignment coverage, zero unknown source paths, and >=12 active
request months with >=3 per fold. If not, stop with `rejected_support_no_outcomes`.
No posthoc support repair, sampling replacement, alternative rule or relaxed gate.

IfA passes, stageB simulates all cases and allocated controls with unchanged
execution. Fixed go-gates: >=80 completed case trades, >=12 each fold, all4folds
positive, net mean>0, PF>1.1, >=12 active months/>=3 per fold, full3-control
observed coverage>=90%, positive matched excess, positive serial mean, positive
mean after extra10bp and after removing the largest2 winners. Require no unknown
case/control outcomes. Both net and matched-excess one-sided month sign-flip
p<.01 and95% month-bootstrap lower bounds>0 are exploratory gates, not clean
proof (9999 draws,seed20260906). Both are required; no winner chosen across arms.

Report gross/net, win rate, PF, right tail, folds/months, fee flips, early exits,
MFE/MAE, missed source/release states, source wait and initial management colour.
Single-feature range/ATR AUC and topdecile gross/net are reference, not model
success. No YOLO/LightGBM training; model valAUC not applicable. Full matched
random comparison accompanies directional results. IfA fails, P/L/AUC/p is not
computed and is explicitly inapplicable; synthetic mirrored/prefix-invariance
tests and unchanged saved baseline identity provide the non-outcome null checks.

## Data, reproducibility and stop condition

Use only physically safe existing OKX5m archive through2024-12-31 23:55UTC;
source hash and timestamp boundary must precede bounded price read. No2025+
price materialization, no repository holdout>=2026-05-04, no audit entrypoint.
Commit all builder/config/test/plan sources beforeStageA; save exact source hashes,
builder commit, original benchmark hashes, support evidence and failures. Refuse
existing output directories. Source/builder exception is preserved, not erased.
Deliver fullMD plusHTML and a separate learning for nontrivial findings. Register
this experiment and artifacts as training_eligible=false/production_eligible=false.
If developmental gates fail, reject this configuration and choose a separately
registered next hypothesis; if they pass, freeze before designing genuinely
prospective evidence. No historical pass alone completes the profitability goal.

## Official implementation sources

- pandas2.3.3 backward/ordered matching:
  https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
- pandas2.3.3 trailing quantile:
  https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.core.window.rolling.Rolling.quantile.html
- NumPy2.0 seeded draw semantics:
  https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html

Repository protocol takes precedence over generic experiment templates: keep
chronological folds, never random train/test splits. Existing pandas2.3.3,
NumPy2.0.2 and Python3.9 environment unchanged; no DOE dependency installation.
