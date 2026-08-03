# P0 独立验收报告（2026-08-03）

## 直接裁决

**`p0_independent_acceptance = accepted`，允许进入 P1-DATA。**

本裁决由重新检查 `4333fa7..fba6a65` 的九个提交、当前源码、测试、P0 报告、机器 JSON、HANDOFF 和受保护文件得出，不引用上一轮完成摘要作为证明。当前仍无 `models/active_bundle.json`，生产继续 fail-closed。

机器可读结论：`analysis/output/p0_independent_acceptance_20260803.json`。

## Commit 独立复核

| 顺序 | commit | 目的 | 写入范围裁决 |
|---:|---|---|---|
| 1 | `95ebfb0` | P0.0 基线证据 | 只新增 `analysis/output/p0_safety_baseline_*` |
| 2 | `cd9ca5a` | side/protocol signal identity 与 executor 拒绝 | execution、tests、learning |
| 3 | `8cd2a56` | exact bundle + hash、production 不 fallback | protocol/executor/forward、example、tests、learning |
| 4 | `892964c` | 单一 protocol provenance 传播 | forward/executor、tests、learning |
| 5 | `1cb669c` | feature semantics 显式选择 | features/forward、fixture、tests、learning |
| 6 | `ee98ebd` | canonical barrier/return/cost | judgment/cost、audit、tests、learning |
| 7 | `8e90390` | signal/decision/request/fill 拆分 | forward/executor/web、tests、learning |
| 8 | `969dda7` | global tip age 与 selector safety | candidate/protocol、example、tests、learning |
| 9 | `fba6a65` | P0 报告、JSON、HANDOFF 与验收矩阵 | docs/analysis/audit/test |

机器审计确认九个 commit 的 parent chain 完整、共 60 个变更路径，且：

- 无 `data/`、模型权重、`models/ACTIVE`、`models/active_bundle.json`、密钥或部署路径变更；
- 无删除文件、无覆盖旧 analysis 实验报告；
- P0 新增 executable diff 中无训练调用、无新增下单/撤单调用；
- `main`、P0 target 和当时 `origin/main` 都是 `fba6a65`；生成 acceptance 文件前 worktree clean，生成时只有本 acceptance 脚本与 learning 属于允许的新增路径。

## 保护对象与 cutoff

| 对象 | P0.0 SHA256 | 当前 SHA256 | 结果 |
|---|---|---|---|
| `models/ACTIVE` | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef` | 同左 | unchanged |
| `data/forward_log.csv` | `6035eb60482481fb60d7e73aa72dd15d1b8884ee4c2da5410fbffa18b17b34bb` | 同左 | unchanged |
| `data/executor_ledger.jsonl` | `de85b3dded80717a1bc0399411c6fc59c2f11842095aac2e105b0d128941fe39` | 同左 | unchanged |

`models/active_bundle.json` 在基线 commit、P0 target 和当前工作树均不存在。

独立流式读取冻结 P0 数据集的 `signal_time` 列：18,379 行，SHA `9bca6802…a94` 与 sidecar 一致，最大时间 `2026-05-03T05:15:00Z`，cutoff 为 `2026-05-04T00:00:00Z`，读取到的 holdout 行数为 0。

## 安全不变量复核

| 不变量 | 独立结论 | 主要证据 |
|---|---|---|
| short/missing/NaN/unknown/protocol mismatch 在 client 前拒绝 | accepted | executor guard tests，mock client 0 调用 |
| production 只接受 exact bundle + hash，无 latest fallback | accepted | corrupt/absent/hash/early-fail tests |
| bundle 不存在时 fail-closed | accepted | forward 在读取 log 前抛 `BundleError` |
| signal/decision/request/fill 分离；无 fill 无 actual PnL | accepted | execution timeline 与 actual-closed tests |
| whole-series global tip age `<=2` | accepted | tip/tip-1/tip-2 接受、tip-3 拒绝 tests |
| 47-feature 研究结果未归给 28-feature ACTIVE | accepted | parity JSON 仍为 `REJECTED` |
| q90 异常只记录/阻断，未偷调 selector | accepted | pass 91.13%、equal 86.16%、ACTIVE hash 不变 |
| gross/taker/maker 不隐式二次扣费 | accepted | gross bridge 与 already-net refusal tests |

## 测试与环境边界

仓库虚拟环境：Python 3.9.6，torch 2.8.0，torchvision 0.23.0，ultralytics 8.4.89，LightGBM 4.6.0，pandas 2.3.3，numpy 2.0.2。

```text
P0 聚焦矩阵：133 passed
完整 tests/：473 passed, 2 skipped, 0 failed, 0 deselected
```

两项 skip 都来自 `tests/test_factor_causality.py`：rolling skew 的 pandas 浮点状态伪差、rolling rank 慢/脆弱用例；它们不是 15m YOLO candidate、28-feature extractor、canonical label 或 cost path。上一轮 system Python 缺 `torchvision` 的结果只能描述错误解释器的环境边界；仓库 `.venv` 已无需 deselect 完整通过。

测试前后 ACTIVE、forward log、ledger 哈希均一致。

## 报告与 HANDOFF 一致性

- P0 报告明确写“本地安全验收通过，但策略不可执行”；
- runtime parity JSON/报告明确拒绝研究 lift 转移；
- return audit 最大数据时间严格早于 cutoff；
- HANDOFF 顶部保留 Owner gate、无 active bundle、不得自动进入 P1/P2 的原始结论；
- MD 与 HTML 均存在且非空。

## 研究指标适用性

本轮是安全验收，不是策略实验；没有训练、阈值搜索或新收益配置，因此 val AUC、置换 p、top-decile 收益、胜率、单特征与随机对照均为 **N/A**。P0 JSON 中的旧 v10 数字只用于身份与语义核对。

## 风险与诚实声明

- Git、哈希和 ledger 不变可以证明没有持久化下单证据，无法从本地仓库证明外部世界绝对没有人工操作；本轮没有访问 VPS、账户或密钥。
- P0 acceptance 只授权 P1 数据重建，不授权训练、selector、ACTIVE、active bundle、部署或下单。
- P1 full build 仍必须先证明 detector、universe 与原始 candle 输入的唯一权威来源，并先通过 fixture/dry-run。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_p0_independent_acceptance_20260803.py
python3 scripts/md_to_html.py analysis/p0_independent_acceptance_20260803.md --out-dir analysis/html
python3 scripts/gen_analysis_index.py
```

## 安全声明

独立验收阶段未训练、未调 threshold、未读取 cutoff 起的 holdout、未修改 `models/ACTIVE`、未创建 active bundle、未修改主 forward log/ledger、未部署、未访问交易 client 或下单。
