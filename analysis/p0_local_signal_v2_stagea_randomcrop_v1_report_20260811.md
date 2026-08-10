# Local Signal V2 Stage A 真实裁剪 P0 报告（2026-08-11）

## 直接结论

Owner 已明确授权恢复交接文档中的 Stage A 离线预训练。新版
`local_signal_v2_stagea_randomcrop_v1` 已通过视觉门与十道 P0 数据门，可以启动 Stage A
离线预训练。

这次位置变化来自原始连续 K 线的真实裁剪起点，而不是在图右边加白：24 张独立预览覆盖
左中、中间、中右、右侧四档，每档 6 张；全量 2,378 个正例的 anchor X 覆盖 20%–85%，
且每个框右侧仍有 1–22 根真实 K 线。数据始终标记 `stage_a_only` 与
`production_eligible=false`，不得直接进入 tip-smoke、forward、ACTIVE 或部署。

## 授权边界与实验角色

- 允许：在完整窗口早于 holdout 的历史数据上，用 decision 后的真实 K 线改变裁剪位置，
  只学习局部形态表征。
- 禁止：把 Stage A 自家 val/mAP 当生产裁决，或直接 promote、部署、实盘扫描。
- 后续：Stage A 权重只能作为严格因果 Stage B 的初始化；最终检测器仍须通过真 tip 金标与
  tip-smoke。
- 本轮没有读取 holdout，没有修改阈值、障碍、成本、新鲜度、ACTIVE，也没有下单。

## 数据合同

| 项目 | 冻结值 |
|---|---:|
| 窗口长度 | 20–30 根真实连续 15m K |
| confirmation delay | 1 或 2 bars |
| 框规则 | `anchor-2..decision` |
| 每个 event | 1 个 crop |
| 位置桶目标 | 20% / 35% / 30% / 15% |
| 正负比例 | 1:1 easy negatives |
| split | 按时间最后 15% 为 val |
| train/val purge | 150 bars |
| holdout 起点 | 2026-05-04 00:00 UTC |
| dataset seed | 20260807 |
| 生产资格 | `false` |

## 数据统计

| split | 正例 | 负例 | 总数 | 正类率 | 完整样本时间范围 |
|---|---:|---:|---:|---:|---|
| train | 2,020 | 2,020 | 4,040 | 50.00% | 2025-06-02 23:15 — 2026-03-18 13:15 UTC |
| val | 358 | 358 | 716 | 50.00% | 2026-03-20 02:45 — 2026-05-03 14:30 UTC |
| 合计 | 2,378 | 2,378 | 4,756 | 50.00% | 全部早于 holdout |

源 manifest 中 246 个位于 holdout 或无合格时间的正事件在读取对应行情前被拒绝；另有 11 个
事件落入 150-bar purge 区被丢弃。构建记录明确为 `holdout_read=false`。

## 位置分布与视觉门

| 真实 K 线位置桶 | 目标占比 | 实际数量 | 实际占比 | 绝对偏差 |
|---|---:|---:|---:|---:|
| left_mid（20%–35%） | 20% | 479 | 20.14% | 0.14pp |
| mid（35%–55%） | 35% | 851 | 35.79% | 0.79pp |
| mid_right（55%–75%） | 30% | 697 | 29.31% | 0.69pp |
| right（75%–85%） | 15% | 351 | 14.76% | 0.24pp |

最大占比偏差 0.79pp，低于预注册 5pp 容差。`future_bars` 在这里专指 decision 后仍画在
Stage A 图片中的真实 K 线，分位数为 1 / 6 / 10 / 14 / 22（min/P25/P50/P75/max）。这正是
Owner 授权的 Stage A 表征预训练例外，不得与严格因果 Stage B 混用。

代表性预览：

- left_mid：`analysis/output/local_signal_v2_stagea_preview/ETH_USDT_SWAP_001930_stagea.png`
- mid：`analysis/output/local_signal_v2_stagea_preview/okx_CELO_USDT_SWAP_008860_stagea.png`
- mid_right：`analysis/output/local_signal_v2_stagea_preview/GLM_USDT_SWAP_029090_stagea.png`
- right：`analysis/output/local_signal_v2_stagea_preview/ONDO_USDT_SWAP_006730_stagea.png`

