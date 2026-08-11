# P1 Owner ETH 短延迟动态窗口 30 张校准报告（2026-08-11）

## 结论先行

已按最新语义合同重渲染 30 张短延迟校准图：核心后文 3/4/5 根各 10 张，前文 6–10 根、
旧核心 proposal 5/7 根均衡覆盖，实际总窗 W14–22。红框中心自然落在 52.94%–70.59%，
没有固定在最右侧或正中，说明动态最短上下文可以消除旧固定位置偏差。

但这轮**没有形成训练集**。目视检查发现部分旧核心 proposal 仍把明显启动大K包进红框，
证明“重新裁剪”只能修复输入位置分布，不能自动修复旧标签语义。全部 30 张保持
`semantic_status=unreviewed`、`geometry_status=unreviewed_legacy_core_proposal`、
`training_eligible=false`。

## 冻结合同

- 事件身份和 train/val 边界：修复后的 Stage-A manifest。
- 核心候选：旧 Owner W20–30 manifest 的 5/7 根框，仅作 proposal。
- 动态窗口：`pre 6–10 + core 5/7 + post 3/4/5`，总长由样本自然决定。
- 后文：3 根优先、5 根硬封顶；不再使用 6–10 根。
- 选择禁止项：后续收益、模型置信度、手写形态分数、Stage-A val 图/标签、holdout。
- 生产资格：false；不得进入 forward、ACTIVE、部署或交易。

## 数据统计

| 指标 | 本轮结果 |
|---|---:|
| Stage-A manifest | 2,378 |
| 可用 train proposal 母池 | 2,020 |
| 校准样本 | 30 |
| post=3 / 4 / 5 | 10 / 10 / 10 |
| core=5 / 7 | 15 / 15 |
| pre=6 / 7 / 8 / 9 / 10 | 各 6 |
| 唯一 event / symbol | 30 / 30 |
| 实际 W 范围 | 14–22 |
| 红框中心范围 | 52.94%–70.59% |
| Stage-A val 图/标签读取 | 0 / 0 |
| holdout 行物化 | 0 |

val manifest 只读取时间边界元数据：最早 val 完整窗口开始于 2026-03-20 02:45 UTC，按 150 bars
purge 得到 train 动态窗口最晚允许结束于 2026-03-18 13:15 UTC。30 张全部早于该边界；原始 CSV
按所需最终 bar 的 `nrows` 前缀读取，不物化后续或 holdout 行。

## 与上一底座同表对照

| 维度 | 昨晚 Stage A | 本轮动态校准 |
|---|---|---|
| 用途 | 宽位置离线表征底座 | 最新语义的几何/上下文校准 |
| 输入窗口 | W20–30 | W14–22（首轮试探） |
| 框宽 | 4/5 根机械框 | 5/7 根旧 Owner proposal |
| 核心后真实 K | 1–22 根 | 3/4/5 根 |
| 位置策略 | 20%–85% 四桶随机 | 由 pre/core/post 自然产生 |
| 本轮观察位置 | 四桶目标分布 | 52.94%–70.59% |
| 语义/边界金标 | 否 | 否，仍待 Owner 裁决 |
| 训练资格 | 已完成 Stage-A 训练 | false |

## 结果解读

1. **几何问题已被隔离**：当 post 从 3 增至 5，框中心会自然由偏右向中间移动；无需固定一个
   坐标，也无需右侧补白。
2. **短延迟与位置有关但不是位置模板**：post=3 的框更靠右是时间合同的自然结果，不等于固定
   右侧 shortcut；同组内仍因 pre/core 变化而移动。
3. **旧框语义仍不可靠**：若旧 proposal 已包含启动大K，重渲染后红框仍会包含它。继续调整 crop
   无法解决，下一门必须是类别与边界的人工语义裁决。

## 非适用指标

本轮未训练、未推理、未做方向性回测，因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、
单特征基线和匹配随机对照组均不适用。任何从这 30 张图外推模型精度或交易收益的说法都不成立。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_owner_eth_shortdelay_calibration.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_build_owner_eth_shortdelay_calibration.py
```

输出：

- `analysis/output/owner_eth_shortdelay_calibration30_v1/summary.json`
- `analysis/output/owner_eth_shortdelay_calibration30_v1/manifest.jsonl`
- `analysis/output/owner_eth_shortdelay_calibration30_v1/calibration_post3_10.png`
- `analysis/output/owner_eth_shortdelay_calibration30_v1/calibration_post4_10.png`
- `analysis/output/owner_eth_shortdelay_calibration30_v1/calibration_post5_10.png`

## 风险与诚实声明

- 30 张来自旧 Owner proposal，尚未被 Owner 按最新 ETH 两条边界语义重新裁决。
- 三张大图能验证位置和上下文合同，不能验证母池正例率，也不能代替难负例设计。
- 本轮使用 30 个不同币种以暴露形态差异；正式训练集仍需按时间块构建 train/val，并在 Owner
  确认后的正例周围补入语义相近难负例。
- 未读取 holdout，未改阈值、成本、障碍、新鲜度、ACTIVE，未 promote、未部署、未下单。

## 下一步

Owner 先在三张大图上确认红框方向是否终于正确。确认后不再继续调 crop 坐标，而是把这 30 张
作为边界校准尺：逐张裁决“形态和框都准 / 形态像但框要改 / 不是目标”。只有通过的样本及其修正
边界才能进入正式短窗数据集；随后补同时间块难负例，再训练 delay 3/4/5 单变量对照。
