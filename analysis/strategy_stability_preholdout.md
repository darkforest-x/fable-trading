# Pre-holdout strategy stability audit

> **Evidence class: historical candidate evidence only.**
> All folds use `signal_time < 2026-05-04` (minus barrier purge).
> This is **not** final profitability proof, **not** a holdout evaluation,
> and **not** a parameter search. No threshold/TP/SL/cost was retuned.

## Scope

- Predeclared candidates: frozen TP5/SL2 long SWAP, H1 scaled, H8 30m h48, H10 short.
- Walk-forward expanding train; test folds are chronological.
- Score gate: train-only score q90 (fixed rule from `src.backtest.run`).
- Cost: SWAP maker round-trip **0.06%** (fixed; not searched).
- Portfolio sim: maker-filled only, max 10 concurrent, one position per symbol.

## Reproduction

```bash
python3 -m scripts.strategy_stability_preholdout --n-folds 4 --write-report
PYTHONPATH=. python3 -m pytest tests/test_strategy_stability_preholdout.py -q
```

## Results by candidate

### tp5_sl2_long_swap (frozen_champion)

- Artifact: `data/swap_replication/swap_tp5_sl2.csv`
- Notes: Frozen ACTIVE long TP5/SL2 on expanded SWAP universe.
- Pre-holdout n=7547 (2025-06-05 05:15:00+00:00 → 2026-05-03 01:15:00+00:00)
- OK test folds: 3/3

| fold | test window | n_test | top-decile n | top gross | top net@maker | top win | top fill | top fund cov | port trades | PF | net/trade | win | maxDD | port fund cov |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2025-09-07→2025-12-13 | 1887 | 188 | -0.00078 | -0.00138 | 0.298 | 0.904 | 0.000 | 104 | 0.581 | -0.00242 | 0.2404 | 0.0295 | 0.000 |
| 2 | 2025-12-13→2026-03-04 | 1887 | 188 | 0.00183 | 0.00123 | 0.404 | 0.862 | 0.000 | 124 | 1.193 | 0.00077 | 0.3629 | 0.0111 | 0.000 |
| 3 | 2026-03-04→2026-05-03 | 1887 | 188 | 0.00206 | 0.00146 | 0.425 | 0.851 | 0.633 | 142 | 1.105 | 0.00043 | 0.3803 | 0.0103 | 0.585 |

- Aggregate top-decile net@maker mean/min/max: 0.00044/-0.00138/0.00146
- Share of folds with positive top-decile net@maker: 0.6667
- Portfolio PF mean / total trades: 0.96 / 370

### h1_scaled_25_t3 (challenger)

- Artifact: `data/sweep_exits_swap/scaled_25_t3.csv`
- Notes: H1 scaled take-profit (half @2.5xATR + trail 3xATR); discovery-tier only.
- Pre-holdout n=7547 (2025-06-05 05:15:00+00:00 → 2026-05-03 01:15:00+00:00)
- OK test folds: 3/3

| fold | test window | n_test | top-decile n | top gross | top net@maker | top win | top fill | top fund cov | port trades | PF | net/trade | win | maxDD | port fund cov |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2025-09-07→2025-12-13 | 1887 | 188 | 0.00021 | -0.00039 | 0.489 | 0.872 | 0.000 | 81 | 0.784 | -0.00088 | 0.4691 | 0.0102 | 0.000 |
| 2 | 2025-12-13→2026-03-04 | 1887 | 188 | -0.00069 | -0.00129 | 0.468 | 0.862 | 0.000 | 131 | 0.505 | -0.00294 | 0.3893 | 0.0396 | 0.000 |
| 3 | 2026-03-04→2026-05-03 | 1887 | 188 | 0.00270 | 0.00210 | 0.686 | 0.894 | 0.622 | 174 | 1.883 | 0.00198 | 0.6724 | 0.0032 | 0.598 |

- Aggregate top-decile net@maker mean/min/max: 0.00014/-0.00129/0.0021
- Share of folds with positive top-decile net@maker: 0.3333
- Portfolio PF mean / total trades: 1.057 / 386

### h8_30m_h48 (challenger)

