# 2m / 3m 高频影子数据可行性

日期：2026-07-12

## 结论

OKX 当前真实接口可直接返回 `2m` 和 `3m` K 线。仓库数据层已支持这两个周期，BTC/ETH
SWAP 的 120 天样本已完成抓取并通过完整性检查。它们只能进入独立影子研究，不能直接套用
15m LightGBM、阈值或前向账本。

## 复现

```bash
curl 'https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=2m&limit=5'
curl 'https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=3m&limit=5'
python3 -m src.data.fetch_okx --symbols BTC_USDT_SWAP ETH_USDT_SWAP --days 120 --workers 2 --bar 2m
python3 -m src.data.fetch_okx --symbols BTC_USDT_SWAP ETH_USDT_SWAP --days 120 --workers 2 --bar 3m
python3 -m pytest tests/test_bar_generalization.py tests/test_ops_jobs_phase2.py tests/test_ops_phase3_hubs.py -q
```

## 数据验收

| Bar | Symbol | Rows | Range UTC | Pre-holdout rows | Duplicate | Gap | Bad OHLC |
|---|---|---:|---|---:|---:|---:|---:|
| 2m | BTC-USDT-SWAP | 86,522 | 2026-03-13 13:26 → 2026-07-11 17:28 | 37,037 | 0 | 0 | 0 |
| 2m | ETH-USDT-SWAP | 86,522 | 2026-03-13 13:26 → 2026-07-11 17:28 | 37,037 | 0 | 0 | 0 |
| 3m | BTC-USDT-SWAP | 57,705 | 2026-03-13 12:15 → 2026-07-11 17:27 | 24,715 | 0 | 0 | 0 |
| 3m | ETH-USDT-SWAP | 57,705 | 2026-03-13 12:15 → 2026-07-11 17:27 | 24,715 | 0 | 0 | 0 |

Expected-interval share is 100% for all four files; zero-volume rate is 0%. Raw data is excluded from git.
Incremental updater QA then added 46 new 2m rows and 12 new 3m rows without creating duplicate files.

## Experiment boundary

1. Evaluate 2m and 3m separately; timeframe is the only experiment variable.
2. Keep literal SMA/EMA 20/60/120 bars as requested by the owner, while clearly reporting that their wall-clock spans differ from 15m.
3. Train and freeze a separate LightGBM artifact for each timeframe. Never score these candidates with the 15m freeze.
4. Pre-register one 18-hour outcome horizon for comparability: 2m `h540`, then 3m `h360`; do not sweep horizons on the same validation window.
5. Keep the fixed 0.20% round-trip cost and add higher-cost sensitivity because spread/slippage matter more at 2m/3m.
6. Holdout remains sealed. Stored post-cutoff rows are not scored or used for selection; training and validation must stop before 2026-05-04 with the normal purge.

## Risk and honesty

- Two symbols prove transport and data quality, not strategy generalization.
- More bars do not guarantee more independent opportunities; autocorrelation and the 18-bar dedupe can create apparent sample inflation.
- At high frequency, 0.20% costs consume a larger share of gross movement. A higher signal count can still reduce net profit.
- No model, threshold, candidate rule, ACTIVE file, forward ledger or VPS executor changed in this task.

## Next atom

Expand a liquid SWAP research pool, build a pre-holdout-only 2m dataset, and run the frozen single-horizon
2m experiment. Only after its report is closed should the same process begin for 3m.
