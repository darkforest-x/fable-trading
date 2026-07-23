# 扩 OHLCV 因子救不出分边可部署因果规则

- **问题**：owner 批评窄特征（spread/排列/动量等）是人为天花板，要求对已标
  long/short 框尽量因子化后再裁决——扩特征能否把因果规则 PF@maker 抬过 1.3？
- **死胡同**：①以为「特征不够」是瓶颈，堆到 116 列（均线族/密集/动量/波动/量/
  结构/时间）后 short 仅 1.127→1.227、long 0.917→0.938，仍不过线；②把已标样本
  walk-forward 分数门的高 PF（5–7）或 oracle（5–7）当成扩特征的胜利——那是确认态
  选点，不是可部署规则；③继续在 OHLCV 上加列期待质变——top gain 仍是 order_score
  + spread_chg，新组（量/时/结构）几乎零贡献，画像未改。
- **有效路径**：在同一协议下扩特征 → LGBM 披露 → AND 因果规则 → 全市场 train
  扫描；只看规则 PF。结论写死：**特征再多也可能复制不了事后选点**；分边+扩因子
  未救出可部署边。下一步应换问题（tip 金标/检测器），不是继续堆因子。
- **通用规则**：owner 要求「尽量因子化」时，用**一次扩特征实验**回应天花板质疑，
  但裁决标准不变（因果 PF≥1.3）。扩完仍不过 → 诚实结案，禁止用 oracle/样本内
  分数门续命。OHLCV 可算量耗尽仍失败 = 增量不在本地因子空间。
- **牵连**：`scripts/owner_side_rich_features.py`、
  `scripts/owner_side_rich_features_verdict.py`、
  `analysis/p_owner_side_rich_features_verdict.md`、
  [分边未解锁可部署规则](owner-side-split-does-not-unlock-deployable-rule.md)。