- Artifact: `data/mtf_sweep/h8_30m_h48.csv`
- Notes: H8 30m TP5/SL2 pool (horizon 48 bars); discovery-tier only.
- Pre-holdout n=1818 (2025-06-07 16:00:00+00:00 → 2026-05-02 21:30:00+00:00)
- OK test folds: 3/3

| fold | test window | n_test | top-decile n | top gross | top net@maker | top win | top fill | top fund cov | port trades | PF | net/trade | win | maxDD | port fund cov |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2025-09-06→2025-12-25 | 455 | 45 | 0.00658 | 0.00598 | 0.489 | 0.867 | 0.000 | 25 | 2.549 | 0.00754 | 0.56 | 0.0042 | 0.000 |
| 2 | 2025-12-25→2026-03-20 | 454 | 45 | 0.00183 | 0.00123 | 0.400 | 0.911 | 0.000 | 18 | 1.605 | 0.00334 | 0.4444 | 0.0038 | 0.000 |
| 3 | 2026-03-20→2026-05-02 | 455 | 45 | 0.00288 | 0.00228 | 0.467 | 0.889 | 0.867 | 32 | 1.461 | 0.00192 | 0.4375 | 0.0051 | 0.844 |

- Aggregate top-decile net@maker mean/min/max: 0.00316/0.00123/0.00598
- Share of folds with positive top-decile net@maker: 1.0
- Portfolio PF mean / total trades: 1.872 / 75

### h10_short_tp5_sl2 (challenger)

- Artifact: `data/short_replication/swap_short_tp5_sl2.csv`
- Notes: H10 short-side mirror TP5/SL2; discovery-tier only.
- Pre-holdout n=7222 (2025-06-04 22:00:00+00:00 → 2026-05-03 05:00:00+00:00)
- OK test folds: 3/3

| fold | test window | n_test | top-decile n | top gross | top net@maker | top win | top fill | top fund cov | port trades | PF | net/trade | win | maxDD | port fund cov |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2025-09-12→2025-12-13 | 1806 | 180 | 0.00537 | 0.00477 | 0.456 | 0.878 | 0.000 | 101 | 1.703 | 0.00379 | 0.4257 | 0.0118 | 0.000 |
| 2 | 2025-12-13→2026-03-05 | 1805 | 180 | 0.00526 | 0.00466 | 0.478 | 0.878 | 0.000 | 162 | 2.15 | 0.00536 | 0.4938 | 0.0101 | 0.000 |
| 3 | 2026-03-05→2026-05-03 | 1806 | 180 | 0.00155 | 0.00095 | 0.406 | 0.817 | 0.356 | 141 | 1.038 | 0.00021 | 0.3759 | 0.0241 | 0.369 |

- Aggregate top-decile net@maker mean/min/max: 0.00346/0.00095/0.00477
- Share of folds with positive top-decile net@maker: 1.0
- Portfolio PF mean / total trades: 1.63 / 404

## Interpretation

- Positive fold-level top-decile net after fixed maker cost is **candidate evidence** that ranking still separates outcomes out-of-sample within the pre-holdout era.
- Fold-to-fold variance (min vs max) is the stability signal; a single strong fold is not enough to claim robustness.
- Portfolio PF uses concurrent-slot constraints and maker-fill filtering; trade counts will be lower than raw top-decile counts.
- Real-funding coverage is reported where OKX funding history exists; uncovered trades must not be treated as zero funding.

## Risk and honesty

- **Not a live profit guarantee.** Future return is unproven.
- **Holdout (≥2026-05-04) was not read** for scoring or summary.
- Consumed trading-validation windows were not re-tuned.
- Prebuilt candidate CSVs may themselves contain post-holdout rows on disk; this audit filters them out before training and aborts if any fold timestamp leaks.
- H1/H8/H10 remain challengers; ACTIVE frozen TP5/SL2 is unchanged by this report.
- Short-side funding helper currently uses long funding cost convention; short funding coverage is informational and may need a dedicated short funding path for production.

## Next options (owner-gated if changing live state)

1. Keep collecting prospective forward/shadow trades (Todo 4) without promoting challengers.
2. Owner decision only: any ACTIVE/threshold/cost change.
3. E2.1b / SAHI path remains independent of this judgment-layer stability audit.
