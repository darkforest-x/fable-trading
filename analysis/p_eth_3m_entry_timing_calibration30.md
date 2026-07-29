# ETH 3m 提前入场线 30 张校准包

日期：2026-07-29
状态：预览完成；未导入 Label Studio、未训练、未消耗 holdout

## 目的

从项目 53 中 owner 已判“形态是”的 93 张里，生成 30 个更早的入场判断点。该包只校准 timing 口径，不重新判断形态，也不把机械提议当成金标。

## 构造口径

- 只使用 93 张 owner shape-positive。
- 相邻任务间隔不超过 60 分钟视为同一事件，每个事件最多保留一张。
- 机械提议线：原 v10 框内第一次“完成收盘后位于六条 MA 下方”的 3m bar。
- 人工审核图：橙线左侧 200 根因果 K 线；右侧固定 60 根 / 3 小时未来。
- 模型图：严格截止橙线，没有未来像素。
- 30 张按未来感知诊断层平衡抽取：15 张“剩余不少于已走”、15 张“已走多于剩余”。该分层只保证校准难度多样，不进入界面或模型特征。

## 数据统计

| 指标 | 结果 |
|---|---:|
| 来源 shape-positive | 93 |
| 校准任务 | 30 |
| 独立事件 | 30 |
| 两个诊断层 | 15 / 15 |
| 相对原 v10 提前最小 | 6 min |
| 相对原 v10 提前中位 | 30 min |
| 相对原 v10 提前最大 | 42 min |
| 候选时间范围 | 2026-03-15 至 2026-05-01 |
| 最晚 future_end | 2026-05-01 23:18 UTC |
| holdout（>=2026-05-04） | 未读取、未消耗 |

## 完整性检查

- 30/30 source task、event id 唯一。
- 30/30 候选线严格早于原 v10 开火。
- 30/30 future_end = 候选线 + 180 分钟。
- 30 张 review JPEG 与 30 张 causal PNG 均存在且尺寸一致。
- 移动 HTML 内嵌 30 张图片，不包含按钮或 JavaScript。
- 抽查第 1、10、20、30 张：中文、橙线、原 v10 对照线和灰框均正常显示。

## 风险与诚实声明

- 第一次跌到六 MA 下方只是候选规则，不是已经验证的最优入场点。
- 样本先经过 v10 找框、再经过 owner 判“形态是”，不能代表所有 ETH 3m 做空启动。
- 30 张是口径校准包，不用于冻结阈值或宣称可学习性通过。
- 只有 owner 对橙线再做“是/不是”复核后，才可把通过样本转成 timing 训练标签。

## 复现命令

```bash
MPLCONFIGDIR=/tmp/fable-entry-calibration-mpl \
  .venv/bin/python scripts/build_eth3m_entry_timing_calibration30.py

PYTHONPATH=. MPLCONFIGDIR=/tmp/fable-entry-calibration-mpl \
  .venv/bin/pytest -q \
  tests/test_build_eth3m_entry_timing_calibration30.py \
  tests/test_analyze_eth3m_v10_yes_no_labels.py
```
