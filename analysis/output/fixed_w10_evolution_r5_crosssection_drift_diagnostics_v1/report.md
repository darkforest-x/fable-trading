# R5 frozen cross-section drift diagnostics

**Verdict:** March degradation is broad across all three sampled symbols, but the negative March frozen-gate aggregate is a **single-symbol concentration**: PIEVERSE contributes more than the full net loss, while SHIB and SUI remain positive after gating. This is diagnostic evidence only—no promotion, retraining, feature selection, threshold search, or orders.

## Scope and integrity

- INNER only: `2026-01-01T00:00:00+00:00` through `2026-03-31T07:30:00+00:00` (exclusive); symbols: PIEVERSE_USDT_SWAP, SHIB_USDT_SWAP, SUI_USDT_SWAP.
- Frozen L1: `threshold=0.25,tp=5,sl=2,horizon=72`; frozen L2 threshold: `0.4`; reconstructed events: `1419`.
- Gate lineage SHA256: `5de01132ce9b3bdf80da6c17f8742c4e68ee043bf37baed2a2292ec5980f2e76`; candidate SHA256: `f73bb5d208e91e6de2aaa8b4b77a9d23a8f53a2acc7bc1dd22a827f2176d5ea6`; summary SHA256: `3dec0be3e0e751b2ad24067cc4c9ec6eb0f869b81cdb09a8c84bf033ef1b6748`; snapshot manifest SHA256: `033e394a614a82a6fedb5c20327bd469ceaa2784dd4394f253cbefc8006b49d5`.
- Strict loader recursively validated gate/parent artifacts, source inputs, candidate/summary/weights, manifest, and the three selected snapshot files.
- Safety: `holdout_rows_read=0`, `april_rows_read=0`, `optimization_sealed_read=false`, `future_features=0`, `orders=false`, `auto_promote=false`.

## Overall result

| Slice | L1 trades | L1 density | L1 maker mean bp | L1 taker mean bp | L1 maker PF | L1 maker MDD bp | Gate trades | Gate density | Gate maker mean bp | Gate taker mean bp | Gate maker PF | Gate maker MDD bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Jan–Mar | 1419 | 5.296 | 4.82 | 0.82 | 1.046 | 13004.4 | 275 | 1.026 | 15.21 | 11.21 | 1.194 | 3599.1 |

Outcome counts are stored for every slice in `report.json`; net maker/taker values include the frozen cost contract. Density is trades per exposed symbol-day.

## By symbol

| Symbol | L1 trades | L1 density | L1 maker mean bp | L1 taker mean bp | L1 maker PF | L1 maker MDD bp | Gate trades | Gate density | Gate maker mean bp | Gate taker mean bp | Gate maker PF | Gate maker MDD bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PIEVERSE_USDT_SWAP | 473 | 5.296 | -17.67 | -21.67 | 0.886 | 9604.7 | 79 | 0.885 | -43.52 | -47.52 | 0.673 | 4039.7 |
| SHIB_USDT_SWAP | 473 | 5.296 | 6.42 | 2.42 | 1.085 | 3641.1 | 107 | 1.198 | 36.39 | 32.39 | 1.659 | 1524.2 |
| SUI_USDT_SWAP | 473 | 5.296 | 25.70 | 21.70 | 1.315 | 3827.0 | 89 | 0.997 | 41.89 | 37.89 | 1.722 | 971.7 |

## By half-month

| Period | L1 trades | L1 density | L1 maker mean bp | L1 taker mean bp | L1 maker PF | L1 maker MDD bp | Gate trades | Gate density | Gate maker mean bp | Gate taker mean bp | Gate maker PF | Gate maker MDD bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-01-01_to_2026-01-16 | 240 | 5.333 | -22.18 | -26.18 | 0.848 | 12155.4 | 42 | 0.933 | 56.57 | 52.57 | 1.678 | 1502.2 |
| 2026-01-16_to_2026-02-01 | 258 | 5.375 | 42.72 | 38.72 | 1.513 | 3666.4 | 56 | 1.167 | 35.98 | 31.98 | 1.574 | 788.1 |
| 2026-02-01_to_2026-02-16 | 240 | 5.333 | 26.50 | 22.50 | 1.229 | 8137.5 | 38 | 0.844 | -11.51 | -15.51 | 0.881 | 1598.3 |
| 2026-02-16_to_2026-03-01 | 207 | 5.308 | 17.97 | 13.97 | 1.198 | 2449.8 | 45 | 1.154 | 47.59 | 43.59 | 1.727 | 1219.7 |
| 2026-03-01_to_2026-03-16 | 240 | 5.333 | -32.64 | -36.64 | 0.701 | 10015.6 | 47 | 1.044 | -28.15 | -32.15 | 0.713 | 2284.3 |
| 2026-03-16_to_2026-03-31T0730 | 234 | 5.094 | -4.75 | -8.75 | 0.941 | 2721.8 | 47 | 1.023 | -12.52 | -16.52 | 0.825 | 1455.3 |

