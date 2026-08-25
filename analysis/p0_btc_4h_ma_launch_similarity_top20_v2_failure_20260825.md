# BTC 4h 相似形态 Top-20 扩展失败报告

## 结论先说

本轮没有新的 4h 图可以诚实交付。获得 Owner 明确授权后，冻结的 Top-20 配置完整读取了 **54 个币、36,720 个 holdout 4h 币种行**；宽门仍有 **64 LONG / 30 SHORT**，但固定的 18 根同币同方向去重后，SHORT 最终只有 **15 个独立候选**。预注册要求每边恰好 20 个，因此程序以 `ValueError: SHORT has only 15 deduplicated candidates` 失败关闭，未生成候选清单、K 线图、总览图或新零假设结果。

这次失败仍然是本配置的 **第 2 次 holdout 消耗**。不能在看到结果后缩短去重间隔、改成不对称 Top-N 或把相邻锚点凑成不同案例。若继续，最小可行的新方案是对称 **Top-15**；它仍需先冻结为新配置、获得 Owner 对 **第 3 次 holdout 读取**的明确授权，然后才可重跑并交付原榜之后的 LONG/SHORT 第 9–15 名，共最多 14 张新图。

## 运行回执

| 项目 | 结果 |
|---|---:|
| 配置 | `exp-btc-4h-ma-launch-similarity-top20-v2` |
| 唯一改动 | 每边 Top-8 → Top-20 |
| 币种 | 54 |
| 时间范围 | 2024-08-25 04:00 UTC 至 2026-08-25 04:00 UTC |
| 宽门 LONG / SHORT | 64 / 30 |
| 去重后 LONG 容量 | 至少 20 |
| 去重后 SHORT 容量 | 15 |
| 请求数量 | 20 / 边 |
| 新候选 / 新渲染图 | 0 / 0 |
| holdout 4h 币种行 | 36,720 |
| holdout 配置消耗 | #2 |
| 退出状态 | code 1，失败关闭 |

`36,720` 的来源是与 v1 完全相同的固定 54 币扫描网格：每币 680 根 holdout 4h K 线，即 `54 × 680`。进程已完成全部币种读取；它在选择阶段报错，因此内存中的计数器没有写进成功 summary。本报告以独立的 [失败回执](../experiments/active/exp-btc-4h-ma-launch-similarity-top20-v2/results/failure_receipt.json) 补记该事实，没有把它伪装成成功扫描产物。

## 与上一版本同表对照

| 配置 | 唯一变量 | 宽门 LONG / SHORT | 请求 Top-N | 去重容量 | 状态 | 图数 | holdout 消耗 |
|---|---|---:|---:|---|---|---:|---:|
| v1 | 基线 | 64 / 30 | 8 / 边 | 两边均 ≥8 | 成功，待 Owner 逐图确认 | 16 | #1 |
| Top-20 v2 | Top-N 8→20 | 64 / 30 | 20 / 边 | LONG ≥20；SHORT=15 | 失败关闭 | 0 | #2 |

这不是相似度性能下降：协议、参考、时间、币池、门槛、通道、尺度、权重、RMSE、DTW、去重和随机种子都未改变。失败原因只是请求的 SHORT 名额超过了最终去重容量。

## 方法与冻结检查

1. 新预注册在扫描前固定，并引用 v1 预注册和 v1 成功 summary 的 SHA-256。
2. 自动检查证明 `spec` 中只有 `top_per_side` 从 8 改成 20；其余字段逐值相同。
3. 扫描仍使用完成形态的 30 根前置 + 12 根匹配释放段；6 根额外 K 线只原计划用于人工复核。
4. LONG 先完成 Top-20 选择；SHORT 在相同 18 根去重后只有 15 个，程序在渲染前退出。
5. 因选择未完成，预注册的“旧 Top-8 身份与距离完全一致”后置门和 Top-20 phase-scramble 均未执行；不能为失败配置借用或编造新的 p 值。

## 严格零假设对照与不适用指标

