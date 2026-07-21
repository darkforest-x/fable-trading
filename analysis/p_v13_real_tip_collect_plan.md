# v13 — 收集 live 真实 tip 成败图（计划，未执行）

> **状态（2026-07-21 晚）**：tip-only 诊断已结（见 `analysis/p_tip_only_smoke.md`）—
> tip_fire≈0，**不**永久 tip-only。进入小样采集：`scripts/collect_v13_tip_previews.py`
> → `analysis/output/v13_real_tip_preview/`（带框预览，非训练集）。

## 为何改方向

此前「冲 ~1000 张 tip 几何图 + 自动标」的优先级已下调。Owner 采纳的第一优先是：
**tip-only 扫描诊断**（搞清 live 为何仍几乎无 tip 开火），不是先堆自标训练集。

自标（v12 predict + 贴边门）仍可作辅线样张，**不能替代**实盘成败分布。

## 目标数据（v13）

收集 **live 真实 tip 窗** 的成败对照集，供后续 tip 模型 / 门槛校准：

| 类 | 定义（草案） | 用途 |
|---|---|---|
| tip-hit | tip 窗上 v12 贴边框命中，且事后标签/前向可判为有效密集启动 | 正例几何 |
| tip-miss-dense | 肉眼/规则看 tip 确有密集，但 detector 无贴边框 | 漏检 |
| tip-noise | 贴边框有，但 spread/事后判为噪声 | 误检 |
| tip-empty-ok | tip 无密集、无框 — 背景对照 | 校准背景率 |

窗几何固定：200 bar、右缘=盘口、无后文（与实盘一致）。

## 采集来源（诊断后再定）

1. **VPS forward / tip 路径落盘**（首选）：诊断确认 tip-only 扫描行为正确后，脉冲内或旁路轻量存 tip PNG + predict 元数据（conf、right_norm、bar_in_win）。
2. **本机回放**：用同一 tip 窗渲染 + `owner_best` tip-edge 过滤，对齐生产门（`TIP_EDGE_BARS=2`）。
3. **不采用**：pad200 金标 remap；纯启发式 `auto_label` 当主 GT。

## 门禁（与生产同值）

- 框保留条件：`bar_in_win >= window - TIP_EDGE_BARS`（默认 N=2）
- conf 默认与 live 一致（0.30）；分析时可另存 raw 低 conf，不混入主集
- 新鲜度三门 / 脉冲预算：采集旁路不得拖垮 15min 节拍（见实盘纪律 7–8）

## 明确不做（本阶段）

- [ ] 大规模自标冲 1000 张训练集
- [ ] 自动 promote / 开训 v13
- [ ] 改 `forward_pulse.sh` / `yolo_candidates`（让位 tip-only 诊断）
- [ ] 动 holdout

## 依赖顺序

```
tip-only 诊断结论
    → 确认 live tip 窗是否被扫描 / 贴边门是否过严 / 是否零框
    → 再定：落盘字段、采样率、目标张数
    → 小样（几十张）Owner 目视
    → 若需要再扩到数百～一千
```

## 已有辅线资产（非 v13 主集）

- 脚本（未大规模跑）：`scripts/build_live_tip_auto_dataset.py`（v12 + tip-edge 自标）
- 空标 tip 几何包（人手标用，非自动 GT）：`datasets/label_live_tip_1000/`
- 旧 tip-only 几何预览（非 v12 贴边自标）：`analysis/output/v13_tiponly_preview/`

## 下一步（Owner / 诊断任务）

1. 等 tip-only 诊断报告（召回、贴边拒绝数、是否扫到 tip 窗）。
2. 按诊断结论改本计划的「采集来源 / 张数」后，再开轻量落盘。
3. 小样目视通过后，才谈训练分布或门槛微调。
