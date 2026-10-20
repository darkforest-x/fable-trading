# ETH H1 timing and causal alt selection

Owner's 2026-10-20 hypothesis: ETH H1 green checks identify local lows; hold16h and find which alts to buy at each signal.

Fix confirmed Stoch5/3/3 below20 crossover, next H1 open entry, close after16 full hours, 20bp original-entry-notional costs. No stop, leverage, funding, or partial exits in this event study; MAE is reported. Overlapping events are independent, not an account.

Reuse registered54 OKX perpetual convenience-pool history (52 alts excludingBTC/ETH); no hindsight removal. Supplement only missing recent dates with existing src.data.fetch_okx, research output directory. Complete H1 from4 consecutive15m bars; validate overlapping OHLC. Native volume is used only relative to each coin's own past, never as cross-coin USDT turnover. This fixed surviving pool is not a historical census.

Only selection rule varies. Top3 equal slots, alphabetical ties: highest24h return relativeETH; lowest24h; positive-correlation>=0.3 highest168h beta; positive4h return times relative4h volume; confirmed own bullish check in last4 bars ranked4h return; lowest168h realized volatility. Insufficient slots remain cash. Eligibility requires173 complete past H1 bars (168h volume baseline preceding the4h signal window) and positive trailing volume. No future return determines membership.

Select rule on2023–2024 mean net event return, purge16h across split. Freeze selection before reading2025+ scores; show2025,2026,October separately. Allhistory is previously available research, not blind validation. Benchmark ETH and same-time equal-weight eligible alts (expectation of random3); same-coin matched random-time controls use same month, UTC6h bucket and causal volatility tercile with up to5 draws. Missing outcomes remain censored; never replace selected coins with successful survivors.

Reuse existing market_sync_shock.trade_returns and imacd_startup_research.inference; no new execution engine. Monthly clustered intervals, no classifier/training. Store every event's picks and realized outcomes. Focused tests cover completed bars, future invariance, exact16h endpoints, and missing selected outcomes. Commit builder before evaluation; no Pine/default/live changes.
