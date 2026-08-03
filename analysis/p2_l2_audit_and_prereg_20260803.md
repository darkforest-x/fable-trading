# P2-L2 只读审计与预注册（训练前 Owner 门）

**日期**：2026-08-03

**当前阶段**：P2.0 只读审计完成；P2.1 预注册已获 Owner 批准并冻结；**尚未训练**

**机器审计**：`analysis/output/p2_l2_audit_20260803.json`

**机器预注册**：`analysis/output/p2_l2_prereg_20260803.json`

## 直接结论

P1 immutable dataset 的 P2 输入门通过。Owner 在对话中以“批准”确认了上一条消息列明的
两个具体推荐值；机器预注册现为 `status=accepted`，可以进入 fixture / dry-run，再进入
full training。记录批准时尚未训练或在真实分数上校准 threshold。

08 页没有定义 P2；同一接管计划 01 页只给出 P2-L2 的验收原则，05 页明确要求在 P2
前由 Owner 批准实际成本压力线，并审查固定 runtime gate。当前指令授权进入 P2，但没有
给出滑点 / funding 数值或 operator。因此先完成可复核审计和完整预注册草案；随后 Owner
明确批准推荐值，才把机器门切换为 accepted。

## P2.0 输入事实

| 项 | 审计结果 |
|---|---:|
| dataset | `data/p1/p1_short_l2_preholdout_aade2a334448d644.csv` |
| dataset SHA256 | `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a` |
| manifest SHA256 | `53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682` |
| 行 / 币 / event group | 18,103 / 230 / 15,604 |
| signal 时间 | 2026-02-01 01:00 → 2026-05-03 05:15 UTC |
| 最大 label interval end | 2026-05-03 22:45 UTC |
| holdout signal / interval | 0 / 0 |
| 28 features missing / inf | 0 / 0 |
| label TP 正类 | 4,533 / 18,103 = 25.04% |
| gross / taker-net 全池均值 | +4.08bp / -5.92bp |
| 成本恒等式 | `net = gross - 0.001`，最大误差 < 3e-16 |

本审计只读取 manifest 指定的 P1 CSV 作为研究数据。没有读取 raw candle、funding、其他
dataset 或 holdout；没有训练、打分或在真实分数上校准 threshold。

## 时间三段与依赖 purge

边界只按 `signal_time` 的数量分布预先固定，没有用 feature、label、return 或 score：

| 段 | UTC 规则 | rows | event groups |
|---|---|---:|---:|
| train | signal < 2026-03-27；label end 也必须早于边界 | 10,940 | 9,403 |
| early-stop val | 03-27 ≤ signal < 04-14；label end 早于 04-14 | 3,498 | 3,013 |
| calibration | signal ≥ 04-14；label end 严格早于 holdout | 3,623 | 3,156 |
| purge | interval 穿越边界，且其完整 event group 一并删除 | 42 | 32 |

实现按完整 `[interval_start, interval_end]` 和 `event_group_id` purge；一个连接分量只要跨段，
就在所有段删除，不能靠固定 72-bar row purge 掩盖依赖。最终跨段 event group 数为 0。

## P2.1 预注册草案

- 唯一模型：LightGBM regression，target=`net_ret_swap_taker`，28 特征严格取 manifest 顺序；
- 不做参数扫描；固定现有主线参数，最多 600 轮、patience 50；
- early-stop 指标是 early-stop 段的 exact top-decile mean net，不再让 RMSE 常数预测器决定业务模型；
- 单特征基线是只用 `ma_spread_pct` 的普通最小二乘回归；
- 5 个 expanding chronological walkforward test folds；每折内部重新做 train / early-stop /
  calibration，并按 interval / event group purge；
- selector 固定 q90、operator `>=`。边界可分时用两侧分数中点；边界并列时整块通过；
  禁止按行号、ID 或 outcome 切并列；
- exact top-decile 与 fixed threshold 分开报告。并列导致无法 exact-k 时明确标 unavailable；
- 匹配对照只从同一 P1 candidate pool 的未选行抽取：同币 × UTC 周 × 折内 ATR quintile，
  不放回且排除共享 event group；它衡量 L2 选择增量，不冒充“检测器 vs 市场随机入场”；
- 经济置换统计量是匹配 lift，按 UTC 周做 exact block sign-flip；AUC 只作参考；
- 成功线：健康门全过、每折 selected≥100、至少 4/5 折 fixed-gate pressure-net>0、聚合
  pressure-net>0、matched lift>0 且经济 block permutation `p<0.01`。

## Owner 已批准的两项

### 1. 实际成本压力线

可核验事实：P1 只有 swap taker 往返 **0.10%**；没有滑点列、没有 funding 列。历史执行
审计的 clean fill/mark 配对数是 **0**，不能声称“实测滑点”。

批准固定为：**总往返 0.15%**，即在 P1 `net_ret_swap_taker` 上再减固定 0.05% 滑点；因
“只使用 P1 dataset”的本轮边界，funding 不建模并在结论中标为缺口。这是仓库已有 short
成本敏感性档，不是从本轮结果反推。若 Owner 不接受，应给出一个明确总成本或额外滑点数值；
不能在训练后再选。

### 2. 固定 runtime gate

批准固定为：calibration q90、`score >= threshold`；边界可分时 threshold 取上下分数中点；
边界并列时整块通过。健康门为 calibration pass rate 8%–12%、threshold equality≤2%、
distinct scores≥100、best iteration>1；任一失败即 P2 rejected，不换 operator、不切 ties。

Owner 原话“批准”与上下文已写入机器预注册。该批准只覆盖 P2 成本压力线和 fixed gate，
不授权 holdout、ACTIVE、active bundle、部署或下单。

## 安全边界

- holdout：未读；
- `models/ACTIVE`：未改；
- `models/active_bundle.json`：仍不存在；
- deploy / restart：未执行；
- 交易 client / 下单：未访问；
- 当前只完成 P2.0 与 P2.1 草案，P2 尚未完成。

## 测试

- fixture / P1 contract 聚焦测试：9 passed；
- 完整 `tests/`：492 passed、2 skipped、0 failed；
- py_compile 通过；
- 测试后 ACTIVE / forward log / ledger SHA 与 P1 结束时完全相同，active bundle 仍不存在。

机器结果：`analysis/output/p2_prereg_test_results_20260803.json`。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_p2_prereg_20260803.py
PYTHONPATH=. .venv/bin/pytest -q tests/test_p2_protocol.py tests/test_p1_dataset_contract.py
python3 scripts/md_to_html.py analysis/p2_l2_audit_and_prereg_20260803.md --out-dir analysis/html
```
