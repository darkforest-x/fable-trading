# Local Signal V2 — 早期启动前沿 300 张 Owner 审核包 PRE-REVIEW

生成日期：2026-08-12

协议：`local_signal_v2_early_frontier_review300_v1_20260812`

方向：仅 SHORT

状态：**审核包技术检查通过，等待 Owner YES / NO / SKIP；禁止据此训练。**

## 结论先行

本轮已从两个冻结的 R1 pre-holdout 候选池中，精确排除此前四个审核包已经展示过的
700 个唯一事件，得到 784 个从未审核的候选，再按上一轮 Canary 的 11 个 Owner YES 与
89 个 Owner NO 做**因果相似度主动检索**，生成 300 张新的盲审条目：

- 150 张内部 `yes_like` 检索候选；
- 150 张内部 `similar_no_boundary` 边界候选；
- 300 个唯一事件、154 个币；
- 300 张原始模型输入、300 张人眼 auto-Y 因果图、300 张独立未来 48 根对照图；
- 300/300 `visible_end_bar == decision_bar`；
- 检索阶段 0 根未来 K，未来图只在 300 个事件选定后生成；
- 0 默认答案、0 training-eligible、0 production-eligible、0 holdout 读取；
- 网页的 Y/N/S、左右键、自动保存、改判和刷新恢复已通过真实浏览器验证。

这批数据是**语义发现集**，不是独立的 candidate precision 验收集。它的用途是让 Owner
快速找出“启动前沿 YES”和“看起来相似但仍应 NO”的边界，不能在 Owner 审核前声称模型变好，
也不能把审核后的 YES/NO 自动转成训练标签。

## 为什么不是重新扫八个新时间块

最初为 2026-03-17 至 2026-04-28 的八个新块生成了严格 pre-holdout 快照，但 RTX 3060
当时不在局域网；Mac MPS 对完整 W12–19 约数十万窗口的扫描实测过慢，两次小规模速度检查均
主动停止，未得到可用事件。继续等待会把本轮审核包拖延数小时。

因此本轮改用仓库中已经冻结、已有完整因果与未来快照、但尚未被 Owner 看过的候选余量。
被放弃的 96MB D 块临时快照已移动到可恢复的废纸篓目录，未删除任何历史候选池或正式产物。

这项替代的代价是：300 张来自旧 train-time 块，不是新的 post-train 独立时间块。因此本报告
只称其为“主动发现集”，不称独立验证或真实连续市场 precision。

## 数据来源与排除

### 候选来源

| 来源 | 冻结候选数 | 已审核排除后剩余 |
|---|---:|---:|
| B01–B05 候选池 | 917 | 417 |
| C01–C05 候选池 | 567 | 367 |
| 合计 | 1,484 | 784 |

被排除的 700 个唯一事件来自：

- `owner_short_train_hardneg_review200_v1`：200；
- `owner_short_train_positive_retrieval100_v1`：100；
- `owner_short_train_hardneg_expansion200_v2`：200；
- `owner_short_train_hardneg_newblocks200_v3`：200。

最终 300 个 event_id 与这 700 个已审核 event_id 的交集为 0。

### 300 张时间块分布

| 时间块 | 选中数 |
|---|---:|
| B02_20250915 | 50 |
| B03_20251115 | 50 |
| B04_20260115 | 49 |
| C01_20250615 | 49 |
| C03_20251015 | 38 |
| C04_20251215 | 15 |
| C05_20260215 | 49 |
| 合计 | 300 |

B01、B05、C02 的未审余量为 0，因此没有复制样本或降低阈值凑配额；缺额确定性地分配给仍有
余量的时间块。

## 检索规则

### 参考语义

仅使用上一轮完成审核的 Canary 候选：

- Owner YES：11；
- Owner NO：89。

旧 Positive Pool 的 85 个 YES 没有进入本轮“早期前沿”相似度参考，因为边界诊断已经证明它们
整体比 Canary YES 更成熟、更偏事后。这样做的目标是避免把“已经跌开”再次定义成早期目标。

