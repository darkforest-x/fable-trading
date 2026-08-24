# P1 Owner-long 待审核候选 manifest v2

日期：2026-08-24

## 结论先行

做多数据集已经从“来源混杂”推进到一份可复核、不可覆盖的 **v2 待审核账本**，但它仍然不是训练集。

- 唯一类别事实来自 Owner 逐框方向表：2,525 个原框中有 1,152 个 `long`；合并 8 个重复别名后为 **1,144 个唯一做多候选**。
- 唯一候选的审核顺序为 A=339、B=480、C=325。A/B/C 只决定人工审核先后，不自动改变标签。
- v2 为每个唯一目标增加稳定 `event_id`，并保存每个 Owner alias 的原行 SHA、原始 bar 几何、原始 YOLO 几何、审核图路径与 SHA。
- v2 把审核页面 public manifest 的完整样本集合及 SHA 锁进每条候选；同一目标的所有 alias 必须全部审核且结论一致，才能从 `PENDING` 变为 KEEP/REMOVE/UNCERTAIN。
- time split 仍为 train=963 / val=171 / drop=10。当前只证明 train→val 相隔 **158 个名义 15 分钟时间格**；真实 OHLC bar gap 必须等 KEEP 物化时用受限前缀读取器重验，当前明确为 `null`，不再提前宣称通过。
- 构建过程没有打开 OHLC、没有读取 holdout、没有渲染图片或标签。1,144/1,144 均为 `PENDING`，且 `training_eligible=false`、`production_eligible=false`。

正式 v2 候选 manifest：
`datasets/owner_long_gold_center_candidate_v2/candidate_manifest.jsonl`

SHA256：
`0b342a75e55d66a99d84e3d6a5be2be90c4e2f3e9de436aef37939cc1d31e929`

大小：3,241,092 bytes。

旧 v1 保留为历史记录，但已被本报告取代，禁止继续注册为 active 或拿去物化训练数据。

## v1 为什么必须被取代

Luna Max 复核发现 v1 有四个证据缺口：

1. v1 把时间戳差换算出的 158 写成了“实际 158 bars”；源 OHLC 尚未读取，无法证明中间没有缺 bar。
2. v1 没有逐 alias 保存 Owner 原始几何与原行哈希，重复目标虽然合并了，但原始标注谱系不够完整。
3. v1 的 partial review 逻辑允许一个 alias 已审、另一个未审时提前解析唯一目标。
4. v1 没有把审核页面的完整样本集合和文件 SHA 锁进候选账本，页面漂移时无法 fail closed。

v2 不覆盖 v1，而是生成新目录、新 candidate ID 和新 manifest SHA；这使复核结论和历史产物都可追踪。

## 冻结来源

| 来源 | 作用 | SHA256 |
|---|---|---|
| `analysis/output/owner_side_review/review_sheet.csv` | Owner 逐框方向及原始几何 | `bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a` |
| `datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/admin/owner_2525_scores.jsonl` | 仅用于 A/B/C 审核排序 | `0752df544308e6c26f647fc1a520375a551ed003e234d12f3e50dd8a05deef0b` |
| `datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/calibration.json` | A/B/C 冻结阈值 | `bd09185267353a4f7381969784d2824b0e128fe85e08b3d53a212227458fadb2` |
| `datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/owner_2525_manifest.json` | 审核 ID 与完整样本集合 | `ae65f868256632b32e627927fa7443b128cfe03492d52d6a1d055dcb8dd75e69` |

以下来源明确禁止混入做多正例：

- short R1 的 916 个 `owner_long` 空标签 hard negative：只表示“不是 short”，不是 long Gold；
- R1 的 2,286 个 hard negative：不是 Owner-long 正例；
- fixed-W10 旧 triage 包：口径已被 Owner 原始方向表取代；
- public review manifest：它是审核队列，不是训练 manifest。

## 数据谱系与统计

