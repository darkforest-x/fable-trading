# Local Signal V2 Positive 语义审核 PRE-REVIEW v2（2026-08-12）

## 直接结论

Owner指出v1审核包“没有走势对照，且K线像水平线”后，问题已定位并修正。当前正式入口已切换为v2；v1保留为历史构建证据，但不再用于Owner裁决。

- 仍是200个事件：Positive Pool 100、Canary Candidate 100；
- 每个事件物理分离为三张图：冻结模型输入、人眼自适应因果图、独立未来走势对照，共600张PNG；
- 左图只到decision，`visible_end_bar == decision_bar`与`future_bars == 0`均为200/200；
- 右图只供人工审核，Positive固定未来48根，Canary显示holdout边界前可安全取得的16–46根；
- 600张图全部存在且SHA匹配，所有未来对照结束时间早于`2026-05-04T00:00:00Z`；
- 正式裁决仍为0，训练资格0，holdout读取0；
- 没有训练、修改权重、conf、NMS、窗口、positive标签、ACTIVE、部署或下单。

本报告仍是PRE-REVIEW，不能给出Positive purity、Canary precision或模型好坏结论。

## 1. “水平K线”的真实原因

旧审核图逐字节复用了YOLO模型输入。模型渲染器`yoyo.layers.l1_detection.render`为保持训练像素尺度一致，设置了`MIN_REL_SPAN = 0.06`：即使窗口真实波幅只有0.5%–1%，纵轴也至少覆盖现价6%。因此数据没有变平，是蜡烛被6%固定轴压扁。

训练与实盘共用该渲染合同，因此没有train/live缩放不一致；但一致不等于无害。6%下限确实会降低低波幅窗口在像素中的纵向分辨率，可能削弱模型区分力，也可能参与造成连续市场候选过密。本轮冻结模型输入，不在语义审计中顺手改renderer；Owner审核后应按真实波幅分层统计YES/NO，再决定是否把纵轴合同作为下一轮唯一变量。

简单放大图片不会恢复纵向信息，把未来大行情拼到同一纵轴还会进一步压缩当时形态。v2采用物理分层：

| 产物 | 纵轴 | 是否含future | 用途 |
|---|---|---:|---|
| `model_input/` | 冻结模型6%合同 | 0 | lineage与模型输入复验，不直接给Owner判断 |
| `causal_review/` | 当时真实OHLC+六均线实际价差自适应 | 0 | Owner左侧主图 |
| `future_review_only/` | 整段真实价差独立自适应 | 16–48 | Owner右侧走势对照，禁止训练 |

候选橙框不是像素裁切后硬贴，而是用真实bar区间及其高低价重新映射到每张自适应图。竖线标记decision，右图紫色区域才是decision后的人工参考。

## 2. 样本是否被界面修复偷偷换掉

首次v2试构建发现协议名误参与采样哈希，显示改动会导致Positive只与v1重合17/100。该构建没有Owner裁决，已移出正式目录并废弃。最终builder将采样身份冻结为v1协议后重新生成：

| 样本组 | v1 | 最终v2 | 与v1重合 | 变化原因 |
|---|---:|---:|---:|---|
| Positive Pool | 100 | 100 | 100 | 完全不换样本 |
| Canary Candidate | 100 | 100 | 69 | 仅替换31个未来不足16根、无法形成安全走势对照的候选 |

Canary内部配额仍冻结为共同保留50、R2新生25、R1抑制25；UI继续隐藏来源、置信度和内部cohort。31个替换项都来自同一最新Canary母池，不读取其他行情块或holdout。

## 3. 为什么Canary不是每张满48根

最新Canary物理时间块截至`2026-05-03 23:45 UTC`。其中任一下午候选向后取满48根，都会跨入`2026-05-04` holdout。Owner没有授权消耗holdout，因此v2没有偷读：

- Positive 100张的decision最晚为`2026-04-29`，全部安全显示48根；
- Canary从最新母池中保留至少16根安全未来，最终范围16–46根；
- 页面逐张显示实际未来根数，并明确写“未读取holdout”。

如果后续要求最新Canary每张强制48根，必须由Owner另行明确批准该配置消耗holdout；本轮没有这样做。

## 4. 因果与数据隔离审计

