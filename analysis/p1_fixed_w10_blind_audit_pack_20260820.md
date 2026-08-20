# P1 fixed-W10 门禁修复、artifact 谱系与盲审包（2026-08-20）

## 结论

**工程交付完成，标签验收仍待 Owner 盲审。** 已修复旧 acceptance 把迁移前
`DIRECT=0` 与最终 Gold 哈希混在一起、零 DIRECT 空通过比例门、非 DIRECT 抽检可能被计入
DIRECT、错误率可由裸标量注入等问题。新验收从同一份最终 Gold 快照重建，真实总体为
**2,649 张、1,251 条 DIRECT**；在未收到逐条盲审答案前，`training_eligible=false`。

已生成主盲审包：**398 个分层随机唯一项，其中 188 个 DIRECT，加 50 个不可辨认重复项，
合计 448 项**。Cleanlab 的 28 张模型筛选项放在独立优先队列，不进入无偏错误率估计。
本轮没有训练、没有读取 holdout、没有 promote，也没有修改 ACTIVE/frozen。

## 复现命令

构建器先于产物落到 `main`；当前产物由提交
`2e8ce475438a8987960e13eec210f9df1cf97090` 的构建器生成。

```bash
git branch --show-current
.venv/bin/python -m pytest -q \
  tests/test_fixed_w10_p1_acceptance.py \
  tests/test_fixed_w10_blind_audit.py

.venv/bin/python tools/datasets/fixed_w10_p1_audit.py build

# Owner 完成主盲审并导出 JSON 后执行；当前尚未执行
.venv/bin/python tools/datasets/fixed_w10_p1_audit.py score \
  --answers /path/to/fixed_w10_core4_confirm1_v1_p1_blind_audit_v1_answers.json

python3 scripts/md_to_html.py \
  analysis/p1_fixed_w10_blind_audit_pack_20260820.md \
  --out-dir analysis/html
```

## 数据身份与全量校验

本轮注册的是 fixed-W10/Core4/Confirm1 的 2,649 张数据，不是旧 W12–19 V3 的 3,453 张，
也不是 `v2_owner_short` 的 2,599 张 manifest。

| 项 | 路径 / SHA-256 |
|---|---|
| 旧冻结 dataset manifest（保留不覆盖） | `datasets/fixed_w10_core4_confirm1_v1/manifests/dataset_manifest.json` · `20686feba41d15b82e34109402840c2d640fe1e2daea0392b35e1ea79320a7fc` |
| 最终 Gold 快照 | `datasets/fixed_w10_core4_confirm1_v1/gold/events.jsonl` · `344212f8e5ef1fac3616b2026d19d6e721ce29984b3bbda194d4071c9fc327c4` |
| 2,649 张 image manifest | `datasets/fixed_w10_core4_confirm1_v1/manifests/image_manifest.jsonl` · `9f51218a9221308530c68968c525a9f7ac21d20193998431f83fed568d67cefd` |
| 全图集合聚合 SHA | `85aade6088e5f55a0dde91f2a12b7cfc94e785143698e8219fea87f63f542084` |
| 新 artifact manifest | `datasets/fixed_w10_core4_confirm1_v1/manifests/artifact_manifest_v1.json` · `a8a4515b302eb08876e33f1ab967da8d2324d63a4b8ec39d01b672a9ef042f34` |
| 主盲审 public manifest | `datasets/fixed_w10_core4_confirm1_v1/review/p1_blind_audit_v1/public/manifest.json` · `ab2035914f431f0c245fc48dcdf7b8f7fbccb6c078bc139c30a58834a2bd8829` |
| 主盲审私有 truth | `datasets/fixed_w10_core4_confirm1_v1/review/p1_blind_audit_v1/admin/truth.jsonl` · `d470d27552113735fcbca78136395db1d845db314f7b38e337c23116cb3d7878` |

构建时逐张重算 2,649 个 PNG 的 SHA，核对 Gold 与 image manifest 的 `gold_id` 一一对应，
并强制每条 `window_end_exclusive_bar == decision_bar + 1`、`future_used_in_model_input=false`、
`holdout_read=false`。全图共 163,100,310 bytes，跨 split 重复图 SHA 为 0，数据最晚时间为
`2026-05-02T23:45:00+00:00`，早于现有 holdout 边界。

## 数据统计

### 总体

| split | SIGNAL | NO_SIGNAL | 合计 |
|---|---:|---:|---:|
| train | 869 | 980 | 1,849 |
| val | 179 | 171 | 350 |
| test | 199 | 251 | 450 |
| **合计** | **1,247** | **1,402** | **2,649** |

最终 Gold 的迁移状态为：DIRECT 1,251、IGNORE 1,256、ONE_CLICK_REVIEW 139、
MANUAL_ADJUST 3。旧报告中的 DIRECT=0 来自迁移前快照，不是最终 Gold 事实。

### 398 个无偏唯一审核项

| split | 样本 | DIRECT | SIGNAL | NO_SIGNAL |
|---|---:|---:|---:|---:|
| train | 279 | 131 | 130 | 149 |
| val | 52 | 27 | 27 | 25 |
| test | 67 | 30 | 30 | 37 |
| **合计** | **398** | **188** | **187** | **211** |