## P0 结果与旧方案对照

| 方案 | 真实内容位置分散 | 因果 | 时间切分 | holdout | 文件守恒 | 训练裁决 |
|---|---|---|---|---|---|---|
| 旧 B2 fixed-30 | 否，约 93%/95% | 是 | 是 | 0 | 是 | 位置 shortcut，作废 |
| blank-only v3 | 否，只移动画布坐标 | 是 | 是 | 0 | 是 | Owner 目视否决 |
| **Stage A randomcrop v1** | **是，20%–85%** | 否，授权例外 | **是** | **0** | **是** | **Stage A P0 PASS** |

十道门全部通过：所有正例均有真实 decision 后 K、框终点不晚于 decision、真实内容位置分散、
event 不跨 split、正负均严格时间切分、0 holdout、标签在界内、4,756 文件/标签/manifest 守恒、
100% 可追溯到行情 bar、所有样本均为 Stage A 且生产资格为 false。

## 可复现性

同 seed 在同一输出目录完整重渲染两次：

- positive manifest SHA-256：`ae4675a6ef4cec3b3d471dce99b000cef8736f7e72955cd0df2d1425073cde89`
- negative manifest SHA-256：`0fdced5c0c3fad428be01aa8361b44ce0f0e4f1a4aa1aa7e626ef74620f323f0`

两次均逐字节一致；manifest 内含每张图片与标签的 SHA-256，因此完整记录的产物 hash 集也一致。
`stagea_summary.json` 含 `generated_at`，按设计不参与逐字节比较。

## 模型与回测指标

本报告是训练前 P0 数据验收，尚未产生模型。因此 val AUC、置换检验 p、top-decile 毛/净收益、
胜率、单特征基线、匹配随机对照、YOLO mAP 与 event precision/recall 均为 **不适用**。这些值
不能从旧 B2 或 blank-only 方案借用。Stage A 训练完成后的自家 val/mAP也只能诊断表征，不能
替代严格因果 Stage B、连续 tip 密度回放或真 tip 验收。

## 复现命令

```bash
# 代码与合同测试
PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_local_signal_v2_stagea.py \
  tests/test_local_signal_v2_stageb.py \
  tests/test_w20_midbox_causality.py \
  tests/test_detection_train_config.py \
  tests/test_detection_train_speed_knobs.py

# 24 张独立预览（每个真实内容位置桶 6 张）
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stagea.py --preview

# 全量构建
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stagea.py \
  --out datasets/local_signal_v2_stagea_randomcrop_v1

# 独立 P0 审计
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/audit_local_signal_v2_stagea.py \
  --dataset datasets/local_signal_v2_stagea_randomcrop_v1 \
  --out analysis/output/p0_local_signal_v2_stagea_audit.json
```

## 风险与诚实声明

- Stage A 故意包含 decision 后真实 K，因此不是无前视生产数据；其唯一合法用途是离线表征初始化。
- 历史正例来自旧 pad200 标注锚点；Stage A 可以学习局部形态，但不能证明盘口 tip 识别能力。
- 1:1 easy negatives 是预训练平衡集，不代表连续市场先验，不能从样本级 FP 外推开单数。
- 真实位置分散消除了“永远贴真实内容末端”的确定性 shortcut，但不保证模型不会学习其他背景、
  币种或渲染 shortcut；训练后仍需遮挡/位置分桶诊断。
- 本轮未消耗 holdout，未 promote、未部署、未下单。

## 下一步

按已授权并预注册的唯一配方启动 Stage A 离线预训练：YOLO11s、60 epochs、patience 15、
batch 8、seed 0，flip/mosaic/mixup/HSV 全关。训练结果只归档为 Stage A 权重；随后另开严格
因果 Stage B 单变量实验，禁止自动晋升。
