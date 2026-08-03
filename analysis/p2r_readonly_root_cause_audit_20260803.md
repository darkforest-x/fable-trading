# P2-R：P1 immutable 上的只读根因审计

**日期**：2026-08-03

**执行边界**：只使用 P1 immutable dataset 与已冻结的 P2 产物；不训练、不拟合、不调
threshold、不读 holdout、不修改 ACTIVE、不创建 active bundle、不部署、不访问交易 client、
不下单；P2-R 完成后停止。

**机器结果**：`analysis/output/p2r_root_cause_audit_20260803.json`

## 技术摘要：P2 的主因不是 q90，而是没有证明可泛化排序增益

**P2-R 已完成并停止；P2 的 REJECTED 裁决不变。** 独立只读复算支持以下诊断：

- fold-local exact top-decile 在 **4/5** 折为负，加权 pressure-net 为 **-15.91bp**；同一
  五折整池为 **-15.33bp**，排序相对整池反而 **-0.59bp**。因此只改 q90 / pass rate
  不能修复排序；
- 1,051 个同币 × 同 UTC week × 同 ATR 桶匹配对照独立复算 lift 仅 **+0.74bp**，exact
  sign-flip `p=0.4836`，没有通过 `p<0.01`；
- outcome base 明显换挡：五折 TP-before-SL 从 13.50% 到 33.03%，整池 pressure-net 从
  -64.32bp 到 +20.47bp；同时 fold 2 / 4 退化到 best iteration=1、15 个分数，fixed gate
  在 4/5 折传输到 8%–12% 之外；
- 28 个冻结特征无 missing / non-finite；20 个特征满足预注册的跨折 Spearman 稳定规则。
  这说明数据并非“完全没有关联结构”，但 P2 的多特征回归没有把它转成独立经济排序增益；
  本审计已看过全部特征与五折 outcome，后续从中挑特征再跑同一 P1 **只能算探索**，不能
  重新宣称独立确认。

结论的证据等级是 pre-holdout 描述性/诊断性，不是单一根因的因果证明，也不是 holdout、
上线或实盘证明。

## 五折分解：市场/标签基线、排序增益与 gate 传输必须分开

P2-R 没有重新预测。它按原时间边界、label interval 和 event-group 规则独立重建五个 test
fold，再从 P1 outcome 复算整池基线；P2 exact-top 与 fixed-gate 数字来自 SHA 冻结的 P2
机器结果。`exact lift vs pool` 是同折 exact-top pressure 减整池 pressure。

| fold | rows | TP rate | 整池 pressure | exact-top pressure | exact lift vs pool | fixed pass | iter / distinct |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,937 | 33.03% | +20.47bp | +34.27bp | +13.80bp | 5.38% | 21 / 294 |
| 2 | 2,918 | 13.50% | -64.32bp | -78.68bp | -14.36bp | 51.44% | **1 / 15** |
| 3 | 2,996 | 30.07% | +3.24bp | -9.28bp | -12.52bp | 5.17% | 47 / 1,115 |
| 4 | 2,944 | 20.41% | -28.95bp | -19.89bp | +9.06bp | 88.55% | **1 / 15** |
| 5 | 3,000 | 24.27% | -7.91bp | -6.77bp | +1.14bp | 10.07% | 38 / 964 |
| aggregate | 14,795 | — | **-15.33bp** | **-15.91bp** | **-0.59bp** | — | — |

这张表同时排除了两个过度简化的解释：

- **不是只怪整池 beta。** 整池确实有巨大换挡，但 exact-top 相对整池没有正的聚合增益，
  fold 2 / 3 还明显选得更差；
- **不是只怪固定门。** fold 2 / 4 的 tie mass 和 pass 漂移会放大 fixed-gate 亏损，但固定门
  无关的 exact-top 已经 4/5 折为负。

P2 的 test-row 加权参考 rank 指标仍为 AUC 0.5117、PR-AUC 0.2454、Spearman 0.0552；它们
接近随机排序，不覆盖经济失败。

## 与 P2 原结果同表：新增的是“相对整池增益”定责

| 指标 | P2 冻结结果 | P2-R 独立复核 / 新分解 | 解释 |
|---|---:|---:|---|
| fold-local exact-top gross | -0.91bp | hash 与逐折 n 对齐 | 成本前已略负 |
| fold-local exact-top taker-net | -10.91bp | hash 与逐折 n 对齐 | P1 已含 10bp taker |
| fold-local exact-top pressure-net | **-15.91bp** | **-15.91bp** | 再减批准的 5bp slippage |
| exact-top TP-before-SL | 22.51% | hash 对齐 | 仅 1/5 折 pressure 为正 |
| 同期整池 pressure-net | 未单列 | **-15.33bp** | P2-R 直接从 P1 复算 |
| exact-top 相对整池 | 未单列 | **-0.59bp** | 排序未提供聚合 edge |
| fixed-gate pressure-net / PF | -39.33bp / 0.641 | hash 对齐 | tie 与 gate transport 放大失败 |
| 单特征 fixed baseline | -22.67bp / 0.873 | hash 对齐 | 少亏，但仍为负 |
| matched lift / p | +0.74bp / 0.4836 | **+0.74bp / 0.4836** | CSV 独立重算 4,096 种符号翻转 |

