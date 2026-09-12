# V1+ full-default backtest — frozen research design

Owner request: "现在不需要每次都关注1r这个事情啊。用子代理跑一下v1加强版本的回测。" The complete existing V1+ default configuration is the treatment. This is not a break-even parameter search. Earlier owner authorization permits all historical dates and research-only stop/exit changes. No live service, Pine, notifications, trading, training, or promotion changes.

## Fixed comparison and scope

- Treatment: current `spike_burst_v1_plus.pine`, SHA256 `5ec3809210c827cc6a5777dc7af59e3eb431f9bdf25a170933e92a3da0861da3`, both directions, all defaults unchanged. Joint overheating rejection and staged protection are enabled; retest, risk-width cap, structural failure, stagnation and cooldown are inactive. Owner authorizes this existing bundle as a whole, so it cannot identify each module's separate effect.
- Primary control: the same implementation with `enablePlus=false`, both directions. It is the V1 display-derived baseline, not the older long-only V1 ledger. Raw opposite confirmation ends an old reference independently of whether the new entry passes its entry filters.
- Historical original long-only V1 ledger is a separately labeled reference, never relabeled as this new control or as a bidirectional result.
- Frozen covered pool: 3,531 venue/timeframe/continuous-segment streams from `exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3`. Binance/OKX/Gate, 30m/1H/4H. This is a current-catalog covered subset, not every historical contract. Keep the upstream coverage denominator and exclusions visible.
- Reconstruct features and the full state from authenticated original source history where necessary. A short trimmed cache cannot establish the initial reference/episode state. No synthetic gap fills or future symbols added.
- Evaluation dates: 2024-09-10 inclusive to 2026-09-10 exclusive UTC; development/validation split 2025-09-10. These periods have been studied already and are reused chronological research, not blind samples. First default-config economic evaluation in this experiment; all subsequent material reruns must be recorded.

## Execution and capital

Signals are close-confirmed; entries and close-based reference endings execute at the next observed open. The prior operative protective stop is checked first, including adverse gap fills. An updated close-based protection is active on the next bar. Signal reference R and simulated fill R are distinct fields. Entry gaps through the initial SL are rejected and recorded. Stops/rounding and opposite-direction handling follow the pinned source, not V6's unrelated raw events. Censored positions and data gaps are explicit.

Fixed transaction cost is 0.002 times original entry notional per complete round trip, split 0.001 per side. Funding, spread/impact, order-book capacity and liquidation are not modeled. This inherited cost is an assumption, not a measured all-in fee.

One independent account per market/timeframe stream, initial 10,000 quote currency, target gross initial stop risk 1% of current equity, entry notional cap 1x equity, fixed quantity until exit. This sizing is a controlled ruler, not a risk optimization. Mark NAV at every bar close; positions carry across the year split. Report account returns and close maximum drawdown separately from summed event R. Do not add independent accounts' percentages or treat venue copies as independent economic opportunities.

## Required outputs and checks

1. Per-stream complete signal/state replay, entries, exits, fills, rejected entries, reasons and input/code receipts. Resume only on matching identities.
2. Period x timeframe x side x venue event summaries: raw/accepted signals, realized/censored trades, net win rate, net-return PF, mean/total net R, realized >=5R/10R, MFE >=10R, holding durations and exit reasons. MFE is not a realizable profit target.
3. Independent account paths and returns/drawdown; long/short trade contributions are attribution within the both-direction account, not independently simulated long-only accounts.
4. Exact same-entry comparison and baseline large-winner retention, plus new/removed entries after changed reference states. No claiming a fixed-event-row deletion is a strategy replay.
5. A bounded matched-entry null benchmark: outcome-independent deterministic sampling up to two signal events per stream/side/year, matched to the same stream/calendar month/side/causal volatility bucket. Target and random entry receive identical initial-risk, cost and protection-only outcome rules. If raw opposite-reference state is omitted from this null, omit it from both target and random and clearly separate the null from actual strategy/account results. Report unmatched counts and time-block uncertainty; do not infer alpha from selected examples.
6. Representative successes and failures and concentration by underlying/time event. No selecting the asset pool using subsequent gains.
7. Meaningful hand-built long/short, gap, old-stop-first, overheat, opposite-event, full-default and prefix-causality checks. No native Pine parity claim without actual runtime evidence. Builder commit precedes formal runs.

Only one research CPU worker by default. Reuse installed dependencies and existing source files. Terra High child owns replay/study/tests and run artifacts; parent owns this design, config, post-analysis/report and registry. Shared working changes must be preserved. No nested agents.

Final delivery: reproducible Markdown and rendered HTML report with all positive/negative findings and limitations; eligibility remains false. AUC/top-decile ranking are inapplicable to this hard-rule fixed configuration and must not be fabricated. No threshold selection based on these outcomes.
