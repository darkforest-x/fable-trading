# V12.8 selective entry: causal relative-volatility upper quintile

Frozen 2026-09-23 before feature-conditioned outcome inspection. Owner asks to begin research toward fewer entries, higher net win rate and realized high R, on both previously requested 15m and 1h. This is historical exploratory research; the aggregate outcomes and this market window have already been examined. No claim of an unseen validation set.

## One variable and rationale

The only new entry variable is the signal bar's SPIKE ATR/close divided by its own rolling 96-bar mean (current bar plus previous 95, full window). This measures change relative to the same instrument, not a ranking of volatile coins. Use the parent's exact ATR; do not import L2 or claim identical ATR seeding. Prior `p_volatility_axes_20260818.md` motivates an axis, but its different detector/obstacles and in-sample evidence do not validate SPIKE.

At each bar, calculate the 20/40/60/80 percentiles of strictly earlier ratio observations over 30 calendar days worth of complete chart bars (2880 for 15m, 720 for 1h). Require a full finite history and restart windows after any chart gap. Ties go to the lower bin. The sole treatment, high20, admits ratio strictly above the prior 80th percentile. It is not guaranteed to retain 20% of SPIKE signals. No return-informed threshold search, fitting, direction choice or coin selection. Five-bin descriptions are secondary diagnostics of baseline trades; no best-bin policy chosen after results.

## Execution and original state

Reuse all frozen window candidate decisions from the parent, including those skipped because of occupancy. Rebuild only the parent's bars/facts/raw opposite signals and fixed-entry exit engine; do not recompute the expensive line detector or change candidate events. Parent trade and control hashes, input manifest and code identities must be checked.

The treatment begins at the window start. Both baseline and high20 inherit the same parent position already open at that time. Reconstruct an affecting carry position from pre-window blocking_trade references and replay its unchanged exit. This is a policy activated on July 23, not a claim of filtering warmup history. An old position with no blocked window candidates has no effect on the replay. Verify the unfiltered reconstruction matches every parent window entry, status and outcome before using filtered results.

Replay each independent candidate once with original five-bar/0.2ATR/min2ATR initial stop, close2R/4ATR trail, raw opposite next-open exit, tick handling and 20bp unchanged. Select candidate outcomes sequentially under each policy so freed occupancy can create later entries. Censors stay censors. Preserve original risk denominators, MFE lower/upper conventions and adverse gap execution.

## Controls and comparisons

Four predeclared cells: ordinary both sides and joint longs, each at 15m/1h. Same deterministic candidate key and parent seed select one same-symbol/side/week/fold/causal ATR-level-bin random entry, excluding the actual signal bar. No redraw on failure or censoring.

Produce two fixed control pools: all parent-ready bars (same baseline comparator, exact parent control parity required), and high20-ready bars. The latter is the single-feature entry baseline and the PRIMARY test for treatment excess: does SPIKE add value beyond the expansion condition? The all-bar control shows performance against ordinary matched random entry. These are diagnostics, not executable capital accounts.

Report full, earlier, later, and crossing-boundary strata; earlier comparison requires both target/control exits before Aug23. Bootstrap UTC week blocks to retain cross-symbol dependence. Main positive-excess p uses exact week sign flips when <=16 weeks and Holm across the four high20-versus-high20-random cells. Also report net win-rate and mean net bp/R change vs original with paired week resampling. Interpreting selected tails or many subgroup tables as independent discoveries is prohibited.

## Required outputs and interpretation

- Candidate/filtered/occupied/taken/closed/censored counts and frequency reduction on identical coverage.
- Net win rate, mean net bp/R, mean win/loss R, PF, >=3R/5R/10R counts/rates, and before/after period consistency.
- Fixed baseline cohorts by causal quintile; retained/lost/new actual serial high-R winners, and fixed-versus-serial opportunity differences.
- All-bar and high20 single-feature random controls alongside directional outcome tables, with available n and uncertainty.
- Honest gaps: source universe coverage, partial feature readiness, inherited opening state, two-month/10-week uncertainty, prior viewed data, native Pine event parity unverified. No account return or deployment claim.

Lower frequency alone is not a success. Report whether net win rate, average net R/bp and realized high-R rates jointly improve, plus the cost in lost large winners; no unapproved absolute target or automatic promote. New training, Pine defaults, trading thresholds, exit rules, cost and real positions remain unchanged.

Builder and behavioral tests must be committed before running market construction. Focused tests cover feature prefix invariance/gap reset/ties, initial occupancy, filtered-event serial admission and failed-control censoring. Smoke BTC/ETH then the fixed 638 pool; all stream receipts hash inputs and outputs. A failed trial remains preserved.

## Prior-evidence addendum before any new market construction

The bounded historical review found `analysis/p0_pine_eth_15m_start_label_audit_20260821.md` had already rejected a fixed `atr_pct_ratio96 >= 1.0` gate for ETH15m: development 2023 win rate 14.46% to12.77%, 2024 19.28% to8.16%. This study is therefore a new causal partition/current V12.8-window check of an already researched axis, not a first discovery. The original whole-pool qcut and L2 result are not deployable evidence. No change to the sole high20 policy follows from this review.