## 匹配对照：selector 的微小 lift 与噪声不可分

P2-R 重新读取冻结 matched-pair CSV，而不是引用上一轮摘要；它验证 selected/control IDs
均存在于 P1、两侧 ID 不重复、无 self-pair、pressure delta 与 taker-net delta 相等，并独立
枚举 12 个 UTC-week blocks 的 4,096 种符号翻转。

| 项 | 独立复算 |
|---|---:|
| matched pairs / blocks | 1,051 / 12 |
| selected pressure-net | -44.27bp |
| control pressure-net | -45.02bp |
| lift | **+0.74bp** |
| hits ≥ observed / permutations | 1,981 / 4,096 |
| one-sided exact p | **0.483642578125** |

lift 的点估计略正，但离预注册 `p<0.01` 很远；不能用“至少比对照好一点”替代显著性门。
匹配覆盖仍只有 22.25%，所以它是 selector 增量诊断，不是 L1 detector 对市场随机入场的证明。

## 28 特征审计：关联结构存在，但已被事后查看

固定规则为：五个 test folds 中至少 4 折同号，且 `abs(median Spearman) >= 0.03`。20/28
特征通过；下表列绝对中位相关最大的 8 个，完整 28×fold 表在机器 CSV。

| feature | median test Spearman | min → max | 同号折 |
|---|---:|---:|---:|
| `atr_pct` | -0.300 | -0.516 → -0.273 | 5/5 |
| `pre_range168` | -0.263 | -0.374 → -0.213 | 5/5 |
| `pre_range48` | -0.256 | -0.378 → -0.242 | 5/5 |
| `close_vs_ema55` | -0.243 | -0.313 → -0.139 | 5/5 |
| `ext_up` | -0.234 | -0.341 → -0.130 | 5/5 |
| `ret_12` | -0.218 | -0.334 → -0.127 | 5/5 |
| `dense_frac48` | +0.218 | +0.124 → +0.269 | 5/5 |
| `full_spread` | -0.211 | -0.265 → -0.152 | 5/5 |

这些是单变量秩关联，不是因果贡献，也不保证 top-decile 扣成本为正。尤其 ATR、range、return
类特征可能部分反映 ATR 障碍下 outcome 幅度，而不是可执行的独立形态 edge。主模型只有
一棵树时的 feature importance 也不能作为因果证据。

更关键的是确认纪律：P2-R 已同时查看 28 个特征和五折 outcome。若现在按本表选最强特征，
再在相同 P1 上训练/验收，就发生 outcome-driven feature selection；结果只能标为 exploratory，
不能重新打开 P2 acceptance。

## 数据、口径与安全边界

| 项 | 值 |
|---|---:|
| P1 dataset SHA256 | `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a` |
| P1 manifest SHA256 | `53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682` |
| P2 result SHA256 | `5bfbd4f4953554fb25a12503cf1b711b14236f330bca458a78f14aa5e298f6da` |
| rows / symbols | 18,103 / 230 |
| signal range | 2026-02-01 01:00 → 2026-05-03 05:15 UTC |
| max label interval end | 2026-05-03 22:45 UTC |
| holdout signal / interval rows | 0 / 0 |
| feature missing / non-finite | 0 / 0 |
| training / fitting calls | 0 |

`pressure_net = net_ret_swap_taker - 0.0005`；P1 target 已含 0.10% taker 往返，本审计只再减
Owner 批准的 0.05% slippage，没有重复扣 0.15%。funding 仍为 P1-only 未建模。

P2-R 预注册 SHA256：
`084a83296897ca282ef664ff4a8493b83a8f2e8b1512cb1d11d787bd4dc82c6a`。

## 方法与可核验性

1. 在执行前提交 P2-R JSON 预注册，冻结输入 hash、假设、阈值无关定责规则和禁止项；
2. 审计脚本不导入 P2 训练入口，自行按固定时间分位与 event-component purge 重建五折；五折
   test rows 精确复现为 2,937 / 2,918 / 2,996 / 2,944 / 3,000；
3. 从 immutable P1 outcome 复算每折整池经济、TP/SL/timeout mix 与 28 feature Spearman；
4. 从冻结 P2 JSON 读取 fold-local model / selector / exact-top 结果，并用 hash 防止事后替换；
5. 从 matched-pair CSV 独立复算配对完整性、lift 与 exact UTC-week sign-flip p；
6. 前后比较 ACTIVE、forward log、executor ledger 和 active bundle；hash 完全不变。

本报告没有画趋势图：只有五个预注册折，折级数字用审计表能完整呈现；画连线容易暗示不存在的
连续趋势。28 特征的完整值保存在 CSV，避免用选择性图形突出事后最优特征。

## 预注册诊断门结果

