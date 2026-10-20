# Bounded 5m parameter study

Owner asks “5min 级别有更好的参数吗” after the four-coin entry comparison. Research only; indicator defaults are unchanged.

- Reuse the exact four-coin candles, candidates and same-symbol/side/week/segment/causal-volatility controls from `exp-range-entry5m-20261020-v1`. OKX BTC/ETH/PEPE; Binance ZEC. No downloads or hashing.
- Compare 13 frozen arms: baseline plus one-at-a-time changes to stop window (3/8/12 vs 5), buffer (0.025/0.10/0.20% vs 0.05%), trail activation (3/8/10R vs 5R), or trail distance (2/3/6ATR vs 4ATR). Do not combine axes or search more parameters after seeing results.
- Keep Stoch 5/3/3, H1 watch, all independent subsequent matching 5m entries, 15m close management, ATR14, armed opposite H1 exit, and fixed 0.2% entry-notional round-trip cost. No risk cap or breakeven changes.
- Select separately by highest total net R in the existing 2024 development segment with at least 100 trades; ties lower realized R drawdown then arm name. Recheck 2025 and 2026 separately and combined. Prior research has seen these dates; do not call them untouched holdout.
- Report total and mean net R, matched random results, costs in R, gross 10R/20R and boundary marks. All-history best is descriptive only. An improvement while still negative is not a profitable parameter set. Independent overlapping R is not account return.
- Reuse the corrected C++ screening kernel, exposing its existing trailing constants as parameters with unchanged defaults. Check every baseline against all cached Nautilus trades, deterministic and extreme scalar probes for every arm, then execute each development-selected arm through the existing Nautilus config runner and require full path parity.
- Run two native subprocesses at most; keep routine output compact, preserve ledgers, summaries, timings, configuration and source lineage. No model training, promotion, live orders, UI or Pine change.

Reproduce with the installed isolated Nautilus Python:
`python -m yoyo.evaluation.range_parameter_search --phase screen`
`python -m yoyo.evaluation.range_parameter_search --phase native`
