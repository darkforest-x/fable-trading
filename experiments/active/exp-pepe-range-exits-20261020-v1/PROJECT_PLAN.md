# PEPE V2.8 exit research

- Owner authorization, 2026-10-20: “用这个回测一下 pepe 找到最优止盈止损”. This explicitly authorizes the joint stop-window, percentage-buffer, risk-cap and exit-policy research grid in config.json. It does not authorize changing delivered Pine defaults.
- Keep V2.8 H1 → 15m admissions, independent overlapping positions, and the frozen 20bp cost. Preserve the current 5-bar / 0.05% / 5R-arm / 4ATR / armed-H1-reversal baseline.
- Existing registered OKX native OHLC is reused for exact parity. The migrated catalog has long OKX 15m but only recent OKX 5m; Binance 1000PEPE is a different venue/contract and is not silently substituted. Obtain missing public OKX monthly history and current-month public API candles in this experiment only; no production data writes or content hashes.
- Freeze 600 stop/exit paths and four skip-cap filters (2,400 configurations) before looking at results. Select on 2023-06 through 2024 only, require 100 trades; report 2025 and 2026 independently. Show full-history descriptive optimum separately, never call it unseen validation.
- Each period resets positions at its boundary. Open positions are marked to market with cost at the boundary for ranking, separately flagged from natural exits. Features may warm up before a boundary, outcomes may not cross it.
- Include matched random entries and weekly block inference, single-factor neighbors, ambiguity counts, 10R/20R tail retention and cost burden. Report cumulative realized-R drawdown as an independent-trade statistic, never account drawdown.
- Commit builder before generating data/results. Verify native recent baseline, hand-computed ordering cases, prefix invariance and an independent scalar oracle. Preserve raw results/config/source receipt, update registries and Notion; no standalone report requested.
- No training, promote, live orders, account actions, chart changes, or new Pine version.
