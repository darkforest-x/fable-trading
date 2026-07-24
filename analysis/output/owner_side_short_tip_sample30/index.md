# owner_side_short_tip — 30 张 tip 对齐样本（GT 绿框）

- 来源：`datasets/dense_owner_side_short_tip/`（tip 重裁窗 + 重写框；时间切分 VAL_CUT=2026-02-01）
- 抽样：固定 seed=42，train 20 + val 10
- 画法：绿色 = YOLO GT；青黄竖线 = 框右缘；图最右红竖线 = 盘口 tip
- **未开训**；等 Owner 看图确认后再决定是否 train。

| # | split | stem | symbol | box右缘 | cut_time | 图 |
|---|-------|------|--------|---------|----------|----|
| train-01 | train | `ARKM_USDT_SWAP_005330__b0` | ARKM_USDT_SWAP | 0.997 | 2025-07-28 15:15:00+00:00 | [打开](images/train_01_ARKM_USDT_SWAP_005330__b0.png) |
| train-02 | train | `ATH_USDT_SWAP_014330__b0` | ATH_USDT_SWAP | 0.997 | 2025-10-28 19:45:00+00:00 | [打开](images/train_02_ATH_USDT_SWAP_014330__b0.png) |
| train-03 | train | `AUCTION_USDT_SWAP_001730__b0` | AUCTION_USDT_SWAP | 0.997 | 2025-06-20 16:30:00+00:00 | [打开](images/train_03_AUCTION_USDT_SWAP_001730__b0.png) |
| train-04 | train | `AUCTION_USDT_SWAP_012530__b0` | AUCTION_USDT_SWAP | 0.997 | 2025-10-10 16:30:00+00:00 | [打开](images/train_04_AUCTION_USDT_SWAP_012530__b0.png) |
| train-05 | train | `ENA_USDT_SWAP_005530__b0` | ENA_USDT_SWAP | 0.997 | 2025-11-13 16:00:00+00:00 | [打开](images/train_05_ENA_USDT_SWAP_005530__b0.png) |
| train-06 | train | `ETC_USDT_SWAP_013530__b0` | ETC_USDT_SWAP | 0.997 | 2025-10-21 03:15:00+00:00 | [打开](images/train_06_ETC_USDT_SWAP_013530__b0.png) |
| train-07 | train | `FIL_USDT_SWAP_010130__b0` | FIL_USDT_SWAP | 0.997 | 2025-09-14 11:45:00+00:00 | [打开](images/train_07_FIL_USDT_SWAP_010130__b0.png) |
| train-08 | train | `GAS_USDT_SWAP_014330__b0` | GAS_USDT_SWAP | 0.997 | 2025-10-30 04:45:00+00:00 | [打开](images/train_08_GAS_USDT_SWAP_014330__b0.png) |
| train-09 | train | `IOTA_USDT_SWAP_005730__b0` | IOTA_USDT_SWAP | 0.997 | 2025-07-31 18:15:00+00:00 | [打开](images/train_09_IOTA_USDT_SWAP_005730__b0.png) |
| train-10 | train | `NOT_USDT_SWAP_014330__b0` | NOT_USDT_SWAP | 0.997 | 2025-10-30 05:00:00+00:00 | [打开](images/train_10_NOT_USDT_SWAP_014330__b0.png) |
| train-11 | train | `PEPE_USDT_SWAP_014730__b0` | PEPE_USDT_SWAP | 0.997 | 2025-11-02 14:30:00+00:00 | [打开](images/train_11_PEPE_USDT_SWAP_014730__b0.png) |
| train-12 | train | `PLUME_USDT_SWAP_007330__b0` | PLUME_USDT_SWAP | 0.997 | 2025-08-18 02:45:00+00:00 | [打开](images/train_12_PLUME_USDT_SWAP_007330__b0.png) |
| train-13 | train | `PNUT_USDT_SWAP_007930__b0` | PNUT_USDT_SWAP | 0.997 | 2025-08-24 06:30:00+00:00 | [打开](images/train_13_PNUT_USDT_SWAP_007930__b0.png) |
| train-14 | train | `RESOLV_USDT_SWAP_004930__b0` | RESOLV_USDT_SWAP | 0.997 | 2025-07-30 05:30:00+00:00 | [打开](images/train_14_RESOLV_USDT_SWAP_004930__b0.png) |
| train-15 | train | `SHELL_USDT_SWAP_000530__b0` | SHELL_USDT_SWAP | 0.997 | 2025-06-09 01:45:00+00:00 | [打开](images/train_15_SHELL_USDT_SWAP_000530__b0.png) |
| train-16 | train | `TIA_USDT_SWAP_005530__b1` | TIA_USDT_SWAP | 0.997 | 2025-07-28 15:15:00+00:00 | [打开](images/train_16_TIA_USDT_SWAP_005530__b1.png) |
| train-17 | train | `okx_MAGIC_USDT_SWAP_011960__b0` | MAGIC_USDT_SWAP | 0.997 | 2025-10-07 14:15:00+00:00 | [打开](images/train_17_okx_MAGIC_USDT_SWAP_011960__b0.png) |
| train-18 | train | `okx_MASK_USDT_SWAP_014460__b0` | MASK_USDT_SWAP | 0.997 | 2025-11-03 01:45:00+00:00 | [打开](images/train_18_okx_MASK_USDT_SWAP_014460__b0.png) |
| train-19 | train | `okx_PI_USDT_SWAP_017660__b0` | PI_USDT_SWAP | 0.997 | 2025-12-04 20:45:00+00:00 | [打开](images/train_19_okx_PI_USDT_SWAP_017660__b0.png) |
| train-20 | train | `okx_ZIL_USDT_SWAP_021860__b0` | ZIL_USDT_SWAP | 0.997 | 2026-01-19 00:00:00+00:00 | [打开](images/train_20_okx_ZIL_USDT_SWAP_021860__b0.png) |
| val-01 | val | `BRETT_USDT_SWAP_028494__b0` | BRETT_USDT_SWAP | 0.997 | 2026-03-26 05:30:00+00:00 | [打开](images/val_01_BRETT_USDT_SWAP_028494__b0.png) |
| val-02 | val | `HOME_USDT_SWAP_026151__b0` | HOME_USDT_SWAP | 0.997 | 2026-03-11 19:45:00+00:00 | [打开](images/val_02_HOME_USDT_SWAP_026151__b0.png) |
| val-03 | val | `LINEA_USDT_SWAP_022456__b0` | LINEA_USDT_SWAP | 0.997 | 2026-04-22 23:00:00+00:00 | [打开](images/val_03_LINEA_USDT_SWAP_022456__b0.png) |
| val-04 | val | `YGG_USDT_SWAP_025480__b0` | YGG_USDT_SWAP | 0.997 | 2026-02-23 15:00:00+00:00 | [打开](images/val_04_YGG_USDT_SWAP_025480__b0.png) |
| val-05 | val | `okx_CHZ_USDT_SWAP_025060__b0` | CHZ_USDT_SWAP | 0.997 | 2026-02-20 11:30:00+00:00 | [打开](images/val_05_okx_CHZ_USDT_SWAP_025060__b0.png) |
| val-06 | val | `okx_JELLYJELLY_USDT_SWAP_029060__b0` | JELLYJELLY_USDT_SWAP | 0.997 | 2026-04-03 10:15:00+00:00 | [打开](images/val_06_okx_JELLYJELLY_USDT_SWAP_029060__b0.png) |
| val-07 | val | `okx_LA_USDT_SWAP_026860__b0` | LA_USDT_SWAP | 0.997 | 2026-03-21 13:45:00+00:00 | [打开](images/val_07_okx_LA_USDT_SWAP_026860__b0.png) |
| val-08 | val | `okx_MOVE_USDT_SWAP_027560__b0` | MOVE_USDT_SWAP | 0.997 | 2026-03-18 12:15:00+00:00 | [打开](images/val_08_okx_MOVE_USDT_SWAP_027560__b0.png) |
| val-09 | val | `okx_TRUMP_USDT_SWAP_026460__b0` | TRUMP_USDT_SWAP | 0.997 | 2026-03-05 12:30:00+00:00 | [打开](images/val_09_okx_TRUMP_USDT_SWAP_026460__b0.png) |
| val-10 | val | `okx_WCT_USDT_SWAP_027960__b0` | WCT_USDT_SWAP | 0.997 | 2026-03-22 19:45:00+00:00 | [打开](images/val_10_okx_WCT_USDT_SWAP_027960__b0.png) |

## 绝对路径

`/Users/zhangzc/fable-trading/analysis/output/owner_side_short_tip_sample30`

