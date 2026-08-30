# P0：5m MA Launch 因果重建与全量审计（2026-08-31）

## 结论先行

今天训练用的 5m 原始 K 线没有证据表明损坏；有问题的是**训练输入、标签时间线和评估单位的契约**。

- 历史单视图数据集有 3,369 / 3,812（88.38%）张图看到了声明入场点之后的 K 线；8 视图数据集为 25,887 / 29,619（87.40%）。
- 旧 outcome 逻辑以 `core_end+2` 的 close 入场，却允许同一根已经走完的 high/low 触发 TP/SL；这是收盘入场后的同 bar hindsight。
- 8 视图包实际是 3,834 个事件扩成 29,619 张图，平均 7.73 张/事件；旧 `build_summary.json` 把 29,619 误写成 `unique_events`。
- 修复时间边界后，共同事件中 126 / 3,806（3.31%）的 TP/SL 类别翻转，说明这不是文档措辞问题，而是会改标签的实质错误。
- 全新的 `causal_v2` 已生成并通过全量审计：3,829 张图逐张从源 K 线重渲染，3,829 / 3,829 像素一致；7,658 个图/标签文件哈希通过；未来可见、候选/结果联结、时间 split、重复事件、重复像素、跨 split 重叠均为 0。

但 `causal_v2` 仍然不是 Gold：TP 只能说明未来结果，不能证明画面上的规则 proposal 就是 Owner 认可的 L1 形态；SL 也不能证明形态不存在。因此本轮没有训练、没有读 holdout、没有 promote、没有部署。已额外生成 200 张 outcome-blind Owner 复核包，作为下一道人工语义门。

## 本轮授权与边界

Owner 本轮授权：“把你刚刚讲的方案全部做一遍”。预注册将其解释为：完成因果契约修复、数据重建、全量审计和人工复核入口；不包含新训练、holdout 读取、阈值/TP/SL/成本调整、promote、部署或真金操作。

架构决定记录在 `docs/decisions/0001-separate-causal-l1-detection-from-l2-outcomes.md`：

- L1 只回答“现在是否看得到 Owner 定义的形态、框在哪里”；
- L2 outcome ledger 才允许读取未来 TP/SL/timeout；
- outcome-conditioned YOLO 包只能作为诊断产物，不能冒充 Owner Gold。

## 旧数据集具体错在哪里

### 1. 模型输入看到了入场之后

历史预注册声明在 `core_end+2` 收盘入场。旧 manifest 的 `post_bars` 却在 2–9 之间变化；只要 `post_bars>2`，图里就包含入场之后的 K 线。缩短总窗口并不能修复这个问题，决定前视的是窗口**右端**落在哪根。

### 2. 收盘入场却复用了该 bar 的盘中极值

旧 outcome slice 从 entry bar 自身开始。若在该 bar close 才入场，它此前的 high/low 已经发生，不能再作为入场后的 TP/SL。新契约固定为：

```text
core_end_i
    └─ +2 = decision_i = visible_end_i
                      └─ 该 bar close 入场
                         decision_i + 1 开始判 TP/SL
```

标签生成和经济评估现在都调用同一个 `resolve_barrier_after_close()`，并由反例单测钉住：decision bar 即使已经越过 TP，也不能成为入场后成交。

### 3. 把未来盈利当成视觉真值

TP 图画框、SL 图写空标签，回答的是“这次以后赚没赚”，不是“这一刻有没有密集启动形态”。这类标签可以用于 L2 结果研究，却没有资格自动成为 L1 Gold。新数据集保留它仅为诊断，manifest、receipt 和注册表都固定 `training_eligible=false`、`production_eligible=false`。

### 4. 8 视图把图片数当成了独立样本数

8 视图不是 29,619 个独立事件，而是 3,834 个事件的相关视图。即使事件不跨 train/val，按图片 bootstrap、计算 fire rate，或用最高 confidence 选较晚视图，仍会重复加权并改变 entry。新构建默认一事件一图；未来若保留多视图，评估器会先按 `event_id` 取最早可见 proposal，再做 matched control 和 bootstrap。

