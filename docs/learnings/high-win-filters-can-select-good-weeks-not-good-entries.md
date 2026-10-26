# A filter that raises win rate can be selecting good weeks rather than good entry moments; compare with matched random controls

- **Problem**: The spike-HW rule (Monday 1618 limit-reclaim, score ≥0.6 and 15m EMA order ≥3) won 85-90 percent in the backtest. The owner asked whether the strategy is overfit.
- **Dead ends**:
  - Judging the filter by its own win rate and by selection on 2020-24 then confirmation on 2025-26. The EMA order lever passed that test (Holm p < .001) and held in every year, but both checks are blind to regime. They cannot tell whether the filter picks a better moment or just a better week.
- **What worked**: Codex's ledger already carried up to three matched random controls per signal: same symbol, NY week, direction and volatility bucket, with the signal's limit distance and target percentage. Pairing each signal with its controls gave:
  - all signals +32bp, CI [25, 39];
  - score ≥0.6 +127bp, CI [80, 172], significant in both periods;
  - spike-HW only +70bp, CI [−26, 170], p .07 (2025-26 p .19). Its controls also won 75 percent.
  - So the order ≥3 cut mostly selects weeks in which almost any entry at that distance worked.
- **General rule**: For every filter that raises win rate, report the matched-control win rate and the paired excess next to it. A large absolute gain with a small paired excess is a regime effect. It may persist, but it is not timing skill and needs forward data before sizing up.
- **Related**: `yoyo/evaluation/monday_limit_spike_controls.py`, `experiments/active/exp-monday-limit-spike-controls-20261027-v1/`, [win-rate-levers-and-profit-levers-are-different.md](win-rate-levers-and-profit-levers-are-different.md), [pool-internal-metrics-cannot-see-beta.md](pool-internal-metrics-cannot-see-beta.md).