## Symbol × month

| Symbol / month | L1 trades | L1 density | L1 maker mean bp | L1 taker mean bp | L1 maker PF | L1 maker MDD bp | Gate trades | Gate density | Gate maker mean bp | Gate taker mean bp | Gate maker PF | Gate maker MDD bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PIEVERSE_USDT_SWAP / 2026-01 | 166 | 5.355 | 0.87 | -3.13 | 1.004 | 8743.8 | 20 | 0.645 | -89.54 | -93.54 | 0.512 | 2253.5 |
| PIEVERSE_USDT_SWAP / 2026-02 | 149 | 5.321 | -6.58 | -10.58 | 0.952 | 5042.2 | 28 | 1.000 | 32.89 | 28.89 | 1.346 | 1195.5 |
| PIEVERSE_USDT_SWAP / 2026-03 | 158 | 5.212 | -47.61 | -51.61 | 0.636 | 7523.0 | 31 | 1.023 | -82.85 | -86.85 | 0.386 | 3169.7 |
| SHIB_USDT_SWAP / 2026-01 | 166 | 5.355 | 11.42 | 7.42 | 1.165 | 1951.4 | 44 | 1.419 | 88.66 | 84.66 | 3.271 | 264.5 |
| SHIB_USDT_SWAP / 2026-02 | 149 | 5.321 | 17.14 | 13.14 | 1.205 | 2263.2 | 30 | 1.071 | -11.00 | -15.00 | 0.852 | 1524.2 |
| SHIB_USDT_SWAP / 2026-03 | 158 | 5.212 | -8.95 | -12.95 | 0.881 | 3641.1 | 33 | 1.089 | 9.78 | 5.78 | 1.165 | 953.6 |
| SUI_USDT_SWAP / 2026-01 | 166 | 5.355 | 22.04 | 18.04 | 1.295 | 2908.7 | 34 | 1.097 | 67.08 | 63.08 | 2.404 | 260.0 |
| SUI_USDT_SWAP / 2026-02 | 149 | 5.321 | 57.08 | 53.08 | 1.621 | 2237.6 | 25 | 0.893 | 44.52 | 40.52 | 1.647 | 514.0 |
| SUI_USDT_SWAP / 2026-03 | 158 | 5.212 | -0.04 | -4.04 | 1.000 | 3827.0 | 30 | 0.990 | 11.14 | 7.14 | 1.184 | 971.7 |

## Score/outcome calibration

The model was supervised on TP vs SL with timeouts excluded, so calibration uses `TP / (TP + SL)` as the empirical target.

| Score bin | Rows | Mean score | TP | SL | Timeout | Resolved TP rate | Empirical − score | Maker mean bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| [0.0,0.1) | 198 | 0.065 | 44 | 115 | 39 | 0.277 | 0.212 | 45.80 |
| [0.1,0.2) | 408 | 0.152 | 99 | 276 | 33 | 0.264 | 0.112 | -17.14 |
| [0.2,0.3) | 311 | 0.248 | 77 | 209 | 25 | 0.269 | 0.021 | -2.10 |
| [0.3,0.4) | 227 | 0.348 | 67 | 143 | 17 | 0.319 | -0.029 | 5.40 |
| [0.4,0.5) | 152 | 0.449 | 42 | 100 | 10 | 0.296 | -0.153 | 0.97 |
| [0.5,0.6) | 86 | 0.543 | 30 | 48 | 8 | 0.385 | -0.159 | 33.57 |
| [0.6,0.7) | 32 | 0.639 | 11 | 20 | 1 | 0.355 | -0.284 | 2.16 |
| [0.7,0.8) | 5 | 0.736 | 3 | 2 | 0 | 0.600 | -0.136 | 216.00 |
| [0.8,0.9) | 0 | n/a | 0 | 0 | 0 | n/a | n/a | n/a |
| [0.9,1.0] | 0 | n/a | 0 | 0 | 0 | n/a | n/a | n/a |