| 层级 | 数量 | 当前含义 |
|---|---:|---|
| Owner 方向表原框 | 2,525 | long 1,152 / short 1,361 / skip 12 |
| Owner-long 原始框 | 1,152 | 类别逐框确认 |
| 重复 target groups | 8 | 保留全部 alias 谱系，只生成一个唯一目标 |
| 唯一 long 候选 | 1,144 | 待第二遍形态过滤 |
| A / B / C 唯一候选 | 339 / 480 / 325 | 只控制审核顺序 |
| KEEP | 0 | 尚无本轮 Owner 导出回执 |
| 新图片 / 新标签 | 0 / 0 | 审核前禁止物化 |
| 可训练样本 | 0 | eligibility 仍关闭 |

manifest 独立复核闭合了以下关系：

- 1,144 个 `event_id` 全部唯一，且可从 symbol、源路径、窗口和核心边界稳定重算；
- 1,152 个 alias 的集合与方向表中的 1,152 个 long 原框完全相等；
- 1,152 个 Owner 原行 SHA 全部重算一致；
- 1,152 个审核图 SHA 全部重算一致；
- 全部候选严格早于 `2026-05-04T00:00:00Z`；最晚可见窗口结束于 `2026-05-03T11:30:00Z`；
- manifest 目录只有账本、来源合同和摘要，没有训练图片或标签。

## 时间切分与 purge 证据等级

| 项目 | v1 写法 | v2 当前证据 |
|---|---|---|
| train / val / drop | 963 / 171 / 10 | 963 / 171 / 10 |
| dependency blocks | 1,097 | 1,097 |
| cross-split dependency | 0 | 0 |
| cross-split event | 未登记 | 0 |
| train→val 间隔 | “实际 158 bars” | 158 个名义 15m 时间格 |
| 真实 OHLC bar gap | 被提前当作已证明 | `null`，等待受限物化 |

`split` 仍按同币重叠可见窗口组成不可拆依赖块，再按时间选最后 15% block 为 val，并留 150 个名义时间格的 purge。这个预切分可以冻结审核前的归属，但删除样本、读取真实 OHLC 后仍必须重验。

后续物化只能调用 bounded pre-holdout prefix loader，并为每条 KEEP 记录：

- `source_preholdout_prefix_sha256`；
- `source_window_sha256`；
- `rendered_image_sha256`；
- `label_sha256`；
- `max_materialized_time`；
- `holdout_rows_materialized=0`；
- `actual_ohlc_gap_bars`。

整个仍在增长的 CSV 的 EOF SHA 被明确禁止拿来代替因果前缀或样本窗口 SHA。

## 审核回执门

现有快捷页面默认选择 `A 核心档 + long + 未审核`，展示 Owner 原审核图，而不是重新绘制的左右对照图。

回执联结器会：

1. 校验 pack ID、public manifest SHA、2,525 个唯一 review/sample ID 和完整样本集合；
2. 要求导出时间带时区，并显式登记 reviewer；
3. 拒绝未知、空白或重复答案；
4. 同一唯一目标若有多个 alias，必须全部审核且结论相同；部分完成继续保持 `PENDING`，冲突直接失败；
5. 输出新的、不可覆盖的 review receipt 目录；
6. 即使 KEEP，也继续保持 `training_eligible=false`，不自动渲染、不自动训练。

## 质量门

| 门 | 结果 |
|---|---:|
| 方向表 / 排序表 / public manifest 样本集合闭合 | PASS |
| Owner 原始 bar 与 YOLO 几何合法 | PASS |
| long 1,152 → unique 1,144 | PASS |
| 稳定 event ID 唯一 | PASS（1,144/1,144） |
| Owner 原行 SHA 重算 | PASS（1,152/1,152） |
| Owner 审核图 SHA 重算 | PASS（1,152/1,152） |
| 严格 pre-holdout | PASS（1,144/1,144） |
| 名义时间格 purge ≥150 | PASS（158） |
| 真实 OHLC purge | DEFERRED，不冒充 PASS |
| 打开 OHLC / 读取 holdout | 0 / 0 |
| 新训练图片 / 标签 | 0 / 0 |
| training / production eligible | 0 / 0 |
| 相关自动测试 | 183 passed / 2 skipped |

