# P2 第三训练臂前置：训练区间正例检索 100 张报告

## Technical Summary

当前路线**严格延续重构文档的 1:1 baseline → 1:3 hard-negative 第二臂 → 第三臂前置审核**，没有跳过 hard-negative 步骤，也没有拿 182 张直接训练。

- 第一臂是 1:1 easy-negative baseline：1,143 个 train 正例 + 1,143 个 easy negative，best SHA `da278820…fc65b4`。
- 第二臂按文档扩成 1:3：1,143 正例 + 1,143 easy negative + 2,286 hard negative，hard 占全部负例 2/3，best SHA `029f80a5…f537`。连续行情仍过度触发，所以禁止 promote。
- post-val 的 331 张审核得到 254 个明确误报，但它们晚于冻结 val，只能作错误参考，不能直接回流训练。
- train-time 的 200 张负例偏置审核已由 Owner 完成：18 对、0 框偏、182 不对。182 个确认误报位于冻结 train 内，但仍只冻结为主动学习种子，不自动写进训练集。
- 为回答“模型还能否找到接近早上 ETH 形态的触发”，现已从剩余 717 个未审 train 事件中另建**正例检索页**：使用 1,238 个正例参考、436 个确认误报参考，选出 100 个全新候选、75 个币。
- 选择过程只读取 decision bar 及之前的 K 线、均线和框几何；未来 48 根只在选样完成后单独渲染。0 holdout、0 labels、0 training-eligible。

Owner 审核入口：`analysis/html/p2_owner_short_train_positive_retrieval100_20260811.html`。

## 文档规定的两条训练臂均已完成

| 阶段 | train 正例 | easy negative | hard negative | 总负例:正例 | 当前结论 |
|---|---:|---:|---:|---:|---|
| 第一臂 1:1 baseline | 1,143 | 1,143 | 0 | 1:1 | 已完成；只证明基础可学，不证明连续行情 precision |
| 第二臂 1:3 hard-negative | 1,143 | 1,143 | 2,286 | 3:1 | 已完成；触发下降但密度仍失败，禁止 promote |
| 第三臂前置 | 尚未改 | 尚未改 | 182 个新 Owner 确认种子 | 尚未建集 | 正在扩充并区分正例检索与负例挖掘 |

第二臂的负样本数量并不少。当前不足的是**Owner 确认、时间合法、能代表第二臂真实误报的新 hard negative**：182 个只占第二臂 2,286 个 hard 槽位的 7.96%。因此第三臂不能立即开训，也不能简单复制 182 张凑数量。

## 负例页的 182 个错误是有效产物，不是正例检索结果

原 review200 的排序目标是“靠近已确认误报、远离已确认正例”，因此 Owner 判出 182/200 为不对，符合难负例挖掘目的。该 91% 是**负例偏置审核集的命中率**，不是模型全分布 precision。

| Owner 裁决 | 事件 | 币种 | 在负例偏置页占比 | peak conf median |
|---|---:|---:|---:|---:|
| 对 | 18 | 17 | 9.0% | 0.623 |
| 框偏 | 0 | 0 | 0.0% | N/A |
| 不对 | 182 | 120 | 91.0% | 0.458 |

182 个 train-time 误报已通过协议、源 SHA、200 个 ID 一一对应、无 pending、无 Owner 框重叠、无前视等质量门。它们仍是 `training_eligible=false`，需后续数据集构建器完成去重、W 桶匹配和数量扩充。

## 新页面把选样方向改为接近正例

正例检索的参考空间由五部分组成：

| 参考类型 | 数量 | 时间/用途 |
|---|---:|---|
| 冻结 train Owner 金标正例 | 1,143 | 主要正例语义支撑 |
| post-val Owner 认可 target + rebox | 77 | 只作形态参考，不回流训练 |
| train review200 新确认 target | 18 | 主动学习正例种子 |
| post-val Owner 确认误报 | 254 | 负例参考，不回流训练 |
| train review200 新确认误报 | 182 | 主动学习负例种子 |
| **合计** | **1,238 正 / 436 负** | 只用于因果距离排序 |

