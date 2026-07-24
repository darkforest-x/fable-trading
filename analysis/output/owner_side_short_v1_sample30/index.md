# owner_side_short_v1 — 30 张可视化样本（GT 绿框）

- 来源：`datasets/dense_owner_side_short/`（只读抽图，未改训练/未 rebuild）
- 抽样：固定 seed=42，train 20 + val 10
- 画法：绿色 = YOLO GT 框；青黄竖线 = 框右缘；图最右红竖线 = 盘口 tip 边
- **看图能确认的**：框是否像你想标的空头密集启动
- **看图看不出的**：train/val 是否按时间切开（技术风险仍在）

| # | split | stem | symbol | owner_side | n_boxes | box右缘(归一化) | cut_time | 图 |
|---|-------|------|--------|------------|---------|-----------------|----------|----|
| train-01 | train | `ANIME_USDT_SWAP_008330` | ANIME_USDT_SWAP | short | 1 | 0.475 | 2025-08-29 07:00:00+00:00 | [打开](images/train_01_ANIME_USDT_SWAP_008330.png) |
| train-02 | train | `APT_USDT_SWAP_004930` | APT_USDT_SWAP | short | 1 | 0.798 | 2025-07-23 13:30:00+00:00 | [打开](images/train_02_APT_USDT_SWAP_004930.png) |
| train-03 | train | `APT_USDT_SWAP_011130` | APT_USDT_SWAP | short | 1 | 0.172 | 2025-09-24 19:45:00+00:00 | [打开](images/train_03_APT_USDT_SWAP_011130.png) |
| train-04 | train | `BLUR_USDT_SWAP_004130` | BLUR_USDT_SWAP | short | 1 | 0.036 | 2025-07-14 14:30:00+00:00 | [打开](images/train_04_BLUR_USDT_SWAP_004130.png) |
| train-05 | train | `BLUR_USDT_SWAP_014730` | BLUR_USDT_SWAP | short | 1 | 0.53 | 2025-11-03 01:45:00+00:00 | [打开](images/train_05_BLUR_USDT_SWAP_014730.png) |
| train-06 | train | `BONK_USDT_SWAP_001330` | BONK_USDT_SWAP | short | 1 | 0.78 | 2025-06-17 00:15:00+00:00 | [打开](images/train_06_BONK_USDT_SWAP_001330.png) |
| train-07 | train | `CELO_USDT_SWAP_008330` | CELO_USDT_SWAP | short | 1 | 0.949 | 2025-08-29 07:00:00+00:00 | [打开](images/train_07_CELO_USDT_SWAP_008330.png) |
| train-08 | train | `EGLD_USDT_SWAP_014730` | EGLD_USDT_SWAP | short | 1 | 0.527 | 2025-11-03 02:15:00+00:00 | [打开](images/train_08_EGLD_USDT_SWAP_014730.png) |
| train-09 | train | `H_USDT_SWAP_025648` | H_USDT_SWAP | short | 1 | 0.435 | 2026-03-26 05:45:00+00:00 | [打开](images/train_09_H_USDT_SWAP_025648.png) |
| train-10 | train | `JELLYJELLY_USDT_SWAP_000530` | JELLYJELLY_USDT_SWAP | short | 1 | 0.574 | 2025-06-08 07:30:00+00:00 | [打开](images/train_10_JELLYJELLY_USDT_SWAP_000530.png) |
| train-11 | train | `LA_USDT_SWAP_011530` | LA_USDT_SWAP | short | 1 | 0.585 | 2025-10-10 16:00:00+00:00 | [打开](images/train_11_LA_USDT_SWAP_011530.png) |
| train-12 | train | `RSR_USDT_SWAP_004930` | RSR_USDT_SWAP | short | 1 | 0.21 | 2025-07-23 10:15:00+00:00 | [打开](images/train_12_RSR_USDT_SWAP_004930.png) |
| train-13 | train | `XPT_USDT_SWAP_006130` | XPT_USDT_SWAP | short | 1 | 0.413 | 2026-04-05 21:45:00+00:00 | [打开](images/train_13_XPT_USDT_SWAP_006130.png) |
| train-14 | train | `okx_ANIME_USDT_SWAP_002660` | ANIME_USDT_SWAP | short | 1 | 0.942 | 2025-07-04 07:00:00+00:00 | [打开](images/train_14_okx_ANIME_USDT_SWAP_002660.png) |
| train-15 | train | `okx_BOME_USDT_SWAP_017160` | BOME_USDT_SWAP | short | 1 | 0.782 | 2025-11-30 23:45:00+00:00 | [打开](images/train_15_okx_BOME_USDT_SWAP_017160.png) |
| train-16 | train | `okx_ENSO_USDT_SWAP_014760` | ENSO_USDT_SWAP | short | 1 | 0.349 | 2026-04-01 21:15:00+00:00 | [打开](images/train_16_okx_ENSO_USDT_SWAP_014760.png) |
| train-17 | train | `okx_IOTA_USDT_SWAP_008560` | IOTA_USDT_SWAP | short | 2 | 0.073, 0.075 | 2025-08-31 23:15:00+00:00; 2025-08-31 23:15:00+00:00 | [打开](images/train_17_okx_IOTA_USDT_SWAP_008560.png) |
| train-18 | train | `okx_JUP_USDT_SWAP_011260` | JUP_USDT_SWAP | short | 1 | 0.514 | 2025-09-30 00:30:00+00:00 | [打开](images/train_18_okx_JUP_USDT_SWAP_011260.png) |
| train-19 | train | `okx_KAITO_USDT_SWAP_010460` | KAITO_USDT_SWAP | short | 2 | 0.37, 0.675 | 2025-09-21 09:15:00+00:00; 2025-09-22 00:45:00+00:00 | [打开](images/train_19_okx_KAITO_USDT_SWAP_010460.png) |
| train-20 | train | `okx_SHIB_USDT_SWAP_031260` | SHIB_USDT_SWAP | short | 1 | 0.654 | 2026-04-25 15:30:00+00:00 | [打开](images/train_20_okx_SHIB_USDT_SWAP_031260.png) |
| val-01 | val | `AGLD_USDT_SWAP_027698` | AGLD_USDT_SWAP | short | 1 | 0.756 | 2026-03-18 14:00:00+00:00 | [打开](images/val_01_AGLD_USDT_SWAP_027698.png) |
| val-02 | val | `MANA_USDT_SWAP_029987` | MANA_USDT_SWAP | short | 1 | 0.514 | 2026-04-12 01:45:00+00:00 | [打开](images/val_02_MANA_USDT_SWAP_029987.png) |
| val-03 | val | `MEW_USDT_SWAP_023086` | MEW_USDT_SWAP | short | 1 | 0.491 | 2026-01-29 02:45:00+00:00 | [打开](images/val_03_MEW_USDT_SWAP_023086.png) |
| val-04 | val | `MEW_USDT_SWAP_024086` | MEW_USDT_SWAP | short | 1 | 0.564 | 2026-02-08 16:30:00+00:00 | [打开](images/val_04_MEW_USDT_SWAP_024086.png) |
| val-05 | val | `MOODENG_USDT_SWAP_022086` | MOODENG_USDT_SWAP | short | 1 | 0.265 | 2026-01-18 05:15:00+00:00 | [打开](images/val_05_MOODENG_USDT_SWAP_022086.png) |
| val-06 | val | `okx_ARB_USDT_SWAP_010560` | ARB_USDT_SWAP | short | 1 | 0.399 | 2025-09-21 10:30:00+00:00 | [打开](images/val_06_okx_ARB_USDT_SWAP_010560.png) |
| val-07 | val | `okx_MAGIC_USDT_SWAP_011560` | MAGIC_USDT_SWAP | short | 1 | 0.928 | 2025-10-04 00:45:00+00:00 | [打开](images/val_07_okx_MAGIC_USDT_SWAP_011560.png) |
| val-08 | val | `okx_ORDI_USDT_SWAP_020260` | ORDI_USDT_SWAP | short | 1 | 0.479 | 2025-12-31 16:15:00+00:00 | [打开](images/val_08_okx_ORDI_USDT_SWAP_020260.png) |
| val-09 | val | `okx_SAND_USDT_SWAP_024760` | SAND_USDT_SWAP | short | 1 | 0.814 | 2026-02-17 06:30:00+00:00 | [打开](images/val_09_okx_SAND_USDT_SWAP_024760.png) |
| val-10 | val | `okx_XTZ_USDT_SWAP_008460` | XTZ_USDT_SWAP | short | 1 | 0.252 | 2025-08-31 09:15:00+00:00 | [打开](images/val_10_okx_XTZ_USDT_SWAP_008460.png) |