### 5. 旧 QA 为什么没有拦住

旧 QA 检查了文件存在、像素重复、split 和 holdout，但没有从源 K 线重建每张图，因此只能证明“文件与自己写出的 manifest 自洽”，不能证明“像素真的截止在 manifest 声明的 decision”。本轮审计把 source-to-pixel 全量重渲染设为硬门。

## 数据与构建统计

### L2 因果 outcome ledger

| 项目 | 数值 |
|---|---:|
| 输入规则 proposals | 3,940 |
| 唯一 event_id | 3,940 |
| 源文件 | 575 |
| core_end 范围 | 2020-01-13 13:25 UTC ～ 2026-04-30 18:15 UTC |
| 完整 144-bar outcome | 3,936 |
| 因临近边界缺完整 horizon 而拒绝 | 4 |
| TP | 1,729 |
| SL | 2,115 |
| timeout | 92 |
| 最晚 label horizon 右端 | 2026-04-30 03:20 UTC |
| holdout 行读取 | 0 |

固定参数没有调整：5m、`decision=core_end+2`、decision close 入场、下一根开始、TP5/SL2、144 bars、same-bar conservative SL、barrier-price gap、0.2% 往返成本。

### `causal_v2` 诊断图像集

| split | 图/事件 | TP 正例 | SL 空标签负例 | LONG 框 | SHORT 框 |
|---|---:|---:|---:|---:|---:|
| train | 3,251 | 1,460 | 1,791 | 696 | 764 |
| val | 578 | 260 | 318 | 133 | 127 |
| 合计 | 3,829 | 1,720 | 2,109 | 829 | 891 |

另有 92 个 timeout 明确排除，没有强塞成负类；15 个事件落在 2025-12-01 切点两侧 450-bar purge 带内而排除。每个 event 只保留一张 1280×742 图，窗口右端固定等于 decision。

## 旧版与新版同表对照

| 指标 | 历史 1-view v1 | 历史 8-view v1 | causal_v2 |
|---|---:|---:|---:|
| manifest 行数 | 3,812 | 29,619 | 3,829 |
| 唯一事件 | 3,812 | 3,834 | 3,829 |
| 平均行/事件 | 1.00 | 7.73 | 1.00 |
| 多行事件数 | 0 | 3,687 | 0 |
| 含入场后 bar 的图 | 3,369 | 25,887 | 0 |
| 含入场后 bar 比例 | 88.38% | 87.40% | 0.00% |
| event 跨 split | 0 | 0 | 0 |
| 完全相同像素 hash 组 | 0 | 0 | 0 |
| 统计单位可直接按图使用 | 是 | 否 | 是 |
| entry 与 label/eval 共用 resolver | 否 | 否 | 是 |
| 训练资格 | 历史、撤销解释权 | 历史、撤销解释权 | 否，等待 Owner 语义裁决 |

共同的 3,806 个 1-view 事件里，126 个标签改变：124 个由 negative 变 positive，2 个由 positive 变 negative。主要不对称来自旧逻辑把 entry bar 已发生的 SL 极值计入结果；修复后这些事件不再被提前判输。

## 全量审计结果

| 硬门 | 结果 |
|---|---:|
| YOLO 图/标签配对 | PASS |
| 实际文件 hash | 7,658 / 7,658 通过 |
| 源 K 线逐图重渲染 | 3,829 / 3,829 像素一致 |
| visible/decision/outcome-start 因果错误 | 0 |
| candidate lineage 错误 | 0 |
| outcome lineage 错误 | 0 |
| time split / purge 错误 | 0 |
| event 跨 split | 0 |
| image hash 跨 split | 0 |
| 重复 event_id | 0 |
| 重复 image hash | 0 |
| holdout 行读取 | 0 |