每个候选用可见窗口中的 OHLC、SMA/EMA 20/60/120 和预测框几何表示。排序分改为“到确认误报的距离 − 到确认正例的距离”；分数越高，仅表示越值得优先做正例审核，不表示已经是真正正例。

没有直接使用 2026-08-10 ETH holdout 行情作为训练或选样特征。早上 ETH 图继续定义语义方向；实际距离参考来自冻结 train 金标和已获授权的审核结果。

## 100 张覆盖五个时间块且全部未审

| 时间块 | 安全候选池 | 已在负例页审核 | 本轮正例检索 | 备注 |
|---|---:|---:|---:|---|
| B01 2025-07-15 | 81 | 40 | 20 | 剩余 41 |
| B02 2025-09-15 | 248 | 40 | 20 | 剩余 208 |
| B03 2025-11-15 | 259 | 40 | 20 | 剩余 219；上一页 40/40 均为误报 |
| B04 2026-01-15 | 270 | 40 | 21 | 剩余 230 |
| B05 2026-03-01 | 59 | 40 | 19 | 剩余全部纳入；事件集中于少数币种 |
| **合计** | **917** | **200** | **100** | **与上一页 0 重复** |

100 张覆盖 75 个币。B05 只剩 19 个未审事件，因此使用 19 张；多出的 1 张分配给剩余池最大的 B04。选样先保证一币一张，B05 因候选集中，在不足时允许同币多事件补齐，但事件 ID 仍全部唯一。

候选自身的几何分布为：

- 输入 W12–19：12/13/14/15/16/17/18/19 = 39/22/4/16/1/5/3/10。
- 预测核心 4/5/6/7/8 根 = 39/16/10/31/4；96% 落在 4–7 根。
- 确认延迟 2/3/4/5/6 根 = 6/77/4/12/1；93% 落在 3–5 根。
- 正例 affinity p10 / median / p90 = 0.969 / 2.601 / 3.552。

这些比例只说明候选符合现有几何分布，不能代替 Owner 对“均线密集平台是否真正成立”的判断。

## 全部数据与浏览器质量门通过

| 质量门 | 结果 |
|---|---:|
| 100 个唯一事件 | PASS |
| 与已审 200 事件零重复 | PASS |
| 固定时间块配额 20/20/20/21/19 | PASS |
| 100 个 decision 与未来对照终点均在冻结 train 内 | PASS |
| 0 个候选碰 Owner 框 ±12 bars | PASS |
| 选样过程 0 future bars | PASS |
| 300/300 张图片存在 | PASS |
| 100/100 张未来图完整包含 48 根 | PASS |
| 0 labels / 0 training-eligible / 0 production-eligible | PASS |
| holdout 读取 | **0** |
| 浏览器控制台 | **0 errors / 0 warnings** |
| 快捷键 `1` 和 `Z` 撤销 | PASS |

关键血缘：

- 第二臂模型 SHA256：`029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537`
- 原 917 候选池 SHA256：`d14372defa7377d03700b38f283e46398bc12b0f384740ba23e0e517656c0cb1`
- train review200 标注 manifest SHA256：`ce41d686ae7ccab9551ff825ba57ffe98679f47ab2c4a2c79e58a1f363372e27`
- 1,143 train 正例 manifest SHA256：`8f4119fbf634ec976077e8eb50b36e57ae3aa0471759cad04f2eaeaeacd6d21b`
- 新 717 评分池 SHA256：`3e0a56664538b6b6d1001b2ef63be7a5e0bd7083ffa2cff374504fc0737ad913`
- 新 100 选择结果 SHA256：`31da7fd4c9150fc7c9450cef47d67ed33f7759d5dd630085af422dba2c155eb8`
- 新审核 manifest SHA256：`716d880558f38e2b73a36d9d942da4ad1eb49f2195d19723d40331f20ea2026f`
- 新 HTML SHA256：`a55beb7aed91035cad99236602b76aae4c0f1b2c5b9041a1dc7c9643bcb182c3`

