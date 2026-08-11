# P2 训练区间难负例候选 200 张 Owner 审核报告

## Technical Summary

已完成第三训练臂之前的**训练区间难负例候选审核集**，但尚未把任何样本写进训练集，也没有启动训练。

- 在冻结 train 末端之前选取 5 个互相分离的 12 小时时间块，覆盖 916 个 symbol-block、43,968 个 bar endpoint 和 351,744 个 W12–19 因果窗口。
- 当前 hard-negative R1 权重产生 20,711 条原始检测，按 5 bars 事件规则去重为 953 个事件。
- 先剔除 36 个碰到任何 Owner 框 ±12 bars 保护区的事件，得到 917 个安全候选。
- 每个时间块固定抽 40 个，共 200 个事件、123 个币；没有使用未来走势参与候选排序。
- 每个事件生成 3 张物理分离图片：原始因果输入、带橙框因果审核图、未来 48 根人工对照图；600/600 文件存在，200/200 对照均为完整 48 根。
- 当前 200 个事件全部为 `unreviewed`、`training_eligible=false`、`production_eligible=false`，没有标签目录。
- 未读取 holdout，未修改阈值、验证集、训练配方、ACTIVE 或生产配置。

Owner 审核入口：`analysis/html/p2_owner_short_train_hardneg_review200_20260811.html`。

## 五个训练块覆盖了不同触发密度

所有块的因果扫描结束时间和人工对照结束时间都早于冻结 train 末端 `2026-03-13 18:30 UTC`。

| 训练块 | 扫描结束 UTC | 币种数 | endpoints | W12–19 exposures | 原始命中 | 去重事件 | events / 1000 endpoints |
|---|---:|---:|---:|---:|---:|---:|---:|
| B01 | 2025-07-15 12:00 | 154 | 7,392 | 59,136 | 1,257 | 81 | 10.958 |
| B02 | 2025-09-15 12:00 | 160 | 7,680 | 61,440 | 7,919 | 277 | 36.068 |
| B03 | 2025-11-15 12:00 | 188 | 9,024 | 72,192 | 4,188 | 259 | 28.701 |
| B04 | 2026-01-15 12:00 | 204 | 9,792 | 78,336 | 6,129 | 274 | 27.982 |
| B05 | 2026-03-01 12:00 | 210 | 10,080 | 80,640 | 1,218 | 62 | 6.151 |
| **合计** | — | **916 symbol-block** | **43,968** | **351,744** | **20,711** | **953** | **21.675** |

五块的触发密度相差约 5.9 倍，说明模型错误不是单一时间块现象，也说明不能只拿原先 12 小时的 254 个 Owner 负例直接代替训练分布。固定每块 40 个是为了避免 B02/B04 的高密度期淹没低密度期，不代表每块真实错误率相等。

## 254 个已审误报只定义参考空间

此前 post-val、pre-holdout 的 331 个 Owner 裁决中有 254 个 `hard_negative`、66 个 `target`、11 个 `rebox`。本轮只把它们用作**形态距离参考**：

1. 对每个事件只读取 decision bar 及之前的 OHLC、SMA/EMA 20/60/120，以及模型当时预测的框几何。
2. 把不同长度的 W12–19 输入插值到 19 个时间点并标准化。
3. 计算到 254 个 Owner 负例和 77 个语义正例的 7 近邻距离，以“离负例近、离正例远”的 affinity 排序。
4. 排序完成后才加载未来 48 根，未来只渲染进右侧人工对照图。

200 张候选的 affinity p10 / median / p90 为 0.714 / 2.616 / 6.700。它只是“值得优先审核”的排序分，不是真值、置信度或收益分。

## 200 张审核集保持了位置与延迟分布

| 维度 | 分布 | 解读 |
|---|---|---|
| 输入窗 W | 12:44，13:21，14:13，15:28，16:3，17:18，18:32，19:41 | 未把输入长度固定成单一模板 |
| 预测核心 | 4:41，5:20，6:15，7:106，8:13，9:5 | 182/200（91%）落在 Owner 目标 4–7 根内；仍保留 18 个越界错误供审核 |
| 首次确认延迟 | 2:6，3:100，4:33，5:53，6:6，7:1，9:1 | 186/200（93%）落在 3–5 根；仍保留过早/过晚错误 |
| 未来人工对照 | 48:200 | 只用于 Owner 判断，不进入选择或训练输入 |