这是非方向性数据审计，因此 val AUC、置换排名 p、top-decile 毛/净收益、胜率和 matched-random economic control 按字面不适用；本轮没有模型分数可报告，也没有编造空值。

同等严格的零假设是：保持正负类数量不变，把 outcome kind 在 event_id 间随机置换 1,000 次，再检查图像标签与 outcome ledger 的联结。真实联结匹配 3,829 / 3,829；置换均值 1,934.018、最大 2,035，经验 `p=0.000999`。这证明新 manifest 按声明联结到 outcome；它**不证明** TP/SL 标签具备 L1 视觉语义。

## 历史模型结果应怎样解释

历史 1-view 模型的最佳 val `mAP50=0.40725`（epoch 10），最佳 `mAP50-95=0.34178`（epoch 16）。更重要的是，其旧经济评估在 367 个 proposal 上：

- 模型池净收益：-0.6118 ATR；
- 同月 × 同波动桶 matched control：-0.5883 ATR；
- 模型相对对照：-0.0235 ATR；
- 95% bootstrap CI：[-0.3361, +0.3089] ATR。

即使暂不考虑本报告发现的时间契约问题，旧 1-view 模型也没有证明经济 edge。发现前视并不意味着“原来其实有好模型”，而是旧视觉指标和旧经济入口都不再有资格支撑 promotion。8-view 运行同理必须留作历史记录，不能用图片量或自家 val 当生产裁决。

## Outcome-blind Owner 复核包

已生成 `datasets/ma_launch_5m_shape_blind_review_v1/`：

- 200 张，按 train/val × 隐藏 TP/SL × LONG/SHORT 共 8 层，每层 25 张；
- 公开图片统一改名 `R0001`…`R0200`，公开 manifest / Label Studio tasks 不含 event、outcome、split、source；
- 200 / 200 图像保持源 causal PNG 的逐字节 hash，公开标签均为空；
- 私有 `admin/truth.jsonl` 单独保存真实联结、分层总体数、抽样概率和估计权重；
- Owner verdict 为 `KEEP_LONG`、`KEEP_SHORT`、`REMOVE`、`UNCERTAIN`；KEEP 时再画一个 tight `dense_cluster` 框；REMOVE/UNCERTAIN 不会自动变训练负例。

该包是语义审计入口，不是已完成标注，也不是 Gold。

## 复现命令

以下命令从同一候选 ledger 生成一套新的临时产物，不覆盖正式数据集：

```bash
cd /Users/zhangzc/fable-trading
git show a39f383 --stat

REPRO_DIR="$(mktemp -d analysis/output/causal-v2-repro.XXXXXX)"

python3 scripts/simulate_5m_ma_launch_outcomes_causal_v2.py \
  --out "$REPRO_DIR/outcomes"

python3 scripts/build_5m_ma_launch_outcome_causal_v2.py \
  --outcomes "$REPRO_DIR/outcomes/outcomes.jsonl" \
  --dst "$REPRO_DIR/dataset" \
  --receipt-dir "$REPRO_DIR/receipts"

python3 scripts/audit_5m_ma_launch_outcome_causal_v2.py \
  --dataset "$REPRO_DIR/dataset" \
  --outcomes "$REPRO_DIR/outcomes/outcomes.jsonl" \
  --out "$REPRO_DIR/causality_audit.json"

python3 scripts/build_5m_ma_launch_shape_blind_review_v1.py \
  --dataset "$REPRO_DIR/dataset" \
  --audit "$REPRO_DIR/causality_audit.json" \
  --dst "$REPRO_DIR/blind_review" \
  --receipt "$REPRO_DIR/blind_review_receipt.json"

python3 scripts/compare_5m_ma_launch_dataset_contracts.py \
  --new "$REPRO_DIR/dataset" \
  --out "$REPRO_DIR/old_new_comparison.json"

python3 -m pytest -q \
  tests/test_canonical_outcomes.py \
  tests/test_ma_launch_5m_causal_contract.py \
  tests/test_evaluate_detector_net_returns_causal.py \
  tests/test_build_5m_ma_launch_shape_blind_review_v1.py \
  tests/test_compare_5m_ma_launch_dataset_contracts.py
```

