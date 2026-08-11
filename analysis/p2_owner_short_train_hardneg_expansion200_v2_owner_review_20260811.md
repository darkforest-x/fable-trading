# P2 难负例扩充 V2 Owner 裁决报告

## Technical Summary

Owner 已完成第二张 train-time 难负例扩充页 200/200 裁决：**25 个目标形态、0 个框偏、175 个难负例、0 pending**。协议、源 SHA、200 个唯一 ID、声明计数和 manifest 一一联结全部通过。

- 本页难负例有效率为 **87.5%**；这是主动学习选样效率，不是模型总体误报率或 event precision。
- 三张 train-time 审核页累计得到 **88 个目标参考 + 412 个 Owner 确认难负例**。
- 412 个难负例只相当于第二训练臂 2,286 个 hard-negative 槽位的 **18.02%**，不能复制凑数，也不足以直接宣布第三臂数据完整。
- 原五个 12 小时块已经被多轮主动学习使用；下一轮转向五个未使用的冻结 train 时间块，避免在同一小范围循环挖掘。
- 本轮 0 holdout、0 新标签、0 自动 training-eligible；未训练、未 promote、未修改 ACTIVE 或生产配置。

## Owner 裁决与分块结果

| 时间块 | 审核数 | target | hard negative | hard-negative 占比 |
|---|---:|---:|---:|---:|
| B01 2025-07-15 | 21 | 0 | 21 | 100.00% |
| B02 2025-09-15 | 60 | 11 | 49 | 81.67% |
| B03 2025-11-15 | 60 | 2 | 58 | 96.67% |
| B04 2026-01-15 | 59 | 12 | 47 | 79.66% |
| **合计** | **200** | **25** | **175** | **87.50%** |

B01 与 B03 几乎完全是误报，B02 与 B04 仍保留 18%–20% 的目标形态。分块差异再次说明不能只用总比例描述模型，也不能把某一个行情块当成完整训练分布。

## 累计 train-time Owner 参考

| 审核页 | 选样目的 | target | hard negative | hard-negative 占比 |
|---|---|---:|---:|---:|
| train review200 V1 | 靠近已确认误报 | 18 | 182 | 91.00% |
| positive retrieval100 | 靠近确认正例 | 45 | 55 | 55.00% |
| hardneg expansion200 V2 | 累计参考扩挖误报 | 25 | 175 | 87.50% |
| **累计** | 互补主动学习 | **88** | **412** | **82.40%** |

这三个比例都来自刻意偏置的主动学习页面，不能相互平均后冒充线上 precision。它们只回答“审核预算有没有集中到有用样本”：V1 和 V2 的负例富集方向有效，positive retrieval 的正例富集方向也有效。

## 置信度不能代替 Owner 语义裁决

| Owner 类别 | peak conf median | p90 | mean |
|---|---:|---:|---:|
| target | 0.6803 | 0.8564 | 0.6726 |
| hard negative | 0.5209 | 0.8062 | 0.5409 |

目标形态的平均置信度更高，但两类在高置信区仍明显重叠：hard negative 的 p90 仍有 0.8062。当前证据不支持靠提高 conf 阈值替代 hard-negative 重训；之前连续行情测试也已经证明单纯抬阈值会伤 recall。

## 血缘与质量门

| 项目 | 结果 |
|---|---|
| 审核协议 | `owner_short_train_hardneg_expansion200_v2_20260811` |
| 选择源 SHA256 | `168751ea1e10622dc9336eb9cadb39d0b23360bd9c0c27515c047211a35e090a` |
| Owner 输入 SHA256 | `2820513e40508c1464d779bee342aa277680cde538fd8144369466df2ecc2cfa` |
| 裁决文件 SHA256 | `e273d9ce72ca02d458ef4ca0a6bb723c1727edc10f72093e79565f041b45c205` |
| 标注 manifest SHA256 | `bcd644f90f82d3950ea3885384ba2bbb763de600a2a8272abd2fa4c5e8427763` |
| 摘要 SHA256 | `6bff261c5bd5c797ea4df7ff38828f684bbcaa46abd72c5e7a97fd912cf41b93` |
| 200 ID 唯一且一一联结 | PASS |
| 声明计数可重算 | PASS |
| 全部在冻结 train 时间内 | PASS |
| 0 Owner 框保护区重叠 | PASS |
| 选样无未来 K | PASS |
| 自动进入训练 | **0** |
| holdout 读取 | **0** |

## 必报模型与交易指标状态

本轮只是人工裁决入账，没有训练新模型或收益回测。

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| val AUC | N/A | YOLO 主动学习审核，不是 L2 排序实验 |
| 置换检验 p | N/A | 无收益排序 |
| top-decile 毛/净收益 | N/A | 无交易标签 |
| 胜率 | N/A | 无交易回测 |
| 单特征基线 | N/A | 本轮只冻结 Owner 语义 |
| 匹配随机对照组 | N/A | 不作方向性收益结论 |

## 风险与诚实声明

- 87.5% 只证明 V2 选样器善于把审核预算集中到误报，不能证明模型全市场误报率为 87.5%。
- 当前页面只包含模型已经触发的事件，没有漏检分母，因此不能报告 recall。
- 412 个确认难负例覆盖 202 个以上币种/事件来源，但绝对数量仍只占既有 2,286 hard 槽位 18.02%；正式 dataset builder 还必须做联合 SHA 去重、时间块/W桶/币种覆盖和 split 审计。
- post-val 的 254 个确认误报继续只作形态参考，不回流 train；第三臂只能使用冻结 train 来源。
- 第二臂权重继续禁止 promote。新的训练、阈值变化、holdout 或生产切换仍需 Owner 逐次明确授权。

## 下一步

1. 扫描五个新的冻结 train 12 小时时间块：2025-06、08、10、12 和 2026-02；每个块的检测输入只到 decision，审核图另带未来 48 根。
2. 使用累计 1,308 个正例参考与 666 个负例参考对新事件排序，固定每块 40 张生成下一页 200 张审核。
3. Owner 完成新页后再统计累计唯一 hard-negative 数量及 W/币种/时间覆盖；覆盖足够才构建第三训练臂。
4. 数据集审计通过后再单独请求训练授权，不因本报告自动开训。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

.venv/bin/python scripts/ingest_owner_short_train_hardneg_review.py \
  --review-json /path/to/owner-export.json \
  --manifest analysis/output/owner_short_train_hardneg_expansion200_v2/review_manifest.jsonl \
  --build-summary analysis/output/owner_short_train_hardneg_expansion200_v2/summary.json \
  --out analysis/output/owner_short_train_hardneg_expansion200_v2 \
  --affinity-field hard_negative_affinity_v2 \
  --selection-goal hard_negative_expansion \
  --expected-total 200

.venv/bin/python -m pytest tests -q

python3 scripts/md_to_html.py \
  analysis/p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.md \
  --out-dir analysis/html
```

## 产物

- Owner 裁决：`analysis/output/owner_short_train_hardneg_expansion200_v2/owner_review_decisions.json`
- Owner 标注 manifest：`analysis/output/owner_short_train_hardneg_expansion200_v2/owner_review_labeled_manifest.jsonl`
- 机器摘要：`analysis/output/owner_short_train_hardneg_expansion200_v2/owner_review_summary.json`
- 本报告 HTML：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.html`
