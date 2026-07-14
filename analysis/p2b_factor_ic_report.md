# H19 外部 alpha 因子 IC 筛选（SWAP池, train/val, 未碰holdout）

样本 24179 候选 / 256 币种。IC=Spearman(因子, 72bar前向收益)。

| 因子 | IC | IR | 月数 | 符号稳定 | 分类 |
|---|---|---|---|---|---|
| ret_skew | -0.0418 | -0.51 | 12 | ✓ | alive |
| hl_pos | +0.0404 | +0.36 | 12 | ✓ | alive |
| boll_pos | +0.0326 | +0.17 | 12 | ✗ | reversed |
| vwap_dev | +0.0306 | +0.17 | 12 | ✗ | reversed |
| close_to_high | -0.0256 | -0.28 | 12 | ✓ | dead |
| rev5 | -0.0248 | -0.23 | 12 | ✗ | dead |
| vol_of_vol | -0.0248 | -0.16 | 12 | ✗ | dead |
| obv_slope | +0.0246 | +0.14 | 12 | ✗ | dead |
| vol_dryup | +0.0206 | +0.31 | 12 | ✗ | dead |
| updown_vol | +0.0194 | +0.28 | 12 | ✗ | dead |
| mom20 | +0.0149 | +0.09 | 12 | ✗ | dead |
| range_compress | +0.0058 | +0.10 | 12 | ✗ | dead |
| illiq | +0.0050 | +0.43 | 12 | ✓ | dead |
| vol_share | -0.0041 | -0.10 | 12 | ✗ | dead |
| vp_corr | +0.0020 | -0.09 | 12 | ✗ | dead |
| taker_imbalance | -0.0017 | -0.23 | 12 | ✗ | dead |

## 存活因子(2个, |IC|≥0.03且符号稳定) → 候选新特征
ret_skew、hl_pos

**下一步**：存活因子逐个（单变量）加进 features.py 验证 top-decile 净收益增益，有增益才留。