### 因果特征

相似度向量只使用每个候选 decision 及以前的：

- OHLC；
- SMA/EMA 20、60、120；
- 检测窗口与预测框几何。

所有序列按最后一个因果 close 去价格水平，并按当时可见 OHLC/均线跨度归一化，再插值到固定
时间网格。使用 5-NN 分别计算到 Owner YES 与 NO 的距离。

内部 `yes_like` 与 `similar_no_boundary` 只是抽样层，不是 AI 裁决：

- `yes_like`：优先取更接近 11 个 YES、远离 89 个 NO 的候选；
- `similar_no_boundary`：优先取仍靠近 YES、但落在 NO 一侧或边界附近的候选；
- UI 隐藏抽样层、置信度、距离、来源时间块和推荐答案；
- Owner 仍只按图选择 YES / NO / SKIP。

## 图片合同

每个 review item 有三张独立图片：

| 图片 | 数量 | 用途 | 是否含未来 |
|---|---:|---|---|
| `model_input/` | 300 | 记录冻结 R1 真正看到的 6% floor 输入 | 否 |
| `causal_review/` | 300 | Owner 左图，auto-Y，止于 decision | 否 |
| `future_review_only/` | 300 | Owner 右图，auto-Y，decision 后 48 根 | 是，仅人工审核 |

左右图均使用人眼 auto-Y，所以不再出现旧页面中真实低波幅被 6% 模型纵轴压成水平线的问题。
这没有修改模型输入；`model_input/` 仍保留原始冻结像素用于 lineage。

### 当前几何分布

- detector window：W12–W19；
- 预测核心 4–7 根：293/300；
- 预测核心 8 根：7/300；
- 框后 decision 延迟：2–6 根，中位主要集中在 3 根。

7 个 8 根框没有被自动删除，因为本轮是语义边界审核，不应把 Owner 判断提前硬编码成筛选规则；
Owner 可直接按 NO。必须诚实说明：本包审计的是当前 R1 的 W12–19 候选，不是未来计划中的
20–30 根新检测窗口，不能用本包宣布后者已实现。

## 因果与 holdout 审计

| 门 | 结果 |
|---|---:|
| 唯一 review_id | 300/300 |
| 唯一 event_id | 300/300 |
| 唯一因果审核图片 SHA | 300/300 |
| `visible_end_bar == decision_bar` | 300/300 |
| manifest `future_bars == 0` | 300/300 |
| 检索使用未来 | 0 |
| 独立未来对照图 | 300/300 |
| 每张未来对照 | 48 根 |
| 最晚未来对照时间 | 2026-02-16 00:00 UTC |
| holdout 起点 | 2026-05-04 00:00 UTC |
| holdout 物化/读取 | 0 |
| 默认 Owner 裁决 | 0 |
| training-eligible | 0 |

`causality_audit.json` 的 300 条逐项 `pass=true`，总门 `all_pass=true`。

## 网页 QA

在临时副本上使用真实 Chromium 验证：

1. 页面显示 `1 / 300`，左右图均成功加载；
2. 点击 YES 后自动前进并显示 `已保存 1 / 300`；
3. 左方向键返回上一张，N 可覆盖先前 YES；
4. S 保存下一张，append-only 日志保留 YES→NO 的改判轨迹；
5. 刷新后状态显示 `已保存 2 / 300`，S001 最新裁决恢复为 NO；
6. 正式审核目录没有写入任何测试裁决。

QA 截图：`output/playwright/local-signal-v2-early-frontier-review300-v1.png`。

## 如何开始审核

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/serve_local_signal_v2_semantic_review.py \
  --out analysis/output/local_signal_v2_early_frontier_review300_v1 \
  --port 8766
```

浏览器打开：`http://127.0.0.1:8766/`

快捷键：

- `Y`：YES；
- `N`：NO；
- `S`：SKIP；
- `← / →`：上一张 / 下一张。

