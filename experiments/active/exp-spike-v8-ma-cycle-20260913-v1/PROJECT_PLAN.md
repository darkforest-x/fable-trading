# V8 MA cycle state diagnostic — frozen plan

This study tests the COMP-derived sequence as a causal **state description**
on the already-frozen V8 event ledger.  It does not rebuild V6/V8 signals,
change costs/exits, simulate a new account, train, promote, or operate any
production surface.

## Inputs and time discipline

The sole event/outcome input is the committed V8 entry-process `full_v1`
`same_entry_evidence.csv.gz`: 113,295 V8 events and 94,180 `scoring_closed`
outcomes after its existing development cross-split purge.  Raw authenticated
caches supply only per-bar MA-cycle fields.  Event state is read at
`signal_bar_open`, which completes at the V8 confirmation close.  Development
and reused validation retain the input table's period and purge semantics;
the latter is authorized holdout-era use #1 for this configuration and
nonblind.

## Causal bar fields

The six lines are SMA/EMA 20/60/120.  Group center is `(SMA+EMA)/2`; group
slope is center at `t` less center at `t-3`.  Directional order compares all
12 cross-period pairs only.  Width is saved as absolute, divided by close,
divided by current ATR, and divided by an episode ATR frozen when that episode
becomes armed.

The compression threshold at `t` is the 20th percentile of **only** the prior
256 valid `width/close` observations.  Three consecutive qualifying bars form
an armed consolidation on the third bar.  Every candidate range is the
strictly preceding 12 bars; launch freezes that bar's high/low range.  Gaps,
invalid OHLC/MA/ATR and insufficient causal history produce `unknown` and
reset every episode counter.

## Fixed transition order

At a valid bar, existing state has priority over any new episode:

1. `launch` first fails if its close returns inside its frozen launch range;
   it becomes `awaiting_compression` and clears the compression run.
2. Otherwise, `launch` can become `expansion` only when its direction has
   order score at least 9 and both center gaps are positive and larger than
   three bars earlier.
3. `expansion` can become `reconsolidation` only after three consecutive bars
   with width below 70% of that episode's running maximum and non-unanimous
   directional slopes.  That transition clears the compression run.
4. Only a bar that did not consume an old-state transition may count toward a
   new three-bar compression run.  `unknown`, `awaiting_compression` and
   `reconsolidation` arm a new episode on its third qualifying bar.  An armed
   episode is valid only through 12 bars after its most recent qualifying compression bar; continued compression updates `last_compact_i` but never changes that episode ID or start. On expiry it becomes `expired/awaiting_compression` and needs a new three-bar compression. Twelve is the already-frozen consolidation range length, not an optimized timeout.
5. Only an episode armed **before** this bar may launch: all three slope votes
   align, body open and close lie fully beyond the six-MA envelope in that
direction, and close breaks the frozen preceding-12-bar boundary.  Order 12
and immediate width expansion are explicitly not launch requirements.

`order12` is a recorded milestone, never an entry gate.  Unknown, transition,
failed-launch, reconsolidation and mismatch observations remain separate;
they are never relabelled from future expansion.

## Prespecified comparisons and process diagnostic

The two independent descriptive comparisons are `same_direction_launch` versus
all other states, and `same_direction_expansion` versus all other states.
Tables report period × timeframe × side × state, monthly coverage, win rate,
PF, mean R, realized 10R counts/retention, and loss tails.  No state is chosen
or promoted after results are observed.

For each V8 event, a separate 48-bar post-confirmation table follows **only
the episode already active at that event**.  If expansion/order12 is already
present in the matching direction, it is `already_at_confirmation` with delay
zero.  Otherwise the scan cannot leave that episode; it records no active
episode, original exit/SL before milestone, end-of-cache censor, no milestone,
or a milestone's next-open price cost in original initial-risk R.  It never
backfills a later cycle's expansion as an old V8 confirmation.

Existing frozen random controls are reported only with their original missing
control-exit-clock limitation.  A second, non-random descriptive comparator
matches each target independently to a non-target V8 event by stream, period,
calendar month, side and a causal (preceding-256-bar) ATR/close quintile;
nearest confirmation time breaks ties deterministically.  Missing matches are
retained.

## Implementation clarifications locked before scoring

A long launch fails on `close <= frozen_launch_high`; a short launch fails on
`close >= frozen_launch_low`.  This includes a move through the whole frozen
range, not only a close geometrically inside it.  Expiry clears all active
episode fields while the next episode counter stays monotonic.  Every rolling
slope, compression/range history and causal volatility bucket restarts at an
invalid bar, cadence break or declared data gap.  Cached MAs are inherited
inputs; this study does not claim to reseed them or reproduce Pine byte-for-byte.

All 113,295 V8 events retain a state and two availability records.  Missing
frozen-trade references identify unexecuted events and make confirmation cost
unavailable.  The `expansion` and `order12` records are separate milestones.
A scan ends as `episode_ended` when its original episode ends; it cannot use a
later cycle.  Only the 94,180 inherited `scoring_closed` rows enter outcome
tables.  Each prespecified comparison has its own full complement: baseline,
launch/non-launch and expansion/non-expansion.