| diagnostic rule | 结果 |
|---|---:|
| exact-top pressure ≤0 至少 4/5 折 | **TRUE（4/5）** |
| collapse 至少 2/5 折 | **TRUE（2/5）** |
| fixed pass 脱离 8%–12% 至少 3/5 折 | **TRUE（4/5）** |
| TP rate range≥10pp 或 pool pressure range≥50bp | **TRUE（19.52pp / 84.79bp）** |
| matched p≥0.01 | **TRUE（0.4836）** |
| 至少一个稳定 feature | **TRUE（20/28）** |
| feature missing / non-finite=0 | **TRUE** |

由预注册 decision rule 得出：`threshold_only_fix_supported=false`；
`single_variable_training_followup_supported=true` 仅表示未来可以另立 **探索性** 预注册，
不是本轮训练授权，也不是确认级证据。

## 风险、限制与诚实声明

- P2-R 没有保存的逐行 fold predictions，因此无法从 P1 独立重建 exact-top 成员；它通过固定
  P2 results hash、独立复现 fold rows、整池 outcome 与 matched pairs 来定责；
- 只有约三个月 pre-holdout，且五折共享 expanding history，不应把五折当五个完全独立市场；
- base-rate / pressure 换挡是描述性现象，不能单独证明某个市场 regime 导致模型失败；
- 20 个稳定 IC 经全特征扫描得到，存在多重查看与 ATR-scaled outcome 的机械关联风险；
- matched coverage 22.25%，只能约束可匹配子集；
- funding 未建模；0.15% pressure line 不是 all-in 实盘成本实测；
- holdout 消耗为 **0**；没有训练、调 threshold、promote、ACTIVE/bundle、部署或订单。

## 下一步选项与未解决问题

**本轮动作：无；立即停止。** 证据不支持调 q90、读 holdout或上线。

若 Owner 将来另行授权，只有两个诚实选项：

- 停止当前 L2 target / objective 路线，保留 P2 rejected；
- 另做单变量、单假设的 **exploratory** 预注册，先解释 IC 是否只是 ATR/障碍幅度的机械关系。
  因为 P1 五折已经被 P2-R 全面查看，该实验不能用相同 P1 重新获得确认级 acceptance；真正
  确认需要预注册后未参与选择的新鲜前向样本。

未解决且本轮不继续的问题：稳定 IC 来自形态信息还是 barrier scaling；回归 objective 与
TP-before-SL / pressure economics 是否错配；为什么 fold 2 / 4 的 early-stop 退化为一棵树。

## 机器产物与 hash

| artifact | SHA256 |
|---|---|
| prereg JSON | `084a83296897ca282ef664ff4a8493b83a8f2e8b1512cb1d11d787bd4dc82c6a` |
| root-cause audit JSON | `dec35ff2bf3a7600d13edd9f614892a3b5bed5e5d8a402d73242a7aca4d94def` |
| feature IC CSV | `3cb8346329dc60d5ec3418720f3f0a703164e7374106599f41705b11920d3f2d` |
| fold diagnostics CSV | `1fea1206dd5411885654353e9f4360cfec07835eca8e123f45bbae2e2c136748` |

完整清单另见 `analysis/output/p2r_hashes_20260803.sha256`。

## 测试

- P2-R 专项：7 passed；
- 完整 `tests/`：506 passed、2 skipped、14 warnings、0 failed；
- 静态 AST 检查确认审计代码没有 estimator `.fit`、`train_regressor` 或训练调用；
- ACTIVE / forward log / ledger hash 不变；active bundle 不存在。

机器结果：`analysis/output/p2r_test_results_20260803.json`。

## Commit 列表

| commit | 内容 |
|---|---|
| `ef2b495` | 固化 P2-R 只读预注册、安全边界与固定诊断规则 |
| `5793d6a` | 只读折重建、根因审计、机器 JSON/CSV 与 7 个专项测试 |

报告、HTML、full tests、learnings 与 HANDOFF 的关闭提交在本报告之后形成，最终列表以 git log
与交付回复为准。

## 从零复现

```bash
# 输入 hash、只读五折重建、整池/IC/matched 根因审计；无训练入口
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2r-pycache \
  MPLCONFIGDIR=/private/tmp/p2r-mpl \
  .venv/bin/python scripts/audit_p2r_root_causes_20260803.py

# 专项与完整测试
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2r-pycache \
  MPLCONFIGDIR=/private/tmp/p2r-mpl \
  .venv/bin/pytest -q tests/test_p2r_root_cause_audit.py
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2r-pycache \
  MPLCONFIGDIR=/private/tmp/p2r-mpl \
  .venv/bin/pytest -q tests

# 仓库规定的 HTML 交付
python3 scripts/md_to_html.py analysis/p2r_readonly_root_cause_audit_20260803.md \
  --out-dir analysis/html
```

## 停止点

**P2-R 已完成，P2 仍为 REJECTED，立即停止。** 不训练、不调 threshold、不读 holdout、
不修改 ACTIVE、不创建 active bundle、不部署、不下单。任何后续探索都需要 Owner 新指令与
单独预注册，且不得把相同 P1 上的自适应结果声称为独立确认。