正式报告 HTML：

```bash
python3 scripts/md_to_html.py \
  analysis/p0_5m_ma_launch_causal_rebuild_20260831.md \
  --out-dir analysis/html
```

## 测试与仓库级红灯

本轮目标测试 23 / 23 通过。`tests/boundaries + tests/causality` 的扩展运行结果为 276 passed、4 skipped、8 failed；8 个失败均可在本轮修改前的 HEAD 中复现，与 causal_v2 代码无关：

1. 本机 `fastapi/opencv-python/pyyaml` 分别为 0.128.0 / 4.12.0.88 / 5.3.1，而仓库 pin 为 0.128.8 / 5.0.0.93 / 6.0.3，共 3 个跨机依赖契约失败；
2. 现有 `artifacts/registry.yaml` 使用了 schema 不接受的 `preregistered_before_holdout_read` 等 holdout 状态，导致 5 个 teacher registry 测试在加载阶段失败。

本轮没有顺手修改依赖契约或历史 holdout 登记；前者必须三机一致，后者涉及历史 holdout 语义，应该作为独立修复处理。

## 风险与诚实声明

- 通过的是因果、格式、像素和 lineage，不是形态语义，也不是经济有效性。
- TP/SL outcome-conditioned 图像集仍可能学习结果相关的视觉 shortcut；因此资格保持 false。
- 200 张盲审尚未由 Owner 完成；没有逐样本确认，就没有新的 L1 Gold。
- 本轮没有训练 causal_v2。任何“修完就重训”的动作都需要 Owner 新授权，并且仍受项目 P0/P1 gate 约束。
- 本轮没有读取 2026-05-04 及之后的 holdout；`holdout_rows_read=0`。
- 没有修改旧数据集、旧权重、ACTIVE、forward_log、阈值、TP/SL、成本或实盘配置。
- 工作树里仍有 Claude/用户的其他未提交文件；本轮提交均显式逐文件 staging，没有夹带它们。当前本地 main 还领先 origin 多个提交，未 push，避免把既有和本轮本地提交一并推送。

## 下一步选项（需要 Owner 决策）

1. **先做 200 张 outcome-blind 复核（推荐）**：这是判断 rule proposal 能否成为 L1 Gold 候选的唯一直接证据。
2. 若加权 KEEP/方向一致率与重复标注稳定性达到 Owner 门槛，再建立真正的 L1 Gold；不要从 TP/SL 自动回填。
3. Gold 释放后，由 Owner 单独批准是否训练一个全新的 causal detector；训练前再次跑 dataset gate，且禁止 holdout。
4. 独立处理当前 8 个仓库级红灯：先裁决跨机依赖版本，再迁移非法 holdout status；不要把这两项混进模型实验。

## 主要产物与身份

- 因果 outcome ledger：`analysis/output/ma_launch_5m_outcomes_causal_v2_20260831/outcomes.jsonl`，SHA-256 `cd669b1c...d705e`；
- causal_v2 manifest：`datasets/ma_launch_5m_outcome_causal_v2/manifest.jsonl`，SHA-256 `c84df475...d4c58`；
- 全量审计：`experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/causality_audit.json`，SHA-256 `dcd6762c...f198`；
- 旧新对照：`experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/old_new_comparison.json`，SHA-256 `fd7c3d35...358c`；
- 盲审 public manifest：`datasets/ma_launch_5m_shape_blind_review_v1/public/manifest.jsonl`，SHA-256 `a22fb3f9...ff5`。

生成器先于正式产物提交：核心契约/构建/审计 commit `a39f383`，盲审构建器 `89383e9`，旧新对照与 learnings `aa4cc06`。
