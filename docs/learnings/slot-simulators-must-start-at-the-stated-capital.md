# A slot sizing simulator must start at the capital it reports against; check the total before the first trade

- **Problem**: The Monday 1618 slot simulators (`monday_limit_v3_slots.simulate`, `monday_limit_spike_opt.simulate`) reported "v3add 20 slots × 3x = 12.6 / 9.3 / 5.4 / 6.2x" and, run continuously from 2020, 152x. A trade-by-trade replay requested by the owner showed the account at 1945.53 USDT after its first 4.47 USDT loss.
- **Dead ends**:
  - Trusting per-period tables because drawdowns and win rates looked sane. A doubled starting capital barely changes drawdown or win rate; it shows up only in the level of the total.
  - Comparing slot settings only with each other. The error scales all slot rows by about the same factor, so rankings within the family looked consistent. The ranking against linear and all-in sizing, which start correctly at 1000, was wrong: corrected, v3add 20 × 3x (76x from 2020) no longer beats v3 linear 1x (86x).
- **What worked**:
  - Replaying one path with every equity component printed (pool, slot balances, total) after each fill. The first row already showed 950 + 20 × 50 = 1950.
  - The fix puts the whole 1000 USDT in the pool and starts slots empty. Regression tests pin a one-trade result (1000 + pnl), flat trades (1000) and a resize that waits for the real doubling (`tests/evaluation/test_monday_slot_simulators.py`).
- **General rule**: For any sizing replay, assert that equity before the first trade equals the reported base (1000), and test one trade whose PnL can be computed by hand. Report multiples against the same base the simulator starts from.
- **Related**: `yoyo/evaluation/monday_limit_v3_slots.py`, `yoyo/evaluation/monday_limit_spike_opt.py`, `yoyo/evaluation/monday_limit_return_opt.py`, `experiments/active/exp-monday-limit-return-opt-20261027-v2/`, [per-trade-edge-does-not-compound-when-trades-overlap.md](per-trade-edge-does-not-compound-when-trades-overlap.md).
