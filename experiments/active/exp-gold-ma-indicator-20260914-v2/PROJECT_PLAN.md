# YOLO human-box indicator alignment V2

Owner correction (2026-09-14): the requested indicator must recognize the previously manually labeled YOLO gold morphology. V1 MA-drift semantics were rejected.

## Frozen first-pass scope

- Reference snapshot: output/offline_tasks/owner_review_exports/20260908T130556806730Z/answers.jsonl. Human annotations only, effective_answer=true, no event conflict, no cancelled/draft/prediction fallback.
- Join original review_id to datasets/owner_box_refinement_20260907_v1/manifest.jsonl. Geometry comes from effective human rectangles, not the inherited middle-half proposals. A research mapping includes candle centers inside the horizontal human rectangle and preserves original rectangle coordinates.
- 323 effective owner boxes (166 long / 157 short), 33 explicit no-target answers and 12 unresolved conflicts at this frozen snapshot. These are research references; admission as new Gold/training remains false.
- Numeric source is the exact close-based six SMA/EMA 20/60/120 used by this review pack, not the later HL2 proposal-only pack. Verify source timestamps and full main-window OHLC/MA digest before use. No images or OHLC at or beyond 2026-05-04T00:00:00Z.
- Development observations: main window closes before 2025-07-01T00:00:00Z. Later pre-holdout rows are held back until a numerical rule is frozen. Inherited train/val labels are provenance only. Dependencies overlapping the time cut are excluded, not randomly split.
- First establish V1 failure attribution against human rectangles. Then define one initial V2 morphology rule in writing before evaluating the later period; modifications after first evaluation are separate versions, never overwritten results.
- Score actual marker timestamps inside the human box and separate delayed hits at +1..+3 and +4..+5. Never backdate a signal to a box that was recognized later. No-target samples are counted by any displayed signal in their entire visible main window. Unknown background remains unlabeled, not a fabricated true negative.
- Optional background control shifts the same human box to an earlier disjoint same-symbol region; these controls measure localization specificity only, not gold precision.
- No model training, performance/returns/backtest, new dataset admission, holdout reading/scoring, active/frozen changes, deployment, live alerts or orders.

## Acceptance evidence

- Per-task lineage, effective annotation ID, raw box, derived candle interval, source prefix audit, exact matching/read failures, observed signal time and delay, and named failed predicates.
- Full denominator including unavailable/conflicting/unmapped cases; no silent dropping. Compare V1 and V2 on the same mapped set.
- Pine native compilation plus Python/PineTS parity on bounded source fixtures; truncation and future mutation tests; same closed-bar rule in 1m/3m/15m.
- Chinese Markdown report, immediately rendered to HTML. Declare that 15m gold recall does not validate 1m/3m transfer or profitability. Save a revised private TradingView script only after describing its measured evidence and limitations.

## Initial numeric candidate A (before source replay)

This is a new transparent morphology proposal, not a change to an existing strict/expanded production preset. It does not claim to reconstruct YOLO.

- Six close-derived SMA/EMA20/60/120; Wilder ATR14; 360 contiguous completed bars required.
- Prior 12 bars contain at least three consecutive bars whose six-line width divided by their preceding ATR is <=2.0.
- At least one of the preceding 8 candle bodies intersects its six-line envelope expanded by 0.25 preceding ATR.
- Topology over prior 12 bars: either at least two undirected pairwise order flips, or the last completed six-line width <=90% of its width 8 bars earlier. Equality carries prior sign; no invented same-period directional cross semantics.
- Direction at current bar: both SMA20 and EMA20 move in the same direction compared with 3 bars ago; at least 3 of the six slopes agree; close is beyond the fast-pair edge in that direction; the three-bar directional close displacement is >=0.4 preceding ATR.
- Current close is no farther than 2.5 preceding ATR outside the full six-line envelope; the current TR is <=3 preceding ATR. No additional requirement that price has already spent six bars below/above all MAs.
- One displayed marker at the first qualifying bar in a direction; rearm only after that direction has had three consecutive nonqualifying bars, with a minimum separation of 6 bars. Both sides use symmetric arithmetic because current actual human answers explicitly include both directions.
- A later confirmation may occur at most five bars after the marker on a directional close beyond the frozen high/low of the preceding five bars including the marker. It is separately timestamped, never relocates the first marker. Expire after five bars or cancel on a close beyond the opposite fast-pair edge.
- For V1 vs candidate comparison primary hit is a same-direction displayed marker inside the exact human core; late hit1..3 and4..5 separately. Initial threshold choices are declared design values, not optimized financial settings.
