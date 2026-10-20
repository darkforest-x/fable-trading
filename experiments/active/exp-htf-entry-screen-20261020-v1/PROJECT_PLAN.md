# HTF entry screen (fixed-horizon markouts)

Owner request 2026-10-20: "那换4H/日线级别帮我筛一下哪些入场有方向收益".

Frozen before the first run:

- Data: OKX USDT-swap 15m in `data/kline_deep` (2022-01-03 .. 2026-07-12), every coin present; complete UTC 4H/1D bins only; 200-bar warmup.
- Rules (13, standard textbook parameters, no tuning): dc20, dc55, mom20, ema20_50, sma200, rsi2, shock, bb_squeeze, ma_dense, monday_break, monday_reclaim, prev_week_break, vol_spike. Long and short reported separately.
- Markout: side x (close[t+h]/close[t]-1), entry at signal close. Primary horizon 3 days on 4H (18 bars), 7 days on 1D; secondary 1d/7d (4H) and 3d/14d/30d (1D) descriptive only.
- Events de-overlapped per coin/rule/side at the primary horizon.
- Cost 20bp round trip unchanged. Baselines: same coin, same side, all bars in the same period half (drift) and same quarter (timing); no direction inherited from a future setup.
- Discovery 2022-01..2024-03, confirmation 2024-04..2026-07; windows crossing the split purged.
- Gate per period: n >= 50, mean markout >= 60bp (3x cost), month-block bootstrap 95% lower bound of net (markout-20bp) > 0 and of excess-over-drift > 0. A cell is a candidate only if it passes in both periods. 52 cells per period; failures reported.
- Limits: markouts are not executable PnL (no stops, funding, slippage beyond 20bp, capacity); coin list is survivors as of 2026-09 (survivorship); cross-coin correlation handled only by month blocks.

Run: `.venv/bin/python -m yoyo.evaluation.htf_entry_screen --config experiments/active/exp-htf-entry-screen-20261020-v1/config.json`
