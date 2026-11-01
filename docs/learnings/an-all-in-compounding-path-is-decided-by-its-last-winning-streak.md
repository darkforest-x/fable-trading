# An all-in compounding path with refills is decided by its last winning streak, not by its history

- **Problem**: The live-allin3 booklet shows 1000U → 428,005U (2023 → 2026-09). The owner suspected it was "just the last few months" and asked whether it is real.
- **Dead ends**:
  - Reading the end number as the strategy's return. It nets 21 refills (20,204U of added cash). Before the last refill on 2026-07-07 the trading P&L was exactly minus the cash added: every gain was given back.
  - Reading the per-year account P&L. Under all-in 3x, the yearly figures mostly measure where a streak landed. The per-trade edge at a fixed notional tells a different story: 2023 +207bp, 2024 −156bp, 2025 −58bp, 2026 +93bp, median 0bp.
- **What worked**:
  - Replay the same candidate rows from every month start (2023-01 … 2026-09, 5 tie seeds) with the same allocator. Every start before 2026-08 ends near the same place (median net ~300kU, 96 percent positive). Whatever happens earlier is erased by blow-ups and refills, and all paths ride the same 48-trade run from 2026-07-07 (+427kU, of which September alone is +416kU).
  - Then check that run trade by trade:
    - The top 10 trades are 117 percent of the total.
    - The last one (ONUSDT, +127.5kU) is a data-end mark, not an exit.
    - Notionals reach 3.75x the touch bar's whole 5m quote volume, and 172x the prior day's median 5m bar.
    - 69 percent of the profit after the last refill is on coins OKX (the paper venue) does not list.
- **General rule**: For any all-in or refilled compounding account, report:
  1. the per-trade edge at a fixed notional;
  2. the month-start replay distribution;
  3. the trading P&L before the last refill;
  4. the notional against the bar's traded volume.

  A converging month-start distribution means the headline is one recent window. Read it as a lottery ticket, not an expected return.
- **Related**: `yoyo/evaluation/live_strategy_audit.py`; `exp-live-strategy-audit-20261101-v1`; source ledger `exp-pretouch-booklet-20261031-v1` (allin3-A_rest_causal). See also [backtest-venue-universe-is-not-the-trading-venue.md](backtest-venue-universe-is-not-the-trading-venue.md).