按状态：DIRECT 188、IGNORE 189、ONE_CLICK_REVIEW 19、MANUAL_ADJUST 2。按来源：
`human_gold_owner_box` 165、`easy_negative_pool` 189、`owner_review_jsonl` 40、
`local_8768` 4。每个非空 `状态 × 类别 × 来源 × split` 小层至少抽 1 张；层内选择由
`sha256(seed|stratum|gold_id)` 决定，可重复生成。

## 与旧验收同表对照

| 项 | 旧 acceptance | 本轮重建 | 解释 |
|---|---:|---:|---|
| Gold 总数 | 2,649 | 2,649 | 图像与标签集合未改 |
| Gold SHA | `344212…` | `344212…` | 同一最终快照 |
| DIRECT 总体 | 0 | **1,251** | 旧验收错误使用迁移前 rows |
| DIRECT 抽检 | 0 | **计划 188；已完成 0** | 现在只按最终 Gold 中真实 DIRECT 计数 |
| 无偏总体审核 | 0 | **398** | 类别、来源、split、状态分层 |
| 盲重复 | 0 | **50（12.56%）** | 重复身份只在私有 truth 中 |
| Cleanlab 优先队列 | 28，容易被误当错误率证据 | **独立 28** | 模型筛选不能估总体错误率 |
| DIRECT 错误率 | 未知 | **仍未知** | 必须等待 Owner 逐条答案，不能手填 |
| training_eligible | false | **false** | 盲审、重复指标和 Owner 明确批准均未发生 |

## 零假设对照

本轮是非方向性的数据/标签审计，val AUC、top-decile 收益、胜率、单特征基线与匹配随机
交易对照在字面上均不适用；没有收益标签，也没有进行模型训练或方向性回测。

同等严格的零假设是：**随机盲审包不得因 Cleanlab 模型筛选而富集疑似错标。** 固定 398
个样本后，在每个 `状态 × 类别 × 来源 × split` 层内保持 Cleanlab 标记数不变，随机置换
2,000 次。主包实际与 Cleanlab 28 张重叠 **3 张**；零假设均值 3.951，范围 0–10，
双侧置换 `p=0.793103`。没有富集证据；这 3 张仍自然属于无偏样本，不能为了让结果好看而删掉。

为避免回忆污染，审核顺序已经冻结为：**先完成并导出 448 项主盲审，之后才打开 Cleanlab
优先队列。**

## 页面与盲化验证

主页面 public manifest 每项只有 `review_id` 和盲图路径；`gold_id`、原标签、来源、split、
迁移状态和重复对应关系只存在私有 truth。50 张重复使用独立 opaque ID 并混入随机顺序。
未来参考目录与主包物理分开；本轮没有生成未来图。

真实浏览器 QA 已验证：页面载入、图片显示、四类选择、SIGNAL 核心起点 1–6、前后翻页、
localStorage 持久保存与 JSON 下载均正常，重新打开无控制台错误。

## 门禁实现

新 acceptance 具备以下 fail-closed 约束：

1. `events` 与 `gold` 内容哈希必须相同，禁止迁移前后快照混用。
2. DIRECT 总体必须大于 0，0 不得通过比例门。
3. 逐条审核结果必须用 `gold_id` 回连最终 Gold；声称计入 DIRECT 的非 DIRECT 行使谱系门失败。
4. DIRECT 错误率由逐条 `review_label` 重新计算，忽略外部裸 `direct_error_rate`。
5. 主包必须全部答完；重复一致率、Cohen κ、重复 SIGNAL 核心边界精确一致率必须实际存在。
6. 即使所有数字通过，缺少带时间与对话引用的 Owner 明确批准，
   `training_eligible` 仍保持 false。

## 风险与诚实声明

- 目前只是“审核工具与样本冻结完成”，不是 P0/P1 已通过。错误率、一致率、κ 和核心边界
  一致率都还没有 Owner 答案。
- `UNCERTAIN` 预注册为错误，不从分母剔除，避免用不确定项降低错误率。
- 现有 ROADMAP 要求报告 κ/一致率，但没有冻结具体阈值；本轮不擅自发明通过线。最终仍需
  Owner 结合数字明确裁决。
- 这份 fixed-W10 数据即使通过标签审计，也只证明标签集合可用；没有完成 P2 的人工 onset
  可判定性与 Future Mutation Test，因此不得称为已验证的 causal-tip 模型数据。
- Cleanlab 队列与主包重叠 3 张。若提前打开 Cleanlab 队列，会污染这 3 张的无偏首次判断，
  所以必须遵守主包先行顺序。
- 本轮没有读取 2026-05-04 及之后 holdout；既有 fixed-W10 holdout 消耗记录没有被重用。

## 下一步与 Owner 决策

1. Owner 先打开主盲审页面，完成全部 448 项并导出 JSON。
2. 运行 `score`，得到总体/DIRECT 错误率、重复一致率、κ 和核心边界一致率；生成新的 scored
   acceptance。错误率超过 5% 则停止，不训练。
3. 主包答案冻结后，再打开独立 Cleanlab 28 张队列，作为优先修错清单，不回写无偏估计。
4. 若审核通过，生成**新版本**数据 manifest，不覆盖当前 2,649 张快照；最后由 Owner 明确
   决定是否把新版本 `training_eligible` 改为 true。

当前不需要也不允许启动 3060 训练。