裁决追加保存到：

`analysis/output/local_signal_v2_early_frontier_review300_v1/owner_verdicts.jsonl`

可以中断继续，也可以回到上一张改判。正式目录在审核前没有该裁决文件。

## 关键 SHA256

| 对象 | SHA256 |
|---|---|
| R1 权重 | `029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537` |
| review manifest | `89ca99b148a85aa4e877c6797a3899ce931d1d23b2c6b648cd39b1b77a4c9555` |
| 784 候选池 | `3f52cc5911779d7d925bf5ae7deaf1e548b9936764e0d386297a6a48f2b61d7c` |
| 300 选中记录 | `d5c64775fe8518213e96ab44787d305fc4e8935c6b454c0c717e0bd858623f53` |
| 300 因果审核图树 | `ace37d27ace3269458d9d7ba6498dc7a2b30f2c0af977dc0b23b6a61be74f219` |
| 300 模型输入图树 | `0f44f8c4e6ffc51c3ad6731566d7ca8c2f1cb493c4d7417c92ca21ea6f49bff4` |
| 300 未来图树 | `09dfc6df8e46cb54b6a96faefb390ca1b34ae54632edd275e2b5f51cb5e9c0d1` |

冻结起点 main SHA：`84501ad199034a8ffac8f9091a0015a55ccde54b`。

产物构建器 SHA：`fd9c3cad42bc3a4587674b26a07bd8320543ebc3`。

## 自动化验证

全仓测试：**701 passed、2 skipped**（14 条第三方 pyparsing/matplotlib 弃用 warning，无失败）。

以下模型/交易指标本轮不适用：val AUC、置换检验、top-decile 收益、胜率、随机入场对照、
mAP、连续市场 precision。原因是本轮没有训练或策略实验，且 300 张是主动检索发现集；为其
填写这些数值会制造伪验收结论。

## 风险与诚实声明

1. 只有 11 个早期 Canary YES，参考集很小；相似度检索可能过拟合这 11 个样本。
2. 300 张来自 R1 已触发候选，不能估计漏检，也不能代表自然市场基率。
3. 来源是旧 train-time 块的未审余量，不是新的独立时间块。
4. 未来 48 根帮助 Owner 判断语义，因此最终 YES 率必须称为“未来辅助语义裁决”，不能称纯
   causal precision。
5. 当前 6% 模型 renderer 未改变；左图 auto-Y 只改善人工可读性，不证明实盘模型已解决压缩。
6. 本包仍是 W12–19 当前模型候选，不等于 Owner 目标的 20–30 根语义窗口已经落地。
7. YES/NO 审核完成后仍需先出解盲报告，再由 Owner 单独决定是否构造训练集；不得自动训练。

## 停止条件与下一步

本轮交付完成后立即停止，等待 Owner 审核 300 张。Owner 完成后只做：

1. 联结 append-only 裁决并验证 300 ID；
2. 报告总体 YES / NO / SKIP；
3. 解盲内部 `yes_like` 与 `similar_no_boundary` 的 YES 率；
4. 比较形态/几何分布，判断检索是否真正富集“早期启动前沿”；
5. 给出是否值得建立高纯度 early-positive 与 similar-NO 训练集的诊断建议。

未经 Owner 新授权，禁止训练 R3/R4、改 positive 标签、conf、NMS、窗口、ACTIVE、部署、下单、
清空 forward log 或读取 holdout。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

.venv/bin/python scripts/build_local_signal_v2_early_frontier_review.py \
  --frozen-main-commit 84501ad199034a8ffac8f9091a0015a55ccde54b

.venv/bin/python -m pytest \
  tests/test_build_local_signal_v2_early_frontier_review.py \
  tests/test_build_local_signal_v2_semantic_review.py \
  tests/test_serve_local_signal_v2_semantic_review.py -q

python3 scripts/md_to_html.py \
  analysis/p2_local_signal_v2_early_frontier_review300_prereview_20260812.md \
  --out-dir analysis/html
```
