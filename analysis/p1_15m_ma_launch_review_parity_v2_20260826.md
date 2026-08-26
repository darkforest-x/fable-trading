# 15m t-3 因果训练图 / 完成走势审核图一致性修复 v2

## 结论先行

- Owner 指出的两处问题均已重做：首批 1,000 张审核图现在 **1,000/1,000** 把蓝线画在
  `t-3`，并用橙色虚线保留原选择 `t`；与原本已经正确的新增 9,000 张合并后，完整池为
  **10,000/10,000 精确 t-3**。
- 不再把 48 根完成走势审核图与 14--22 根模型输入冒充为同一视觉尺度。新版 40 页画廊的左栏
  直接引用 canonical 训练 PNG，浏览器只根据同名 YOLO `.txt` 叠框；右栏单独显示
  `t-30..t+17` 的未来辅助审核图。左栏与模型输入逐字节相同，右栏永不进入训练。
- 9,938 个时间切分后正例全部接回原训练图片和标签：LONG 4,965、SHORT 4,973；62 个 purge
  候选明确显示“没有训练图”，没有为了凑满 10,000 张而伪造输入或标签。
- 本轮没有改变 36,812 张既有训练图片/标签，没有重训、读取 holdout、改 ACTIVE/frozen、
  promote、部署、forward 或订单状态。它修复的是审核与谱系证据，不把旧弱标签模型升级为 Gold。

![t-3 模型输入 / 未来审核双视图分层抽样](../experiments/active/exp-15m-ma-launch-t3-review-parity-v2/results/comparison_overview.png)

完整入口：
`experiments/active/exp-15m-ma-launch-t3-review-parity-v2/results/index.html`。

## 两个问题怎样修

### 1. 短窗与 48 根图的横纵轴不再混为一谈

旧展示把两个不同问题放在一张比较里：

- 候选审核图：固定 48 根，`t-30..t+17`，包含完成后的大幅走势；
- 模型输入：14--22 根，最晚到 `t..t+2`，每张按自己的可见价格范围渲染。

两者都铺满 1,280 像素时，短窗单根 K 线自然比 48 根审核图宽 2.18--3.43 倍；未来大幅高低点
又会改变审核图纵轴。若强行让训练图继承未来审核图的尺度，就必须读取未来价格来决定模型像素，
形成泄漏；若把 48 根全塞进训练，又违背 Owner 要求的小检测窗。

v2 因此采用物理分栏：左栏就是模型真正吃到的原始 PNG，既不重渲染也不重编码；标签框是 HTML
CSS overlay，来自同名 `.txt`。右栏保留完整完成走势，但明确标记为 review-only。两个视图可以属于
同一 `event_id`，不能再被解释成同一像素坐标系。

### 2. 首批 1,000 张补齐 t-3

旧首批 1,000 的蓝线仍在选择 `t`；新增 9,000 才是蓝线 `t-3`、橙线 `t`。v2 从原 OHLCV
前缀和相同六均线 renderer 重新画首批 1,000，不覆盖历史目录：

| 项目 | 旧视图 | v2 |
|---|---:|---:|
| 首批 1,000 显示 t-3 | 0 / 1,000 | **1,000 / 1,000** |
| 新增 9,000 显示 t-3 | 9,000 / 9,000 | **9,000 / 9,000**（原图哈希复用） |
| 联合池显示 t-3 | 9,000 / 10,000 | **10,000 / 10,000** |
| 训练正例核心右端 t-3 | 9,938 / 9,938 | 9,938 / 9,938（未改） |

每张 v2 行同时保存 `review_marker_source_i = source_anchor_i - 3` 和
`review_marker_time = anchor_time - 45min`。蓝线仍是审核边界，不把显示线烙进模型 PNG。

## 数据与产物统计

| 项目 | 结果 |
|---|---:|
| 候选 | 10,000；LONG 5,000 / SHORT 5,000 |
| 候选时间 | 2022-01-05 至 2026-05-03，全部 pre-holdout |
| 接回训练正例 | 9,938；train 8,468 / val 1,470 |
| 正例方向 | LONG 4,965 / SHORT 4,973 |
| purge，无训练图 | 62 |
| 首批重新渲染 | 1,000 PNG / 151,624,980 bytes |
| 后续原审核图复用 | 9,000 PNG |
| HTML | 40 页 / 10,000 卡片 |
| HTML 图片引用 | 19,938：10,000 review + 9,938 causal |
| HTML 标签框 | 9,938 |
| 缺失图片引用 / 非法框样式 | 0 / 0 |
| v2 结果目录 | 183 MiB；不复制原 1.49 GiB 训练集 |

关键身份：