这一步没有把“核心 4–7 根、延迟 3–5 根”硬编码为候选准入门；越界候选仍能进入审核，因此 Owner 可以判断模型的几何偏差，而不是只看到被规则预先修饰过的样本。

## 质量门全部通过

| 检查项 | 结果 |
|---|---:|
| 200 个唯一事件，且每块恰好 40 个 | PASS |
| 200 个 decision time 全在冻结 train 内 | PASS |
| 200 个未来审核终点全在冻结 train 内 | PASS |
| 0 个候选碰到 Owner 框 ±12 bars | PASS |
| 选择过程 0 future bars | PASS |
| 600 张图片全部存在 | PASS |
| 200 张未来图全部包含 48 根 | PASS |
| 0 labels / 0 training-eligible / 0 production-eligible | PASS |
| holdout 读取 | **0** |

关键血缘：

- 权重 SHA256：`029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537`
- 331 Owner 裁决参考 SHA256：`2760d7744b92c5569068ba8824044f3b3a1c4e59d4a8e11c5f42dfc0cc1789d4`
- 安全候选池 SHA256：`d14372defa7377d03700b38f283e46398bc12b0f384740ba23e0e517656c0cb1`
- 200 张选择结果 SHA256：`8d123d806adbb0ab5ee8a932f4d853792cfc292ac5aa1cc7748476ef487759ab`
- 审核 manifest SHA256：`1929e9e1c542750a2cb7631ad088d3f87d98a1c33e95b14e724eb264a1bba406`
- HTML SHA256：`7bdc96f3d9fb0d72301e17afff03119c6537867877acd8851dbee89c08e110a8`

## 与上一阶段同表对照

| 阶段 | 时间区间 | 扫描事件 | Owner 已审 | 语义正例 | 框偏 | 明确负例 | 可回流训练 |
|---|---|---:|---:|---:|---:|---:|---:|
| post-val canary V3 | 2026-05-03 00:00–12:00 UTC | 331 | 331 | 66 | 11 | 254 | 0；晚于冻结 val，只作参考 |
| 本轮 train review200 | 5 个冻结 train 块 | 953 | 0 | 待审 | 待审 | 待审 | 0；Owner 确认后才决定 |

本轮解决的是“从哪里找时间合法的同类错误”，不是已经解决模型 precision。只有 Owner 审完，才能知道 200 张里有多少是真 hard negative。

## 必报模型与交易指标状态

本轮没有训练新模型，也没有收益标签，因此下列指标均**不适用**，不能拿旧模型数值冒充本轮结果：

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| val AUC | N/A | YOLO 候选审核集，不是 L2 排序模型评估 |
| 置换检验 p | N/A | 没有收益排序实验 |
| top-decile 毛/净收益 | N/A | 没有读取未来收益标签 |
| 胜率 | N/A | 没有交易回测 |
| 单特征基线 | N/A | 本轮只构建人工审核池 |
| 匹配随机对照组 | N/A | 本轮不作方向性收益结论 |

## 风险与诚实声明

- affinity 使用了 331 事件的 Owner 裁决来设计候选排序，因此这 331 事件以后不能再充当第三臂的独立最终验证集。
- 200 张只是校准规模，未必能提供第三臂所需的全部 hard negative。若 Owner 确认的真负例不足，需要继续扫更多未使用的冻结 train 时间块，不能用未审候选凑数。
- 当前已有的 1,370 个模型排序背景负例没有被自动宣布为真负例；本轮也没有覆盖或删除它们。
- 本页不能证明 recall、event precision 或交易收益改善。任何训练后的结论仍需独立时间块验证，并遵守 holdout 授权纪律。
- 本轮没有改 conf=0.25、NMS IoU=0.7、W12–19、5-bars 去重规则，符合单变量纪律。

## 下一步

Owner 在 HTML 里逐张按：`1=形态和框都对`、`2=形态像但框偏`、`3=不是目标形态`，完成后导出 JSON。