## 非方向性零假设对照

本轮只做数据谱系、审核合同和切分门禁，没有模型训练、阈值选择或交易回测。因此 val AUC、top-decile 毛/净收益、胜率、0.2% 成本和匹配随机入场对照均不适用；不能用旧模型结果填充。

同等严格的零假设仍是：如果六均线排序分与 Owner keep/drop 无关，打乱裁决后结果应与真实裁决相当。冻结的 390 条人工 keep/drop 对照得到 AUC=0.489、10,000 次置换 `p=0.635`，不能拒绝“排序无效”的零假设。因此 A/B/C 只能帮 Owner 先看更可能的样本，不能自动产生 KEEP/REMOVE。

## 风险与诚实声明

- `owner_side=long` 是逐样本方向确认；机械派生的 4–7 根核心不是逐样本边界确认。
- 用户口径“A 档基本都能保留”说明 A 应优先审核，不等价于 339 个唯一目标已经逐样本确认。
- 1,144 是旧人工池的候选上限，不是最终正样本数；最终数量由 KEEP 回执决定。
- 当前没有真实 OHLC gap、因果窗口、图片或 label 哈希，故绝不能启动训练。
- 未审和 `UNCERTAIN` 都不能进入正例；重复 alias 不能靠一个答案代替全部答案。
- 没有读取 holdout、没有训练、没有 promote、没有部署、没有下单。

## 下一步

1. Owner 先审核 A 档 long：页面上是 343 个原始框，对应 339 个唯一目标；快捷键 `K/1=保留`、`X/2=去掉`、`?/3=待定`。
2. 导出 JSON 后，用 `join-review` 生成独立回执；只要 alias 不完整或冲突就停止。
3. A 档结果稳定后再决定是否审核 B；C 不自动删除。
4. 仅对 KEEP 运行 bounded causal materialization，记录前缀/窗口/图片/标签 SHA，重验实际 bar purge、图片标签 parity 和 split 依赖。
5. 生成新的 Gold dataset 版本，仍默认 `training_eligible=false`；由 Owner 看最终统计与抽查结果后单独批准训练。
6. 旧人工池冻结后，再用 short 模型式的主动学习循环扩充做多候选；模型提议只进审核池，不自动进 Gold。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# 构建器必须先落库；正式 v2 由这个 commit 生成。
git show --stat 47f7557bc9f0cb3a35d08440c8d090e107d7c9e2

# 不覆盖正式工件，重建到新的审计临时目录。
owner_long_audit_dir="$(mktemp -d)"
.venv/bin/python tools/datasets/owner_long_candidate.py build-pending \
  --generator-commit 47f7557bc9f0cb3a35d08440c8d090e107d7c9e2 \
  --out "$owner_long_audit_dir/owner_long_gold_center_candidate_v2"

.venv/bin/python -m pytest -q \
  tests/test_owner_long_candidate.py \
  tests/causality/test_holdout_boundary_is_single_valued.py \
  tests/boundaries

# 收到 Owner 页面导出后才运行；每次输出必须是新目录。
.venv/bin/python tools/datasets/owner_long_candidate.py join-review \
  /path/to/ma_rope_prefilter_v1_answers.json \
  --reviewer owner \
  --out datasets/owner_long_gold_center_candidate_v2/review_receipts/owner_export_YYYYMMDD_v1

python3 scripts/md_to_html.py \
  analysis/p1_owner_long_candidate_manifest_v2_20260824.md \
  --out-dir analysis/html
```

## 产物

- 候选 manifest：`datasets/owner_long_gold_center_candidate_v2/candidate_manifest.jsonl`
- 构建摘要：`datasets/owner_long_gold_center_candidate_v2/summary.json`
- 来源合同：`datasets/owner_long_gold_center_candidate_v2/source_contract.json`
- 构建器：`yoyo/datasets/owner_long_candidate.py`
- CLI：`tools/datasets/owner_long_candidate.py`
- 测试：`tests/test_owner_long_candidate.py`

