# 检测主线切 v12（owner 强制）— 2026-07-20

**Owner 决策**：对话明确「主线直接换 v12」。

## 范围（单变量）

| 改 | 不改 |
|---|---|
| `models/owner_best.pt` ← v12 H-TIP 权重 | 判断冻结 `frozen_tp5_sl2_swap_yolo_v11_reg_20260718` |
| live YOLO 候选源读 owner_best | 阈值 val-q90 / TP5/SL2 / 成本 |
| | **未**重建 `judgment_yolo_swap_v12` |
| | **未**消耗 holdout / accept 回测 |
| | 主线 `forward_log.csv` 未清空（时钟继续） |

## 指标依据

| 项 | v11 | v12 |
|---|---:|---:|
| tip_hit_rate | 0.009 | **0.925** |
| frozen-F1 | 0.658 | **0.650** |

自动 `promote_owner_best.py` 会因 F1 略低留下 v11；本切流为 **owner 覆盖**（为 tip 实时路径）。

## 操作

```bash
cp models/owner_best.pt models/owner_best_pre_v12.pt   # 回滚点
cp models/owner_v12_htip.pt models/owner_best.pt
# owner_best.json 写 promote_mode=owner_forced_mainline_cutover
# scp → VPS /opt/fable-trading/models/owner_best.pt
```

## 回滚

```bash
cp models/owner_best_pre_v12.pt models/owner_best.pt
# 同步 VPS；下一脉冲即恢复 v11 检测
```

## 风险与诚实声明

1. **无 v12 历史组合回测**；经济性仍靠前向 100 笔新鲜裁决。  
2. 判断层仍在 v11 候选分布上训练的分数空间；v12 候选分布若漂移，分数校准可能变差——影子/前向需盯 score 分布。  
3. frozen-F1 略降 0.008：尺子不塌，但不是「全面更强」，是 **tip 专项** 换主线。  
4. 未做 holdout 第 6 次；若日后要 v12 池 + 重冻 + accept，另批。

## 判定

**检测主线 = v12。** 判断主线仍 = v11 freeze。  