## 绝对路径

`/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30`

```
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_01_ANIME_USDT_SWAP_008330.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_02_APT_USDT_SWAP_004930.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_03_APT_USDT_SWAP_011130.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_04_BLUR_USDT_SWAP_004130.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_05_BLUR_USDT_SWAP_014730.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_06_BONK_USDT_SWAP_001330.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_07_CELO_USDT_SWAP_008330.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_08_EGLD_USDT_SWAP_014730.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_09_H_USDT_SWAP_025648.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_10_JELLYJELLY_USDT_SWAP_000530.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_11_LA_USDT_SWAP_011530.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_12_RSR_USDT_SWAP_004930.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_13_XPT_USDT_SWAP_006130.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_14_okx_ANIME_USDT_SWAP_002660.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_15_okx_BOME_USDT_SWAP_017160.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_16_okx_ENSO_USDT_SWAP_014760.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_17_okx_IOTA_USDT_SWAP_008560.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_18_okx_JUP_USDT_SWAP_011260.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_19_okx_KAITO_USDT_SWAP_010460.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/train_20_okx_SHIB_USDT_SWAP_031260.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_01_AGLD_USDT_SWAP_027698.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_02_MANA_USDT_SWAP_029987.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_03_MEW_USDT_SWAP_023086.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_04_MEW_USDT_SWAP_024086.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_05_MOODENG_USDT_SWAP_022086.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_06_okx_ARB_USDT_SWAP_010560.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_07_okx_MAGIC_USDT_SWAP_011560.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_08_okx_ORDI_USDT_SWAP_020260.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_09_okx_SAND_USDT_SWAP_024760.png
/Users/zhangzc/fable-trading/analysis/output/owner_side_short_v1_sample30/images/val_10_okx_XTZ_USDT_SWAP_008460.png
```

## 简易 HTML 画廊

同目录 `index.html`（双击或浏览器打开）。

