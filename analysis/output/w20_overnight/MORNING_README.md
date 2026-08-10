# w20 midbox 验收结果

- 权重: `/Users/zhangzc/fable-trading/analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt`
- 训练: **60 ep** · best **ep50** · peak mAP50 **0.2812** · last 0.2355
- val: 405 正 + 405 负
- **最佳 conf=0.15**
  - F1 **0.403** · P 0.3546 · R 0.4667
  - 纯负误火率 **0.1259**（门 ≤0.2）
- 轻量 12-bar: hit_mean=0.00454 · ctrl=0.00031 · **lift=0.00423** · n_hit=237
- 决策: **PASS** — f1=0.403_neg_fp=0.126
- 未 promote ACTIVE / 未读 holdout

## conf 扫描
- conf=0.15: F1=0.403 P=0.3546 R=0.4667 neg_fp=0.1259
- conf=0.25: F1=0.2632 P=0.5512 R=0.1728 neg_fp=0.0346
- conf=0.35: F1=0.1306 P=0.7436 R=0.0716 neg_fp=0.0074
- conf=0.45: F1=0.0337 P=0.7 R=0.0173 neg_fp=0.0
