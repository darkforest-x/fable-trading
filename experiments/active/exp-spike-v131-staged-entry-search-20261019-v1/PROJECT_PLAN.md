# Entry allocation search registration

Owner authorization, 2026-10-19: “你都试试，找到收益最高的参数”, following half entry plus a resting remainder at -0.5R and recovery-confirmation alternatives. This explicitly authorizes the joint research grid of entry fraction, adverse depth, entry method and waiting duration in config.json. No stop, trail, cost, model or production setting changes.

- Unit: the same 21,547 archived closed V13.1 opportunities before symbol exclusions, plus the same eligible matched random opportunities. Preserve original occupancy even when an entry is skipped; no replacement signals or risk reallocation.
- Search response: arithmetic net R summed over original opportunities. Original full quantity is the maximum; R remains original entry-to-stop risk times original quantity. Fees retain 20bp round trip on each filled leg's entry notional. This is not account return or a portfolio simulation.
- Exits: original entry/R drives 2R arming and original absolute stops; 1H 2ATR, others 4ATR. Do not re-anchor exits to the blended entry. Controls receive identical entry rules and recover their actual 1H exit.
- Resting limit: submitted at original entry open; rounded away from original entry to the price tick. Primary fill requires one-tick penetration, with price improvement at qualifying opens. Cancel before an opposite/gap exit and when protection is already at or beyond the limit. The model assumes atomic exit/cancel priority and no queue/latency/capacity constraints.
- Reclaim: after reaching the adverse depth, a surviving chart close on the favorable side of original entry schedules the remainder at the next open. An intervening gap/opposite exit cancels it. Same-bar adverse touch then recovery close is observable at close. Lifetime counts entry bar as bar 1; expiry 0 means until original exit.
- Time split: select only opportunities with entry AND actual exit before 2025; later entries are the diagnostic check. Crossing trades are excluded from both. Matched comparisons within a split require both target and control belong to it. Full-history best is explicitly retrospective; prior research has already seen these data.
- Grid fixed before this search. Each candidate has a constant initial fraction; endpoints are included because a frozen-exit fixed-quantity mean-return objective is affine in that fraction. Report the owner's 50%/-0.5R proposal, best 50% method, and full/early-selected winners. Do not claim an optimum outside this finite grid.
- Validate lifecycle edge cases, all baseline target/control parity, exact source counts, independent result aggregation, weekly-block uncertainty for fixed selected policies, and touch-fill sensitivity. Record failed checks; no market data downloads or content hashes.

Reproduce after builder commit:
` .venv/bin/python -m yoyo.evaluation.spike_v131_staged_entry --workers 4 --output experiments/active/exp-spike-v131-staged-entry-search-20261019-v1/results_reproduce `

Code and raw results are required experimental records; no separate report is requested.
