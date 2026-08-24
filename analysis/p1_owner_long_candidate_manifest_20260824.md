# P1 Owner-long 待审核候选 manifest

日期：2026-08-24

## 结论先行

做多数据的第一层谱系已经冻结，但**还不是可训练数据集**。

- 从 Owner 逐框方向表的 1,152 个 `long` 标注出发，按完全相同的 symbol、可见窗口和核心几何合并 8 个重复别名，得到 **1,144 个唯一候选目标**。
- 唯一目标的六均线审核排序为 A=339、B=480、C=325。此前页面显示的 long A=343 是去重前的原始框数，两者口径不同但能够闭合。
- 按同币重叠窗口先组成依赖块，再做最后 15% 时间块验证和 150 根 purge，得到 **train 963 / val 171 / drop 10**；train 与 val 实际相隔 158 根，跨 split dependency=0。
- 最晚可见窗口结束于 `2026-05-03T11:30:00Z`，早于 holdout `2026-05-04T00:00:00Z`。
- 构建器没有打开任何 OHLC 文件，没有渲染图片或标签，没有使用未来结果；1,144/1,144 均为 `owner_filter_decision=PENDING`、`training_eligible=false`、`production_eligible=false`。

正式候选 manifest：
`datasets/owner_long_gold_center_candidate_v1/candidate_manifest.jsonl`

SHA256：
`5f9707e23e291382a2e595af274ebb0273ccb5aad132590ac1bb3e7b382c2687`

## 为什么这一步先于继续挖模型信号

当前 short R1 中的 916 张 `owner_long` hard negatives 是空标签，其含义只是“对 short 检测器而言不是 short”。它们不能通过复制或改名变成做多正标签。做多正类必须回到原始 Owner-long 框和逐框方向事实。

同样，六均线 A/B/C 的独立反证没有证明自动筛选有效：390 条 keep/drop 上 AUC=0.489，10,000 次置换 `p=0.635`。所以 A/B/C 只改变人工审核顺序，不能把 A 自动写成 KEEP，也不能把 C 自动写成 REMOVE。

因此本轮只冻结“候选是谁、从哪里来、如何去重、以后落在哪个 split”，不提前生成会被误认为 Gold 的图片和 YOLO 标签。

## 数据谱系

| 层级 | 数量 | 事实来源 | 当前证据等级 |
|---|---:|---|---|
| Owner 方向表全部框 | 2,525 | `analysis/output/owner_side_review/review_sheet.csv` | Owner 逐框方向 |
| Owner-long 原始框 | 1,152 | `owner_side=long` | 类别已确认 |
| 完全重复别名 | 8 | symbol + visible window + core geometry | 合并，不复制样本 |
| 唯一 long 候选 | 1,144 | 原框机械派生 | 类别确认；核心边界未逐样本确认 |
| 审核 KEEP | 0 | 等待页面导出 | 尚无本轮过滤裁决 |
| 可训练样本 | 0 | 需物化、哈希门和 Owner 批准 | 当前禁止训练 |

每条候选记录包含：

- 原始 Owner annotation ID（重复目标保留全部 alias）；
- symbol、Owner 大框全局行号与时间；
- 机械派生的 4–7 根核心、5–7 根前文、3–5 根后文和 W12–19 可见窗口；
- 已解析 OHLC 路径，但 `source_window_sha256` 明确留空并标记为待物化；
- Owner 原审核图路径与 SHA；审核图明确不能作为模型输入；
- A/B/C 审核顺序、dependency ID、time split；
- `PENDING` 审核状态及两项 eligibility=false。

## 去重与时间切分

| 项目 | 结果 |
|---|---:|
| 原始 long annotations | 1,152 |
| 重复 target groups | 8 |
| 删除的重复 aliases | 8 |
| 唯一 targets | 1,144 |
| dependency blocks | 1,097 |
| train | 963 |
| val | 171 |
| purge drop | 10 |
| train→val gap | 158 bars |
| cross-split dependencies | 0 |

去重发生在 split 之前；split 以同 symbol 的重叠可见窗口为不可拆依赖块。没有随机切分，也没有为了凑 1:1/1:3 数量移动 split。

## A/B/C 口径

| 口径 | A | B | C | 合计 |
|---|---:|---:|---:|---:|
| 原始 Owner-long 框（去重前） | 343 | 482 | 327 | 1,152 |
| 唯一 long targets（本 manifest） | 339 | 480 | 325 | 1,144 |

差异正好来自 8 个重复 aliases：A 少 4、B 少 2、C 少 2。页面仍可能显示原始 alias；回执联结器按唯一 target 汇总，若同一 target 的两个 alias 被判成不同结果会 fail closed。