## 必报模型与交易指标状态

本轮没有训练新模型，也没有作收益回测。下列指标均不适用，不能用第二臂旧数值冒充本轮结果。

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| val AUC | N/A | YOLO 主动学习选样，不是 L2 排序模型 |
| 置换检验 p | N/A | 没有收益排序实验 |
| top-decile 毛/净收益 | N/A | 没有读取未来收益标签 |
| 胜率 | N/A | 没有交易回测 |
| 单特征基线 | N/A | 本轮只比较正/负形态距离 |
| 匹配随机对照组 | N/A | 本轮不作方向性收益结论 |

## 限制与风险

- 正例检索页是主动学习页，不是独立验证集。它使用了 18 个同时间块 Owner 正例来排序剩余事件，审核后的命中率不能当模型总体 precision。
- 1,143 个 train Owner 金标提供的是目标分布，但模型是否能在连续行情完整召回仍没有可信分母；本页只审核已经触发的事件，不能发现模型完全漏掉的正例。
- 182 个确认误报不足以直接替换 2,286 个 hard-negative 槽位。后续扩挖必须控制币种、时间块、W 长度和重复事件，不能复制同类图凑数。
- B05 剩余 19 个候选集中于少数币种；页面保留该事实，不伪造第 20 个事件。
- 当前第二臂权重继续 `production_eligible=false / auto_promote=false`，没有修改 ACTIVE 或部署。

## 下一步由这 100 张裁决决定

Owner 在新页按 `1=对 / 2=框偏 / 3=不对`，完成后复制 JSON。

随后：

1. 把本页确认的正例加入主动学习参考，不直接作为训练金标；把新增误报加入 train-time hard-negative 种子。
2. 用累计确认误报重新评分剩余 617 个事件，并追加未使用的冻结 train 时间块，直到 hard-negative 数量和时间/币种/W 桶覆盖足够。
3. 只有完成去重、1:2/1:3 配额设计、冻结 val 不变和单变量审计后，才提出第三臂训练方案。
4. 启动 3060 训练仍需 Owner 另行明确授权。

## Further Questions

- 这 100 张中有多少真正达到早上 ETH 的平台语义，而不是仅有“随后下跌”？
- `2=框偏` 若出现，第三臂先排除，还是由 Owner 再定边界后作为正例？
- 当确认 hard negative 达到什么数量和覆盖度时才足够开第三臂，需要在本轮审核后按实际分布裁决。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

.venv/bin/python scripts/ingest_owner_short_train_hardneg_review.py \
  --review-json /path/to/owner-review200-export.json

.venv/bin/python scripts/build_owner_short_train_positive_retrieval_review.py

.venv/bin/python -m pytest \
  tests/test_ingest_owner_short_train_hardneg_review.py \
  tests/test_build_owner_short_train_positive_retrieval_review.py -q

python3 scripts/md_to_html.py \
  analysis/p2_owner_short_train_positive_retrieval100_report_20260811.md \
  --out-dir analysis/html
```

## 产物

- 正例检索审核页：`analysis/html/p2_owner_short_train_positive_retrieval100_20260811.html`
- 报告 HTML：`analysis/html/p2_owner_short_train_positive_retrieval100_report_20260811.html`
- 负例页 Owner 冻结裁决：`analysis/output/owner_short_train_hardneg_review200_v1/owner_review_decisions.json`
- 负例页标注 manifest：`analysis/output/owner_short_train_hardneg_review200_v1/owner_review_labeled_manifest.jsonl`
- 正例检索摘要：`analysis/output/owner_short_train_positive_retrieval100_v1/summary.json`
- 新 100 选择记录：`analysis/output/owner_short_train_positive_retrieval100_v1/selected_candidates.jsonl`
- 新审核 manifest：`analysis/output/owner_short_train_positive_retrieval100_v1/review_manifest.jsonl`
