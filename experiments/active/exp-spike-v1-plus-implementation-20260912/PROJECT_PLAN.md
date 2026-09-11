# SPIKE V1+ indicator implementation

Owner authorization (2026-09-12): implement the stronger V1 derivative using
lighter agents because quota is limited. Two Terra High roles handle the new
Pine source/probe and a bounded independent review. Root handles integration
and native TradingView validation. No nested delegation or new worktree.

## Contract

Create separately named SPIKE 强劲爆发 V1+ from the latest V1 display.
Keep frozen V1, display V1, V6, historical results and monitoring unchanged.
Master disable compares the display derivative, not the frozen long-only backtest.
Preserve original qualifiers, dual directions, confirmed signals, configurable
white signal bodies/wicks, a visible zero axis and bounded RR/dashed milestones.

Default additions: joint overheat rejection and staged protection, from 1R to
-0.5R, then at 2R to an estimated cost buffer while retaining the 2R/4ATR trail.
Optional additions: risk-width rejection, early structural failure, stagnation
protection, same-side cooldown and causal post-launch retest confirmation.
Raw opposite signals end old references independently of new entry filters or
retest waiting. Prior stops/gaps have priority. New protection is monotonic and
active on the next bar. References use confirmation close, not actual fills or
orders. Joint overheat rejection consumes that qualified episode.

## Validation and limits

Run focused contract tests and injected native Pine fixtures, then compile/save
a separate TradingView script when the available UI surface permits it.
The Mac reported a competing PC session: do not force another device logout
or repeatedly reconnect. Existing loaded chart use is technical validation.

No new market fetching, parameter fitting, economic backtest, training,
notifications or orders. Synthetic QA does not consume historical holdout.
The full 18-module design remains a research backlog; listing metadata,
market breadth and global account risk require Python/account context.
Do not claim optimal thresholds or improved profitability from software tests.

training_eligible: false
production_eligible: false
