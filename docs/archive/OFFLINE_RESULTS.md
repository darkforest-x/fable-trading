# 离线管道结果（自动生成，2026-07-09 15:07）

> 由 `scripts/offline_pipeline.sh` 无人值守产出。原始产物由后台脚本写在
> `/Users/zhangzc/fable-trading/OFFLINE_RESULTS.md`；本文件为 Codex worktree
> 验收同步副本。运行日志：`logs/offline_run.log`。

## 合约复制性检验（val only，未碰 holdout）

| 配置 | 候选 | val AUC | p | top毛利 | 净@taker0.10% | 净@maker0.06% | maker成交率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| tp4_sl2 | 8828 | 0.572 | 0.001 | 0.185% | 0.085% | 0.125% | 88% |
| tp5_sl2 | 8828 | 0.560 | 0.001 | 0.285% | 0.185% | 0.225% | 87% |

结论：TP5/SL2 合约复制性成立，主线宇宙保持 SWAP。

## YOLO 全量训练官方评估

```json
{
  "mAP50": 0.8569,
  "mAP50-95": 0.6643,
  "precision": 0.8003,
  "recall": 0.7112
}
```

结论：mAP50 低于正式验收线 0.90；按 `NEXT_STEPS.md`，不继续调 conf/IoU/增强凑数，
YOLO 正式验收未达成，标记为非关键路径并暂停。