Resolved Brier score: `0.2207`; log loss: `0.6532`. The bins are not monotonic—especially the lowest-score bin—so the score is not well calibrated cross-sectionally even though the frozen threshold improves the aggregate mean return.

## Causal feature drift: Jan–Feb vs March

SMD is `(March mean − Jan/Feb mean) / sqrt((var_JanFeb + var_March)/2)`. All 28 features were reconstructed after physically slicing each symbol at its decision bar; no future feature rows were used.

| Feature | Jan–Feb mean | March mean | SMD | Magnitude | Same direction in all symbols |
|---|---:|---:|---:|---|---|
| pre_range168 | 0.128229 | 0.091925 | -0.518 | moderate | yes |
| atr_pct | 0.008480 | 0.006526 | -0.466 | small | yes |
| pre_range48 | 0.065052 | 0.049067 | -0.412 | small | yes |
| full_spread | 0.031596 | 0.020849 | -0.405 | small | yes |
| fast_slow_gap | 0.021394 | 0.013934 | -0.379 | small | yes |
| spread_mean24 | 0.013103 | 0.009779 | -0.306 | small | yes |
| ma_spread_pct | 0.013108 | 0.009641 | -0.286 | small | yes |
| drawdown24 | 0.022472 | 0.016333 | -0.266 | small | yes |
| spread_mean8 | 0.012908 | 0.009803 | -0.261 | small | yes |
| close_vs_ema200 | 0.007229 | 0.000250 | -0.180 | negligible | yes |
| slow_slope_12 | 0.000585 | -0.000054 | -0.141 | negligible | yes |
| volume_ratio | 1.187149 | 1.080217 | -0.089 | negligible | yes |
| close_vs_ema55 | 0.001834 | 0.000053 | -0.086 | negligible | yes |
| volume_z | 0.150412 | 0.049545 | -0.082 | negligible | yes |
| order_score | 2.291005 | 2.158228 | -0.081 | negligible | yes |
| dense_frac48 | 0.137213 | 0.153393 | 0.079 | negligible | yes |
| atr_pct_ratio96 | 1.020926 | 1.003313 | -0.059 | negligible | yes |
| ext_up | -0.005488 | -0.004686 | 0.054 | negligible | no |
| ret_48 | 0.001271 | -0.000741 | -0.049 | negligible | no |
| spread_pos96 | 0.381118 | 0.366567 | -0.047 | negligible | yes |
| full_ratio_min48 | 2.527640 | 2.635847 | 0.034 | negligible | yes |
| spread_chg8 | 0.000061 | -0.000141 | -0.029 | negligible | yes |
| vol_ratio_mean8 | 1.052786 | 1.039022 | -0.025 | negligible | no |
| dense_run_len | 1.451852 | 1.339662 | -0.022 | negligible | no |
| spread_chg24 | 0.000005 | -0.000266 | -0.020 | negligible | no |
| ret_24 | -0.000046 | -0.000551 | -0.017 | negligible | no |
| ret_4 | 0.000296 | 0.000281 | -0.001 | negligible | no |
| ret_12 | 0.000325 | 0.000318 | -0.000 | negligible | no |

## Evidence classification

- Broad temporal degradation: all three symbols have lower March gate maker means than their Jan–Feb means: PIEVERSE_USDT_SWAP -64.73 bp; SHIB_USDT_SWAP -38.48 bp; SUI_USDT_SWAP -46.38 bp.
- Single-symbol loss concentration: March gate total maker is -1911.46 bp; PIEVERSE contributes -2568.35 bp (134.4% of net loss because the other symbols offset it). Ex-PIEVERSE March remains 10.43 maker bp/trade and 6.43 taker bp/trade.
- Covariate drift: 1 feature(s) have |SMD| ≥ 0.5 and 9 have |SMD| ≥ 0.2. The largest shifts are `pre_range168` -0.518, `atr_pct` -0.466, `pre_range48` -0.412, `full_spread` -0.405, `fast_slow_gap` -0.379.

## Exactly one next experimental variable

Increase **independent confirmation symbol sample size from 3 to 12**, keeping the seed rule, frozen gate/model, L1 config, L2 threshold, Jan–Mar interval, costs, and snapshot protocol unchanged. This directly tests whether PIEVERSE-like behavior is population-level without excluding a losing symbol, selecting features, or retraining. No promote and no order action is recommended.
