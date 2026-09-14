# V8 ETH raw-trade descriptive summary

Owner 2026-09-14 asks for ETHUSDT perpetual 3m/5m historical longest stop-loss streaks, win rates, and 3R counts before considering capital management.

Read only the SHA-pinned frozen baseline trade ledger from exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3. No OHLCV, signal replay, parameter search, cash-policy filtering or fixed-3R exit is authorized by this descriptive builder. The optional question about fixed-3R versus existing-V8 3R statistics is pending; this builder remains useful under either answer.

Report both gross/net wins; losses versus losing stops versus initial stops; conservative recorded MFE at 1/2/3/5/10R and realized gross/net returns; longest runs and timestamps. Break streaks at historical replay-fold boundaries. Preserve different venue/date coverage and censored-trade exclusion.

This is the first descriptive reuse in this config of already exposed outcomes, not a new independent holdout evaluation. The source V8 configuration is unchanged. Fixed-3R replay is a separate configuration and cannot be inferred from MFE. No training or production eligibility.

Validation: source SHA,3525 row count,duplicate/censor checks,0.2% cost arithmetic,three synthetic tests for streak and stop semantics. No newly sampled market controls or classifiers; the original matched-control results can only be cited as original evidence.