| 产物 | SHA-256 |
|---|---|
| preregistration | `f3926d8ba00f0b89f6ee2e3f437a4de4387cb8485c4359ef88c669de6aa68d3c` |
| parity manifest | `f61cc05dd905fa563acffac49cb1e98973ff82dba8c00e2b32f8b00dd199106d` |
| build receipt | `97afac92b4ae48241997677f56b49d0308064098111cea587e99cd1526c8140d` |
| static HTML QA | `8314c124d9c1fc6936ad472993017b6457fb59753ed4da2bc707d116a06c2fc6` |
| comparison overview | `34682728f188dda828a74424b5bad489e4d8e3f3ffa26a04c20a6e5a274d471f` |

## 验收与零假设

这是非方向性的渲染/谱系修复，不定义入场、退出、成本、模型新分数或收益。因此 val AUC、收益
置换 p、top-decile 毛/净收益、胜率、单特征收益基线和匹配随机入场对照均不适用；不能编造。
对应的严格零假设是“仅改变审核表示，不改变模型输入、标签或候选身份”：

| 检查 | 结果 |
|---|---:|
| candidate event_id 集合变化 | 0 |
| canonical 训练 PNG 重编码 | 0；9,938 行直接接回原 SHA |
| canonical YOLO 标签变化 | 0；9,938 行接回原 SHA |
| future review 进入模型输入 / 标签 | 0 / 0 |
| 10,000 行 t-3 index / timestamp 算术 | 10,000 / 10,000 通过 |
| HTML 卡片 / 图片 / 框 | 10,000 / 19,938 / 9,938 |
| HTML 缺失图片引用 | 0 |
| holdout OHLCV 物化 | 0 |
| 新模型 / 训练轮数 | 0 / 0 |
| 定向测试 | 80 passed |

Codex in-app Browser 的安全策略拒绝自动导航到本地 `file://` 页面；没有绕过或换浏览器规避。
替代验收为：40 页 HTML 的 10,000 卡片、19,938 图片路径和 9,938 CSS 框全量静态解析，
再用本地图片查看器对分层 overview 做视觉检查。这个限制已写进 QA receipt，未冒充浏览器通过。

## 解读

本轮修正了 Owner 看到的真实矛盾：过去“找到的图”和“训练图”不是坏像素，而是被错误地当成
同一个视图比较。v2 后，左图回答“模型到底看了什么和框在哪里”，右图回答“为什么这个候选事后
被选中”；二者通过 event_id 联结，但未来和坐标系物理隔离。

首批 1,000 的三根错位也已从展示层消失。NMR rank 1 现在和新增 9,000 的 SAND rank 1 一样，
均显示蓝线 `t-3` 与橙色原 `t`，不再需要用户在脑中手动平移三根。

## 风险与诚实声明

1. **没有改善旧模型。** 旧权重仍学习同一批自动弱标签；v2 只让其输入和标签可被准确看见。
2. **没有把全图缩放成一样。** 这是有意的：短窗和未来图本来就不应共享由未来决定的纵轴。
   修复方式是分栏并锁定左栏为真实模型像素，而不是制造一个视觉相似但泄漏的训练图。
3. **t-3 仍是统一派生边界。** 10,000 行显示一致不等于逐样本 Gold 几何正确；当前
   `training_eligible=false / production_eligible=false` 不变。
4. **62 个 purge 不补图。** 它们没有进入 canonical 训练 split，v2 如实缺少左栏，不用另一时段
   或伪造图片补齐。
5. **浏览器 QA 有明确边界。** `file://` 自动打开被产品安全策略阻止；本报告只声称静态引用与
   overview 视觉检查通过。

## 下一步

Owner 可以直接从 v2 画廊审“左侧模型输入+框”是否表达目标形态。若大量框语义仍不对，下一步
应先逐样本修核心边界并重建 Gold 数据集；只有数据门通过后才讨论是否重新上 3060 训练。本轮不把
一次审核显示修复扩张成新的训练授权。

## 完整复现命令

构建器先于产物提交：`a552a7762bebfd201c34061e3fecc30611da520e`；静态 verifier：
`2363fc3fc6773a4eea4d91edc03fc7cf13b7e7fa`。

```bash
cd /Users/zhangzc/fable-trading
git branch --show-current

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ma_launch_review_parity.py \
  tests/test_fifteen_minute_launch_candidates.py \
  tests/boundaries/test_layer_imports.py

# --out 必须指向仓内一个不存在的新目录；canonical results 拒绝覆盖。
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_review_parity_v2.py \
  --out experiments/active/exp-15m-ma-launch-t3-review-parity-v2/repro_results

PYTHONPATH=. .venv/bin/python scripts/verify_15m_ma_launch_review_parity_v2.py \
  --results experiments/active/exp-15m-ma-launch-t3-review-parity-v2/repro_results \
  --out experiments/active/exp-15m-ma-launch-t3-review-parity-v2/repro_results/html_qa_receipt.json

.venv/bin/python scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_review_parity_v2_20260826.md \
  --out-dir analysis/html
```