导入裁决后只做两件事：

1. 统计时间合法的真 hard negative 数量、币种覆盖和 W 桶覆盖；不足则追加新的冻结 train 时间块。
2. 若数量和覆盖足够，构建第三训练臂，保持 1,143 正例、1,143 easy negative、冻结 val 和全部训练配方不变，只替换/补充 hard-negative 部分。

**训练仍需 Owner 另行明确授权。** 本报告完成不等于授权启动 3060、promote 或部署。

## 复现命令

以下命令只使用冻结 train 范围；`historical` 的 420 bars 用于因果扫描，468 bars 用于额外 48 根人工审核对照。

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

blocks=(B01_20250715 B02_20250915 B03_20251115 B04_20260115 B05_20260301)
scan_ends=(2025-07-15T12:00:00Z 2025-09-15T12:00:00Z 2025-11-15T12:00:00Z 2026-01-15T12:00:00Z 2026-03-01T12:00:00Z)
audit_ends=(2025-07-16T00:00:00Z 2025-09-16T00:00:00Z 2025-11-16T00:00:00Z 2026-01-16T00:00:00Z 2026-03-02T00:00:00Z)

for i in 1 2 3 4 5; do
  block=${blocks[$i]}
  scan_end=${scan_ends[$i]}
  audit_end=${audit_ends[$i]}

  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py historical \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v1/$block/scan_snapshot \
    --end $scan_end --context-bars 420 --evaluation-scope train_hardneg_mining
  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py historical \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v1/$block/audit_snapshot \
    --end $audit_end --context-bars 468 --evaluation-scope train_hardneg_mining

  # 可把 mps 换成 CUDA device 0 或 cpu；输出统计和事件应相同。
  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py scan \
    --snapshot-dir analysis/output/owner_short_train_hardneg_blocks_v1/$block/scan_snapshot/kline_snapshot \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v1/$block/merged \
    --weights analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r1_ft/weights/best.pt \
    --hours 12 --window-min 12 --window-max 19 --conf 0.25 --iou 0.7 \
    --imgsz 960 --device mps --batch 32 --evaluation-scope train_hardneg_mining
done

# 构建候选、三联图、审核页和机器审计摘要：
.venv/bin/python scripts/build_owner_short_train_hardneg_review.py

# 验证：
.venv/bin/python -m pytest tests/test_build_owner_short_train_hardneg_review.py -q
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_train_hardneg_review200_20260811.md \
  --out-dir analysis/html
```

## 产物

- Owner 审核页：`analysis/html/p2_owner_short_train_hardneg_review200_20260811.html`
- 报告 HTML：`analysis/html/p2_owner_short_train_hardneg_review200_report_20260811.html`
- 机器摘要：`analysis/output/owner_short_train_hardneg_review200_v1/summary.json`
- 安全候选池：`analysis/output/owner_short_train_hardneg_review200_v1/candidate_pool.jsonl`
- 200 张选择记录：`analysis/output/owner_short_train_hardneg_review200_v1/selected_candidates.jsonl`
- 审核 manifest：`analysis/output/owner_short_train_hardneg_review200_v1/review_manifest.jsonl`

## Further Novel Deductions

- 如果 Owner 在 200 张中仍判出约 75% 的明确负例，预计可一次获得约 150 个高价值、时间合法的错误种子；这足够校准下一轮自动挖掘，但仍不足以不经审计直接替代 2,286 个 hard-negative 槽位。
- 五块触发密度波动很大，第三臂应控制时间块权重；否则高密度阶段会成为隐性时间特征，训练后的“降触发”可能只对特定行情有效。
- 4–7 根核心和 3–5 根延迟在候选中已经占大多数，但 precision 仍低，说明下一阶段的主要矛盾不是框长或延迟，而是“均线密集平台是否真的具备启动语义”的区分能力。

## Open Questions

- Owner 审完后，`2=框偏` 应作为几何回归样本重新框选，还是先排除，只用 `3=不对` 做第三臂负例？
- 第三臂的硬负例目标量是否保持 2,286 不变，还是先做较小的单变量试验？两者都需要 Owner 在看完确认数量后裁决。
