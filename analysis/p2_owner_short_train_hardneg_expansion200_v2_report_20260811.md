# P2 第三训练臂难负例扩充 200 张报告

## Technical Summary

Owner 已完成正例检索 100 张：45 对、0 框偏、55 不对。该结果把 train-time 主动学习参考累计到 **63 个正例 + 237 个难负例**，但 237 只相当于第二臂 2,286 个 hard-negative 槽位的 10.37%，第三臂仍不能直接开训。

- 正例检索页 target 占比 45%，是前一张负例偏置页 9% 的 5 倍，证明相反选样目标确实能富集不同样本。
- 两个比例都来自主动学习偏置样本，不是模型总体 precision，也没有 recall 分母。
- 已使用累计 1,283 个正例参考和 491 个确认误报参考，重新评分原池中剩余的 617 个未审事件。
- 新建难负例扩充页 200 张，覆盖 126 个币；与此前 300 个 Owner 已审事件零重复。
- 新页明确以“多数按 3=不对”为目标；3 是有效难负例，不代表审核或页面失败。
- 选择只读取 decision bar 及之前的 OHLC、六条均线和预测框；未来 48 根只在选择结束后渲染。0 holdout、0 labels、0 training-eligible、0生产变更。

Owner 审核入口：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_20260811.html`。

## 正例检索把 target 富集提高了 5 倍

| 审核页 | 选样目标 | Owner target | Owner hard negative | target 占比 | 可当总体 precision？ |
|---|---|---:|---:|---:|---|
| train review200 V1 | 靠近确认误报 | 18 | 182 | 9% | 否；负例偏置 |
| positive retrieval100 | 靠近确认正例 | 45 | 55 | 45% | 否；正例偏置 |
| **累计 train-time 已审** | 两种互补主动学习 | **63** | **237** | **21%** | 否；非随机样本 |

45% 对 9% 的差异说明形态距离排序具有富集能力，但不能解释为模型提升：两页使用的是同一个第二臂权重 `029f80a5…f537`，变化来自选样目标，不是模型参数变化。

## 时间块差异说明不能只看总命中率

| 时间块 | 正例检索审核数 | target | hard negative | target 占比 |
|---|---:|---:|---:|---:|
| B01 2025-07-15 | 20 | 5 | 15 | 25.0% |
| B02 2025-09-15 | 20 | 17 | 3 | 85.0% |
| B03 2025-11-15 | 20 | 3 | 17 | 15.0% |
| B04 2026-01-15 | 21 | 9 | 12 | 42.9% |
| B05 2026-03-01 | 19 | 11 | 8 | 57.9% |
| **合计** | **100** | **45** | **55** | **45.0%** |

B02 与 B03 相差 70 个百分点。当前模型在不同训练行情块的可检索正例密度明显不同，因此第三臂 hard-negative 不能只从单一高误报块堆积，也不能用总体 45% 掩盖分块失败。

## 第二臂负样本不少，缺的是确认质量与覆盖

| 口径 | hard negative 数量 | 是否能直接进第三臂 |
|---|---:|---|
| 第二臂训练 hard-negative | 2,286 | 已用于第二臂；其中部分是模型排序背景，不等于新 Owner 真值 |
| post-val Owner 确认误报 | 254 | 否；晚于冻结 val，只作参考 |
| 第一张 train 负例页确认误报 | 182 | 需经第三臂 dataset builder 审计 |
| 正例检索页顺带确认误报 | 55 | 需经第三臂 dataset builder 审计 |
| **累计新 train Owner 确认误报** | **237** | **仍不足；占2,286槽位10.37%** |

当前工作不是重新补 easy negative，而是把模型实际会误触发的 train-time 事件变成 Owner 确认 hard-negative，并控制时间块、币种、W 长度和重复关系。

## 新200张使用累计63正/237负重新排序

参考空间：

| 参考来源 | 正例 | 负例 | 用途 |
|---|---:|---:|---|
| 冻结 train Owner 金标 | 1,143 | 0 | 正例语义主体 |
| post-val Owner 审核 | 77 | 254 | 形态参考；不回流训练 |
| train 负例页审核 | 18 | 182 | 主动学习参考 |
| train 正例检索页审核 | 45 | 55 | 主动学习参考 |
| **合计** | **1,283** | **491** | 因果距离排序 |

在原 917 个安全候选中排除已审 300 个，剩余 617 个。B05 的 59 个安全事件已在前两页全部审核，因此新页不伪造 B05 候选；配额为：

| 时间块 | 剩余未审 | 新页数量 |
|---|---:|---:|
| B01 | 21 | 21 |
| B02 | 188 | 60 |
| B03 | 199 | 60 |
| B04 | 209 | 59 |
| B05 | 0 | 0 |
| **合计** | **617** | **200** |

选择时先提高币种多样性，再按难负例 affinity 补齐配额。新 200 张覆盖 126 个币；affinity p10 / median / p90 = -0.713 / -0.018 / 1.322。分数只是审核优先级，不是标签。

## 数据、图像与浏览器门全部通过

| 质量门 | 结果 |
|---|---:|
| 200 个唯一事件 | PASS |
| 与前两页 300 个事件零重复 | PASS |
| 固定时间块配额 21/60/60/59 | PASS |
| decision 与未来审核终点全在冻结 train 内 | PASS |
| 0 候选碰 Owner 框 ±12 bars | PASS |
| 选样过程 0 future bars | PASS |
| 600/600 图片存在 | PASS |
| 200/200 未来图完整 48 根 | PASS |
| 0 labels / 0 training-eligible / 0 production-eligible | PASS |
| holdout 读取 | **0** |
| 浏览器控制台 | **0 errors / 0 warnings** |
| 快捷键 `3` 与 `Z` 撤销 | PASS |

关键血缘：

- 权重 SHA256：`029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537`
- 正例检索100标注 manifest：`8dd060b3ba56ecebf5af30360e875af51328874d2dcc85326009cc2d6062ea3b`
- 剩余617评分池：`bbc96a9deb00a5b4e4e2ae000e707950d657d76956018219292f82e5a31deeb2`
- 新200选择结果：`168751ea1e10622dc9336eb9cadb39d0b23360bd9c0c27515c047211a35e090a`
- 新审核 manifest：`8630381d2d4370b58fd49140bacaf90105b58763d0c1493afb90a066f6b9eeef`
- 新 HTML：`e2a6bcf788ea4734a1bff3607de7594de7d10c5092582c2b19bf4061e3371c2c`

## 必报模型与交易指标状态

本轮没有训练新模型，也没有收益回测，因此以下指标均不适用。

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| val AUC | N/A | YOLO 主动学习选样，不是 L2 排序模型 |
| 置换检验 p | N/A | 无收益排序实验 |
| top-decile 毛/净收益 | N/A | 无收益标签 |
| 胜率 | N/A | 无交易回测 |
| 单特征基线 | N/A | 只比较正负形态距离 |
| 匹配随机对照组 | N/A | 不作方向性收益结论 |

## 限制、稳健性与不能下的结论

- 45% 与 9% 只证明选样富集方向不同，不能证明模型总体 precision 是45%，也不能证明模型已经改善。
- 当前页面只来自模型已经触发的事件，无法发现完全漏检的目标形态，因此没有 recall 分母。
- 237 个 train Owner hard-negative 仍不足。即使新200全部判为误报，累计也只有437个，仍需更多未使用 train 时间块。
- 当前617池来自原五个12小时块；完成后必须转向新的冻结 train 时间块，避免在同一小范围反复主动学习。
- 第二臂权重继续禁止 promote，未修改 ACTIVE、阈值、训练配方或生产配置。

## 下一步

Owner 审核新页：目标形态按 `1`，框偏按 `2`，不是目标按 `3`。本页本来就是难负例扩充页，因此多数按3是预期结果。

审核完成后：

1. 冻结新裁决并计算累计 hard-negative 数量、币种、时间块、W桶和重复覆盖。
2. 原617事件尚未审核的417个不再无限循环；开始扫描新的、未使用冻结 train 时间块。
3. 当确认负例覆盖足够后，构建第三臂候选数据集；保持正例、easy negative、冻结 val 和训练配方不变，单变量改变 hard-negative 组成。
4. 数据集审计完成后再请求 Owner 逐次授权3060训练。

## Further Questions

- 新200张能新增多少真正的 Owner hard-negative，是否继续保持约90%的负例富集率？
- 累计确认负例达到多少才足以替代第二臂中未审的模型排序背景，需要结合W桶与币种覆盖而不是只定绝对数量。
- 第三臂是否完全替换2,286 hard槽位，还是先做固定总量的小规模单变量替换比例实验，需在完整审计后由Owner决定。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

.venv/bin/python scripts/ingest_owner_short_train_hardneg_review.py \
  --review-json /path/to/positive-retrieval100-owner-export.json \
  --manifest analysis/output/owner_short_train_positive_retrieval100_v1/review_manifest.jsonl \
  --build-summary analysis/output/owner_short_train_positive_retrieval100_v1/summary.json \
  --out analysis/output/owner_short_train_positive_retrieval100_v1 \
  --affinity-field positive_affinity --selection-goal positive_retrieval \
  --expected-total 100

.venv/bin/python scripts/build_owner_short_train_hardneg_expansion_review.py

.venv/bin/python -m pytest \
  tests/test_ingest_owner_short_train_hardneg_review.py \
  tests/test_build_owner_short_train_hardneg_expansion_review.py -q

python3 scripts/md_to_html.py \
  analysis/p2_owner_short_train_hardneg_expansion200_v2_report_20260811.md \
  --out-dir analysis/html
```

## 产物

- 新难负例审核页：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_20260811.html`
- 报告 HTML：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_report_20260811.html`
- 正例检索 Owner 裁决：`analysis/output/owner_short_train_positive_retrieval100_v1/owner_review_decisions.json`
- 正例检索标注 manifest：`analysis/output/owner_short_train_positive_retrieval100_v1/owner_review_labeled_manifest.jsonl`
- 新617评分池：`analysis/output/owner_short_train_hardneg_expansion200_v2/hard_negative_scored_pool.jsonl`
- 新200选择记录：`analysis/output/owner_short_train_hardneg_expansion200_v2/selected_candidates.jsonl`
- 新审核 manifest：`analysis/output/owner_short_train_hardneg_expansion200_v2/review_manifest.jsonl`
