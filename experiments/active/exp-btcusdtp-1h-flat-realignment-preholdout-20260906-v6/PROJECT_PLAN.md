# V6 — stay flat until native-5m alignment after the first hourly K2

Owner authorized autonomous strategy research, not live trading. V5 is a
completed negative mechanism experiment. Its 33 initially opposite K2 entries
included nine hard stops before the first alignment. That observation motivates
this single new entry-timing hypothesis; those future nine or future aligned24
are never used to select entries. This plan precedes any new price replay.

## Frozen contrast and unit

Two K2 policies only: `k2_immediate` and `k2_flat_alignment`. Both retain V5's
native5m SMA40(HL2) genuine aligned-to-opposite exit, fixed initial K1 extreme,
20bp round-trip assumption and original mother+72h absolute final deadline.
No direct-entry variant, MA grid, partial TP, trailing-stop or extra filter is
part of this experiment. The counterfactual unit is each of the same251 original
hourly K1 mothers, not only the55 eventual K2 executions.

Reuse byte-pinned V4 case/control mothers, first-K2 requests and terminal records.
All89 old control requests remain, including21 with invalid risk at the old K2
open. Re-alignment may change the execution price, and risk is checked ONLY at
the first actually attempted open. Controls keep their old fixed initial_stop:
never re-anchor it to a new entry or transfer risk again after waiting.

## Exact entry clock

1. Original V4 no-K2 terminations are inherited unchanged; no second K2 search.
2. For each emitted K2, inspect the exact completed native5m bar ending at its
   original decision boundary. Colour means supplied HL2 versus SMA40 state,
   not candle-body direction. If known aligned, enter at that boundary's raw
   next-bar OPEN; result must match the immediate policy exactly.
3. If known opposite, stay FLAT and inspect each subsequent completed5m bar,
   in chronological order. Enter only at the FIRST known aligned close, using
   the raw5m OPEN starting at that same timestamp. Consume one attempt even if
   its risk is invalid; no retry at a later, more favourable price.
4. Deadline is original mother decision+8h, inclusive. A confirmation exactly
   there is processed before expiration; a K2 already at+8h and opposite
   immediately expires. No K2+8h extension or post-deadline confirmation.
5. Missing/invalid management colour or completed-source data before the first
   confirmation means the first opportunity cannot be determined: censor as
   unknown, do not skip and retroactively call a later bar the first. Raw gaps
   or source-segment changes censor. Observed-through only admits the new OPEN,
   never that bar's later high/low/close. Duplicate/ambiguous input times fail.
6. Touching the K1 extreme while FLAT does NOT cancel the setup (same V4 rule).
   No extra hourly invalidation is introduced after K2 has already emitted.
   This intentional one-variable isolation may permit structure recovery after
   a previous extreme touch; report it, do not present it as validated trading.
7. Initial_stop and signal_atr remain immutable. Actual entry price/risk, all
   exits and excursions are recomputed. Execution retains hard-stop priority.
   Actual remaining duration is integer4320 minus total waiting minutes, not
   a rounded floating-hour duration. This introduces no longer holding horizon.

## Denominators, controls and inference

Every original mother retains one terminal record. Known no-K2, observed
`expired_no_alignment`, and attempted `entry_invalid_risk` are non-entry zero.
Missing source, unknown colour or censored held positions are NaN, never zero.
Serial diagnostic reserves unknown paths until mother+72h; pending starts at
the original mother time, not the delayed entry. Track the changed availability
of the single pending/position slot as well as independent-event returns.

Same462 fixed control mothers for154 cases, using the original matching strata:
same asset, month, UTC6h bucket, causal ATR tercile, known5m/hour colour and signed
hourly slope. Both policies apply identical waiting/expiry/entry/exit rules to
controls. No rematching or result-conditioned coverage repair. Coverage61.35%
is known below the unchanged90% gate; case executions cannot exceed55<80. This
is a falsifiable mechanism test, not a nominee or independent profitability
confirmation, regardless of its mean. Do not spend audit data on this design.

Report both per-completed-trade and per-original-mother net/gross bp, PF, win
rate, four chronological half-year folds, support, cost+10bp stress, leave top2,
same-policy matched excess and serial single-pending results. No compounding
claim from sums of event-bp. Range/ATR is the unchanged single-feature AUC and
top-decile baseline; there is no trained ranking model/AUC to invent.

One planned paired contrast: flat-alignment minus immediate, across all251
mothers. Use9999 calendar-month cluster bootstrap95% interval and one-sided
month sign-flip, seed20260906; no multiple candidate selection this round.
Also disclose SD/median/IQR, zeros, lag1 monthly dependence and all unknowns.
These exploratory intervals do not remove sequential search/data reuse or
cross-month dependence. Match p<.01 and positive paired p<.01 remain necessary,
not sufficient; unchanged economic/sample/coverage gates must all pass before
any future properly designed verification could be proposed.

## Failure and opportunity accounting (predeclared)

- Original entry-known aligned/opposite/unknown cohorts, not future winners.
- Request emitted, expiry, first-attempt invalid risk, data/colour censor.
- All altered entry delays, directional price change, actual risk/ATR,
  held minutes, early colour exit, hard stop, fee flip and >=1R giveback.
- Compare full mother ledger, unchanged same-time requests, same-executed
  timing effect, participation effect, missed former winners, avoided losers,
  former losers now winning, new losers, and every changed individual path.
- Count K1-extreme touches during the known flat interval as descriptive only;
  this cannot change entry decisions or produce an outcome-selected subgroup.

## Validation and scope

Synthetic mirrors, all97 possible5m total-delay steps, boundary/expiry priority,
first-confirmation no-retry, old invalid-risk controls, raw gaps, invalid colours,
prefix/suffix mutation and exact absolute deadline. Baseline all old trade
columns must reproduce V5; initially aligned actual entries/outcomes must remain
identical. Freeze all executable sources/config/plan in main BEFORE new output.

Only reused2023--2024 development prefix. No2025+ price load, repository
holdout, new source, dependency installation, training, ACTIVE/frozen, VPS,
TradingView, order or position mutation. All negative results stay recorded.

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse*.py tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py
git branch --show-current
# Commit explicitly owned builder/config/tests before outcome generation.
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.hourly_impulse_realign_research
```

Source basis: [pandas2.3.3 exact durations](https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html),
[known-at matching](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html),
[TradingView confirmed bars](https://www.tradingview.com/pine-script-docs/concepts/bar-states/).

## Next action if this fails

Do not respond with another arbitrary exit threshold. Use entry-time price and
participation decomposition to decide whether continuation confirmation creates
an edge or merely enters late. A subsequent entry episode/continuation hypothesis
must have separately frozen support, controls and genuinely later verification;
past favourable subgroups are not an executable feature.
