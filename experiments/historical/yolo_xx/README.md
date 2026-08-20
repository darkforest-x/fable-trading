# yolo-xx — Pattern Teacher / Pattern Quality / Causal Onset（已归档）

| 项 | 值 |
|---|---|
| 来源仓 | `darkforest-x/yolo-xx` |
| 冻结 commit | `9296cfa8e5053d86cea44e29dbd45874c3dff689` |
| 最终状态 | `historical_research` |
| 机器摘要 | [`summary.json`](summary.json) |
| holdout | **0 次消耗** |

## 最重要的一条：完整上下文 ≠ Tip

| 评估口径 | 旧 detector 复现率 |
|---|---|
| 完整上下文 | **62–72%** |
| Tip（只到当前已收盘 K 线） | **9–10%** |

**识别一个已完成的形态，和在盘口识别一个正在形成的形态，是两个任务。**
这就是 YOLO 在本项目被保留为 Pattern Teacher 而**不是**最终 Tip 触发器的全部理由，
也是 `yoyo/contracts/candidates.py` 把 `available_at` 定义为
「生成器最后一根输入 K 线的收盘时间」而不是「框的右边界」的原因。

## 可信数字（仅作基线，不可与新任务的事件召回/延迟混比）

| 项 | 结果 |
|---|---|
| Pattern Library | 2,366 条候选，其中 829 条已人工分级 |
| Pattern Quality（A 级分类，val 侧） | AUC 0.6323，95%CI [0.5661, 0.6945]，κ 0.159 |
| Formation v1（提前 30 根 = 7.5h） | AUC 0.6453，95%CI [0.5846, 0.7032]，κ 0.206 |
| **Formation（GPT 原案 T0–T30 = 距完成 8 根）** | **AUC 0.7417，95%CI [0.6888, 0.7882]，κ 0.295** |
| Formation 负样本严格污染率 | 4.0%，95%CI [1.1%, 13.5%] |

各臂的置信区间彼此重叠，κ 0.295 至多算 fair agreement。
"最好的数字"不等于"可以上生产"。

## 三条负面结论

### 1. pooled AUC 0.8067 是在分来源，不是在分形态

659 样本合池 AUC 0.8067 看似突破。**按 source 分层后每个子集只剩 0.57–0.65**，
三个来源的正类率是 **85% / 84% / 29%**。渲染管线当时已经统一，仍然中招——
这是 v15「正负样本两条渲染管线」的同族变种，触发面从渲染换成了采集批次。

**分组 CV 防不住这个。** 分组 CV 防的是「同一实体跨折」，这里的病是
「不同来源基率不同」，是两种病。
见 `docs/learnings/pooled-auc-can-be-source-discrimination.md`。

### 2. Quality ranker v1 的提升是泄漏

方向中性的几何特征打分 0.53 / 0.55；加特征后升到 0.64 —— 那个提升是泄漏，不是信号。

### 3. 事件本来根本没有方向

619 个带 owner 方向裁决的 golden_pool 框里 **287 个是做多**。
全部按做空计分（`gold_hindsight.csv` 就是这么干的）时：
做空侧 +273.9bp / 命中率 83.3%，做多侧 **−182.6bp / 0-24**。
合池报出来是 141.3bp / 58.9%，**两边都被藏住了**。

而且它污染特征工作：price-relative-to-cluster 区分多空框的 AUC 是 **0.988**，
所以任何在合池数据上训练的模型学到的是方向，任何在合池数据上做的单特征研究测的也是方向。
A/B 等级本身也偏（A 类 74% 做空、B 类 37%），所以光看等级也泄漏方向。

**这就是 `side` 必须是人给的答案、绝不能从几何推断的原因**——
推断出来的 side 会按构造复现那个 0.988，什么也证明不了。

## 迁进来的东西

| 能力 | 落点 | 裁决 |
|---|---|---|
| Causal Onset v3 六锚点 schema | `yoyo/layers/l1_detection/onset/events/schema.py` | `DIRECT_PORT` |
| validator（锚点越界=错误，launch 早于 confirm=警告） | `.../validator.py` | `DIRECT_PORT` |
| **不自动填 onset** 的 v1→v2 迁移 | `.../migration.py` | `DIRECT_PORT` |
| progressive reveal + 渲染期物理盲化 + 隐藏重复 | `.../review_pack.py` | `DIRECT_PORT` |
| 逐 tip 渲染 | `.../render_frames.py` | `ADAPT_AND_PORT`（删掉跨仓 sys.path 桥） |
| 权重血统与 SHA-256 登记 | `artifacts/registry.yaml` | `REFERENCE_ONLY` |

### 权重：登记，不复制

`owner_v10_chain.pt` 的摘要
`b9a84b5f5ebf0032dfa8ddf1ed1f12c19b7cc2d410a57480bd196d76cbc7d953`
已三方核对（本仓副本、3060 上的 `best.pt`、`C:\fable\base_hts.pt`）。
血统：`yolo11s (COCO) → v7_chain → v8_chain → v9_chain → v10_chain`。

3060 上的 59 个权重登记为 `storage_uri: host://windows-3060/C:/fable`，不复制。
**"权重已被清除"不能再当作前提**——见
`docs/learnings/purge-records-are-claims-not-facts.md`。

注：[`pattern_teacher_asset_inventory.md`](pattern_teacher_asset_inventory.md)
里 v10_chain 的 SIZE 写作 18,185,754，实际是 19,185,754（少打一位）。
**摘要是对的**——这正是产物按哈希而不是按大小识别的理由。

## 明确拒绝的

`src/yolo_xx/{predict,scan_predict,scan_set,train}.py`（已被替代的 bbox-only 默认 CLI）、
`src/yolo_xx/outcome.py` 及收益/判断层支线、全部 datasets 图片、全部 runs、
全部 scan overlays、全部权重副本。yolo-xx 自己的 README 已声明这些是历史资产：
不删除、不扩展、不被新 core import、不作默认 CLI、不决定验收结论。