| 检查 | 结果 |
|---|---:|
| review manifest | 200行 |
| Positive / Canary | 100 / 100 |
| 冻结模型输入 | 200张 |
| 自适应因果审核图 | 200张 |
| 独立未来对照图 | 200张 |
| `visible_end_bar == decision_bar` | 200 / 200 |
| 因果图`future_bars == 0` | 200 / 200 |
| 未来对照与因果图物理分离 | 200 / 200 |
| 未来对照结束早于holdout | 200 / 200 |
| 图片存在且SHA匹配 | 600 / 600 |
| Owner verdict预选 | 0 |
| training eligible | 0 |
| holdout读取 | 0 |

需要诚实区分：v2左图仍是严格因果图，但Owner现在可以看到右侧后续走势。因此最终YES率是“有走势对照辅助的Owner语义裁决”，不是完全盲于未来的实时识别精度。右图不会进入模型输入或训练数据，但会影响人工判断；后续报告不得把它冒充纯causal precision。

## 5. 审核界面

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py --port 8766
```

打开`http://127.0.0.1:8766/`。左侧为自适应因果图，右侧为独立未来对照。快捷键仍只有：

- `Y` = YES
- `N` = NO
- `S` = SKIP
- `← / →` = 前后移动

裁决append-only保存到：

`analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_verdicts.jsonl`

真实浏览器已验证：双图加载、未来根数显示、Y/N/S自动保存并前进、刷新恢复、修改上一张及方向键导航均正常。功能测试在临时副本进行；正式API仍返回0个裁决。

浏览器实测截图：`output/playwright/local-signal-v2-semantic-review-v2.png`。

## 6. 关键SHA与可复现命令

| 输出 | SHA-256 |
|---|---|
| builder commit | `0fc58fb6f1aa2bf85e5e732a882ffbb2c7b0f3ad` |
| review manifest | `015074dcb9f874804425b73191227a336b08aea62f2d6ba849301a18f40a7834` |
| 200张因果审核图内容树 | `285061a2b2a8fc73cb21d6112f34a9e0cfdb12de9b570c44648a08301fba482f` |
| 200张模型输入内容树 | `84f9f32fa705bb7e4f075a9fe5a0e01fc272444ad79fd1aa44f3cc1ad88664f5` |
| 200张未来对照内容树 | `b6d881caf6ac9c4413489119ee874741ab16b6c798ca497306b6446d0cb764c4` |
| sampling audit | `bc5cc1a3bdc210a3782f190b0abeeb328e258bc140ab6a854f65972fef2301c4` |
| causality audit | `cf99881a4d6074df6a1fa09f1f8869b7d9a131811ad5e3a2f4ac3540443dc394` |
| freeze receipt | `c2894fa7f96019e866cabacae6ba07eebb879d8c456d2ca1850df03a752f110b` |
| review HTML | `9410502e660c5e1b5ae35ce0a92e7cc152d1169dc1234abfe1384191cd164a18` |

复现：

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/build_local_signal_v2_semantic_review.py \
  --device mps --batch 16 \
  --frozen-main-commit b134f211b4300d7d95679bebd5c50dc3b23d0789 \
  --out /tmp/local_signal_v2_positive_semantic_review200_v2_rebuild

PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/pytest -q tests
```

项目全量测试：`688 passed, 2 skipped`。

最终builder提交前后各自独立构建的manifest、因果图树、模型输入树、未来图树、sampling audit、causality audit、HTML与README八个轴逐SHA一致；仅freeze receipt按设计记录了新的builder commit。

## 7. 指标状态、风险与停止条件

| 指标 | PRE-REVIEW状态 |
|---|---|
| Positive purity | N/A，等待Owner完成100张Positive |
| Canary candidate YES率 | N/A，等待Owner完成100张Canary |
| common / R2-new / R1-suppressed YES率 | N/A，完成后解盲 |
| val P/R/mAP、AUC、收益、置换检验 | N/A，本轮禁止训练与交易裁决 |

风险与边界：

- v2是Owner明确要求的“未来走势辅助审核”，存在后见偏差；汇总报告必须用这个名字，不得写成实时precision。
- Canary为了至少16根安全对照，时间分布偏向较早事件；总体YES率仍是诊断样本，不可直接外推398 events/day。
- 自适应纵轴只改变人工显示，不改变模型输入像素、标签框、conf、NMS或窗口。
- 模型6%纵轴是否损害低波幅识别尚未验收；本轮保留每张真实波幅与模型输入，待Owner裁决后做分层诊断，不提前宣布安全。
- v1没有删除，但已被v2取代；不要在两个版本之间混合裁决。

当前停止条件已再次满足。下一步只有Owner审核v2的200张；完成前不训练R3/R4，不继续添加hard negative。