## 审核回执门

已实现 `join-review`，但因为本机尚未找到页面导出的 JSON，本轮没有伪造任何 Owner 决策。

回执门会：

1. 校验 pack ID、review ID、sample ID 和 decision 枚举；
2. 拒绝未知 ID、重复答案和同一 target 的 alias 冲突；
3. 输出完整 joined manifest 与 KEEP-only candidate manifest；
4. 保留 `training_eligible=false`，不生成图片或标签；
5. 分别报告 A 档是否审完和全部 long 是否审完。

## 质量门

| 门 | 结果 |
|---|---:|
| 2,525 sheet 与 2,525 score 一一联结 | PASS |
| long 1,152 → unique 1,144 | PASS |
| 1,144 全部严格早于 holdout | PASS |
| 依赖跨 split | 0 |
| purge ≥150 bars | PASS（158） |
| 打开 OHLC 文件 | 0 |
| 使用未来 outcome | 0 |
| 新训练图片 / 标签 | 0 / 0 |
| `training_eligible=true` | 0 |
| `production_eligible=true` | 0 |

## 非方向性零假设对照

本轮是数据谱系与切分审计，没有训练模型、交易收益或阈值选择，故 val AUC、top-decile 收益、胜率、成本后净收益和匹配随机入场对照均不适用，不能拿旧模型数字填充。

同等严格的标签质量零假设沿用冻结的 390 条 Owner keep/drop 反证：若六均线分数与保留裁决无关，标签可以交换。实测 AUC=0.489、置换 `p=0.635`，无法拒绝“排序分与裁决无关”的零假设。因此本 manifest 把所有目标保持 PENDING，没有自动 KEEP/REMOVE。

## 风险与诚实声明

- `owner_side=long` 是 Owner 逐框类别事实；4–7 根核心是从原框中心机械派生，未获得逐样本边界确认。
- 本 manifest 是候选 ledger，不是 Gold。图片、YOLO label 和 exact causal OHLC window SHA 均未物化。
- OHLC 路径已解析，但源文件仍会增长；下一步必须只读取每个候选的因果窗口、记录窗口级 SHA，不能用整个可变 CSV 的哈希冒充样本内容哈希。
- 963/171 是未应用本轮 KEEP/REMOVE 的预切分。收到回执后必须在过滤结果上重新验证依赖与 purge；不能默认删除样本后统计不变。
- 没有读取 holdout、没有训练、没有 promote、没有部署、没有下单。

## 下一步

1. Owner 在 long 审核入口先完成 A 档并导出 JSON；A 是 343 个原始框、对应 339 个唯一 target。
2. 用 `join-review` 冻结 A 档回执；若 alias 冲突立即停止，不猜答案。
3. Owner 决定是否继续 B；未审与 `UNCERTAIN` 均不进入新正例。
4. 完成目标范围后，仅物化 KEEP 的因果图与标签，逐样本记录 source-window/image/label SHA，并重验 split。
5. 仍保持 `training_eligible=false`；最终数据统计和盲审门通过后，再由 Owner 单独批准训练资格。
6. 旧人工池稳定后，才启动下一轮模型主动学习候选（模型触发、MA/similarity 未触发探针、随机对照），避免两个未冻结母池同时漂移。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

.venv/bin/python tools/datasets/owner_long_candidate.py build-pending \
  --generator-commit eeaafc87b3776dbb8ede0643cb4f3394289dd82a

.venv/bin/python -m pytest -q \
  tests/test_owner_long_candidate.py \
  tests/boundaries/test_layer_imports.py \
  tests/causality/test_pattern_contract.py

# 收到 Owner 页面导出的 JSON 后才运行：
.venv/bin/python tools/datasets/owner_long_candidate.py join-review \
  /path/to/ma_rope_prefilter_v1_answers.json \
  --out datasets/owner_long_gold_center_candidate_v1/review_results/owner_export_v1

python3 scripts/md_to_html.py \
  analysis/p1_owner_long_candidate_manifest_20260824.md \
  --out-dir analysis/html
```

## 产物

- 候选 manifest：`datasets/owner_long_gold_center_candidate_v1/candidate_manifest.jsonl`
- 构建摘要：`datasets/owner_long_gold_center_candidate_v1/summary.json`
- 来源合同：`datasets/owner_long_gold_center_candidate_v1/source_contract.json`
- 构建器：`yoyo/datasets/owner_long_candidate.py`
- CLI：`tools/datasets/owner_long_candidate.py`
- 测试：`tests/test_owner_long_candidate.py`

