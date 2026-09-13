# V8 entry evidence: BB episode, MA overlap, and completed higher timeframe

## Fixed objective

Read the frozen two-year V8 event ledger (113,295 events; 94,180 scoring-closed
outcomes) and determine whether three **independent** facts already known at
V8 confirmation stratify unchanged outcomes. This is not an entry/exit replay,
account simulation, signal rebuild, parameter search, or promotion decision.
Owner authorized all dates; this configuration records holdout-era exposure #1.
The post-split history has been studied before and is nonblind.

## Input and clocks

The builder authenticates all 3,531 frozen raw cache streams individually using
their receipt-bound cache SHA. It hashes the frozen entry-process manifest and
`same_entry_evidence.csv.gz`, the MA-cycle manifest and event-state table, and
the raw manifest. Event identity is `(stream_key, signal_bar_open, side,
period)`. Outcomes are joined only after labels are complete. The inherited
split uses confirmation close: development before `2025-09-10T00:00Z`; a
trade crossing that boundary remains excluded by the frozen `scoring_closed`
flag.

All new fields use closed bars no later than `signal_confirm_time`. `data_gap`,
invalid OHLC, and an incorrect timeframe cadence break history. Missing BB,
MA, or higher-timeframe evidence remains an explicit unknown coverage status;
it is not a false flag.

## Frozen independent labels

1. `bb_episode_directional_order9_run3`: scan only the preceding 12 bars for
the most recent contiguous `bb_compressed` run of at least three valid bars.
Within that selected in-window episode, test whether three consecutive bars
have order >=9/12 in the V8 direction. An order12 run is output only as a
diagnostic, not a second gate. If the selected run reaches the 12-bar left
edge, report `truncated_left`, actual known continuous start, and known total
length; the predicate still consumes only the in-window slice.
2. `bb_ma_tight_overlap_run3`: in that same selected BB episode, test whether
three consecutive bars are both BB-compressed and at/below the preceding-256
valid-bar P20 of six-MA width/close. The quantile resets at a gap/invalid row.
3. `opposed_completed_htf`: use 30m->1H and 1H->4H only from the same
venue/symbol cache; use 4H->UTC 1D constructed from six complete no-gap 4H
bars. Select the last HTF bar whose close time is no later than V8 confirmation.
It is adverse when its opposite directional cross-group order is >=9/12 and
its close remains on the adverse side of its fast-pair center. Fast/middle/slow
three-bar slopes, slope vote/recovery, and inherited md/sb are diagnostics only.

The three labels are never ANDed. The definition is selected before reading
outcomes and will not be adjusted for profitability or particular illustrations.

## Outputs and evaluation

`event_evidence.csv.gz` preserves every V8 event plus all coverage/episode/HTF
metadata. Only frozen `scoring_closed` rows enter outcome tables. For each
label, target and complete eligible complement are reported separately by
period, timeframe/side, and calendar month: n, win rate, mean/net R, PF,
realized 10R, original-10R retention, and signal retention. No dynamic account
claim follows.

Matching is descriptive, never randomized: within stream, period, month, side,
and the inherited causal ATR/close volatility bucket, choose nearest non-target
confirmation without reusing a control. Missing matches remain rows. Asset by
month block sign-flip p values and Holm adjustment across the three frozen
comparisons are exploratory, nonblind context.

## Validation

Synthetic tests cover strict prior-12 selection, a left-truncated BB run,
gap reset, causal MA-width prefix parity, exact completed-HTF as-of selection,
and unknown HTF coverage. Formal execution additionally verifies source hashes,
all 3,531 cache receipts, full event count, and unique event keys.