本轮是非方向性的配置可行性审计，没有训练模型，也没有定义进场、出场、TP/SL 或成本。因此 **val AUC、top-decile 毛/净收益、胜率、0.2% 往返成本和匹配随机入场对照均不适用**；填数会制造不存在的经济结论。

与本问题同等严格的零假设是：如果 Top-N 真的是唯一改变的变量，那么除 `top_per_side` 外的冻结规格必须逐字段等于 v1。自动契约测试通过了这一对照。原计划的第二层零假设——保持释放段联合值但随机打乱 12 根时序——因 SHORT 容量门先失败而没有执行。v1 的 `p=1/201` 只能作为旧 Top-8 基线，不能冒充 v2 的结果。

## 解读

- **30 不是最终容量。** 宽门 SHORT=30 是可能重叠的锚点数量；18 根去重把同币相邻锚点合并后只剩 15 个事件邻域。
- **图数为 0 是正确行为。** 如果程序先渲染 LONG 20 张再报 SHORT 不足，用户很容易误把半成品当正式榜单；当前实现先完成双边选择，再开始画图。
- **Top-15 只是容量上限，不是已批准结果。** 15 是本次冻结规格看到的 SHORT 最终容量。用它设计下一配置会产生新的 estimand，也会再次读取 holdout，所以必须单独授权。
- **不能从本轮推断可训练性。** 这些仍是使用启动后 12 根未来 4h K 线的完成形态检索，既不是 causal tip 标签，也不是训练集正例。

## 风险与诚实声明

1. 本轮完整读取 holdout 后才在最终去重容量门失败，因此即使没有图或成功 summary，也必须记录消耗 #2。
2. 失败前没有持久化 15 个 SHORT 的逐行身份和距离；本报告只声称错误消息能证明的容量，不推测第 9–15 名是谁。
3. LONG 在 SHORT 失败前已至少选出 20 个，但未形成正式双边结果；同样不交付、不注册为候选。
4. 没有改变原始 K 线、训练、模型、ACTIVE/frozen、forward、阈值、新鲜度门、日志或交易状态。
5. 任何 Top-15、非对称 20/15、缩短去重或换币池都属于新配置，必须重新预注册并获得 Owner 授权。

## 下一步选项

| 选项 | 新读取 holdout | 能交付什么 | 约束 |
|---|---:|---|---|
| 停在现有 v1 | 0 | 已有 8 LONG + 8 SHORT 图 | 不会增加案例 |
| 对称 Top-15（建议） | #3 | 若旧 Top-8 前缀门通过，新增 LONG 9–15 与 SHORT 9–15，最多 14 张图 | 先冻结、再获 Owner 明确批准 |
| 非对称 LONG20 / SHORT15 | #3 | 最多新增 12 LONG + 7 SHORT | 改变双边比较口径，不优先 |
| 改去重或宽门 | #3 或更多 | 数量可能增加 | 事后调规则风险最高，不建议 |

当前允许的动作到此为止。下一步需要 Owner 明确回答是否授权按**只改 Top-N 为 15**的新配置进行 **holdout 第 3 次读取**。

## 复现命令

以下是已经执行且预期以 code 1 失败关闭的完整命令；**不要自行重跑**，因为每次执行都会再次读取 holdout：

```bash
git show --stat 4979e1871a0f883405d76fc5e357fe63b8bf0daf
PYTHONPATH=. .venv/bin/python scripts/find_four_hour_ma_launch_similarity.py \
  --prereg experiments/active/exp-btc-4h-ma-launch-similarity-top20-v2/preregistration.json \
  --out experiments/active/exp-btc-4h-ma-launch-similarity-top20-v2/results
python3 scripts/md_to_html.py \
  analysis/p0_btc_4h_ma_launch_similarity_top20_v2_failure_20260825.md \
  --out-dir analysis/html
```

回执校验与非 holdout 测试：

```bash
python3 -m json.tool experiments/active/exp-btc-4h-ma-launch-similarity-top20-v2/results/failure_receipt.json >/dev/null
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_four_hour_similarity.py \
  tests/test_find_four_hour_ma_launch_similarity.py \
  tests/contracts/test_registries.py
```
