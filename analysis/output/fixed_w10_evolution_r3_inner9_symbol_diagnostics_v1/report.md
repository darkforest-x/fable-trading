# INNER-9 symbol diagnostics v1

## Scope and safety

- **Scope:** optimization INNER only, decision times 2026-01-01 through 2026-03-31 UTC. April, sealed evaluation, and holdout were not read.
- **holdout_rows_read:** `0`; manifest also records `0`. No model was trained, changed, promoted, or used for orders.
- **Source:** `fixed_w10_evolution_r3_inner9_merged/candidates.jsonl`, SHA-256 `0091de6d9fd9b288d72493cf4494a07a31733b1c2ae00731b2ebf336d5059bd5`, equal to the merge manifest's `combined_sha256`.
- **Locked calculation:** threshold `0.25`, TP `5*ATR`, SL `2*ATR`, horizon `72` bars; threshold-first, same-symbol chronological 18-bar dedup; canonical SHORT `linear_short` resolver (conservative same-bar SL, barrier-price gaps, next-bar-open entry) and `SWAP_MAKER=0.0006` round-trip cost.
- **Month-end availability crop before selection:** Jan 41 / Feb 62 / Mar 0 candidate rows were removed because their full 72-bar label would leave that month. Selection counts were Jan 1,458, Feb 1,305, Mar 1,386 (4,149 total).

All return and drawdown columns below are maker net basis points. `SL` includes any conservative same-bar ambiguous SL (there were none separately). Win rate is the share of net-maker-positive trades; profit factor is positive net maker divided by absolute negative net maker. Drawdown is calculated in chronological portfolio order for the reported group.

## Pooled result

| Trades | TP | SL | Timeout | Mean net | Total net | Win rate | PF | Max DD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,149 | 1,147 | 2,633 | 369 | 9.07 | 37,614.14 | 35.43% | 1.091 | 25,138.84 |

## By symbol, pooled

| Symbol | Trades | TP / SL / TO | Mean net | Total net | Win rate | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| DASH | 461 | 119 / 291 / 51 | 24.22 | 11,165.44 | 36.01% | 1.203 | 9,236.63 |
| EGLD | 461 | 140 / 285 / 36 | 13.46 | 6,204.02 | 36.44% | 1.178 | 4,845.96 |
| GMT | 461 | 144 / 282 / 35 | 22.44 | 10,346.96 | 37.74% | 1.266 | 5,714.42 |
| GPS | 461 | 94 / 310 / 57 | -16.69 | -7,693.77 | 31.24% | 0.885 | 22,668.84 |
| HBAR | 461 | 128 / 291 / 42 | 9.76 | 4,498.45 | 35.79% | 1.136 | 3,638.00 |
| IOTA | 461 | 143 / 276 / 42 | 19.76 | 9,110.59 | 38.83% | 1.271 | 3,380.95 |
| ONE | 461 | 152 / 280 / 29 | 21.33 | 9,835.38 | 38.61% | 1.254 | 4,044.78 |
| ZK | 461 | 124 / 298 / 39 | 2.79 | 1,287.98 | 34.49% | 1.027 | 4,890.39 |
| ZRO | 461 | 103 / 320 / 38 | -15.49 | -7,140.91 | 29.72% | 0.888 | 11,546.74 |

## By month, pooled

| Month | Trades | TP / SL / TO | Mean net | Total net | Win rate | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Jan | 1,458 | 390 / 943 / 125 | 6.71 | 9,784.41 | 34.36% | 1.065 | 25,138.84 |
| Feb | 1,305 | 384 / 810 / 111 | 16.39 | 21,385.47 | 36.63% | 1.140 | 21,407.11 |
| Mar | 1,386 | 373 / 880 / 133 | 4.65 | 6,444.26 | 35.43% | 1.059 | 22,179.29 |

## Symbol × month: mean net maker bp (diagnostic only)

| Symbol | Jan | Feb | Mar |
|---|---:|---:|---:|
| DASH | 40.86 | 51.81 | -19.26 |
| EGLD | 19.39 | 34.67 | -12.76 |
| GMT | 15.58 | 36.22 | 16.70 |
| GPS | -40.15 | -49.11 | 38.52 |
| HBAR | 7.28 | 25.87 | -2.80 |
| IOTA | 25.86 | 13.65 | 19.10 |
| ONE | 2.44 | 43.09 | 20.72 |
| ZK | 9.30 | -2.01 | 0.47 |
| ZRO | -20.16 | -6.70 | -18.85 |

Each symbol-month count is 162 / 145 / 154 respectively. The pooled positives are broad but not uniform: GPS and ZRO are negative across their pooled samples, while DASH/EGLD reverse negative in March; this is a diagnostic observation only and is **not** a symbol-exclusion recommendation.

## Score distribution by resolved outcome

| Outcome | n | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|
| TP | 1,147 | 0.2697 | 0.2712 | 0.2973 | 0.3673 | 0.4480 |
| SL | 2,633 | 0.2699 | 0.2717 | 0.3062 | 0.3873 | 0.4781 |
| Timeout | 369 | 0.2705 | 0.2765 | 0.3449 | 0.4188 | 0.5038 |

Selected TP scores are not stochastically higher: their median is 0.2973 versus 0.3062 for SL, and their p90 is 0.4480 versus 0.4781. This is descriptive, not a threshold-change recommendation.

## Causal L2 feature univariate check: TP minus SL standardized mean difference

Computed only from the existing INNER L2 dataset rows matching selected TP/SL events: 1,092 TP and 2,555 SL rows; all have `future_rows_in_features=0`. Positive means a higher TP mean. Largest absolute effects:

| Feature | TP mean | SL mean | SMD |
|---|---:|---:|---:|
| atr_pct_ratio96 | 0.9570 | 1.0088 | -0.195 |
| pre_range48 | 0.0504 | 0.0563 | -0.165 |
| atr_pct | 0.0067 | 0.0074 | -0.162 |
| full_spread | 0.0234 | 0.0262 | -0.129 |
| spread_mean8 | 0.0099 | 0.0110 | -0.119 |
| fast_slow_gap | 0.0158 | 0.0177 | -0.118 |
| ma_spread_pct | 0.0099 | 0.0111 | -0.118 |
| spread_pos96 | 0.3625 | 0.3950 | -0.108 |
| drawdown24 | 0.0171 | 0.0193 | -0.105 |
| spread_mean24 | 0.0101 | 0.0111 | -0.104 |

These are weak univariate differences (largest absolute SMD 0.195), not evidence for a feature filter or a causal claim. Dataset matching verified zero outcome mismatches and a maximum net-maker difference of `9.97e-17` against the canonical re-resolution. The feature dataset has 3,740 rows total; 3,647 selected rows matched because this report additionally enforces each month’s full-horizon crop and includes 369 timeouts, which the dataset does not label as TP/SL.
