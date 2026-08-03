# P2-L2：immutable P1 dataset 训练与 pre-holdout 验收

**日期**：2026-08-03

**执行边界**：只使用 P1 immutable dataset；不读 holdout、不修改 ACTIVE、不创建 active
bundle、不部署、不访问交易 client、不下单；P2 完成后停止。

**机器结果**：`analysis/output/p2_l2_results_20260803.json`

**独立产物审计**：`analysis/output/p2_l2_independent_audit_20260803.json`

## 直接裁决

**P2-L2 = REJECTED。** 训练与验证流程完整执行，但模型没有形成可部署的固定门：

- 主模型再次退化成 **best_iteration=1 / 1 棵树 / 15 个 distinct scores**；
- calibration q90 落在覆盖 81.23% 样本的并列分数上，`>=` 实际放行 **85.51%**，远离
  预注册的 8%–12%；
- 5-fold fixed runtime gate 只有 **1/5** 折 pressure-net 为正，聚合 **-39.33bp/trade**；
- matched candidate control lift 仅 **+0.74bp**，UTC-week exact block permutation
  `p=0.4836`，未过 `p<0.01`；
- 所有经济成功门和模型健康门按预注册判定，未换 threshold operator、未切并列、未调成本、
  未补跑模型。

artifact integrity 独立审计为 **accepted**，意思是 REJECTED 结论、hash、模型、逐折聚合、
matched permutation 与安全边界均能复算一致；不是策略 accepted。

## 规范、Owner 决策与预注册

08 页止于 P1；同一接管计划 01 页规定 P2-L2 的时间三段、event purge、fixed gate、5-fold
walkforward、matched random control 与经济 block permutation，05 页要求 Owner 批准实际
成本压力线和 runtime gate。

训练前已单独提交机器预注册。Owner 在对话中回复“批准”，对应：

- 总往返成本 **0.15%**：P1 `net_ret_swap_taker` 已含 0.10% taker，评估只再减 0.05%
  slippage，禁止重复扣 0.15%；
- funding：因本轮只允许 P1 dataset，固定为 `not_modeled_p1_only`，不暗填 0 为“实测”；
- selector：calibration q90、`score >= threshold`；边界可分时取上下分数中点，边界并列时
  整块通过；pass 8%–12%、threshold equality≤2%、distinct scores≥100；不切 ties；
- 单一 LightGBM regression、28 个 manifest features、target=`net_ret_swap_taker`、无参数
  搜索；单特征基线只用 `ma_spread_pct`。

预注册 SHA256：
`38e4c474323bc03f269168db6a030575ce94ffbd4e69652403d54539da7a72b6`。

## P1 dataset 与时间切分

| 项 | 值 |
|---|---:|
| dataset SHA256 | `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a` |
| rows / symbols / event groups | 18,103 / 230 / 15,604 |
| signal range | 2026-02-01 01:00 → 2026-05-03 05:15 UTC |
| max label interval end | 2026-05-03 22:45 UTC |
| holdout signal / interval rows | 0 / 0 |
| feature missing / inf | 0 / 0 |
| label positive | 4,533 / 18,103 = 25.04% |
| full-pool gross / taker-net | +4.08bp / -5.92bp |

主三段只按 UTC 时间边界选择，完整 label interval 与 `event_group_id` purge：

| segment | rows | event groups | signal range |
|---|---:|---:|---|
| train | 10,940 | 9,403 | 02-01 01:00 → 03-26 20:45 |
| early-stop | 3,498 | 3,013 | 03-27 00:15 → 04-13 21:15 |
| calibration | 3,623 | 3,156 | 04-14 00:15 → 05-03 05:15 |
| purged | 42 | 32 | interval 触边及完整连接分量 |

最终跨段 event group=0。fixture 曾发现“桥接行删除、同组邻居仍留在下一段/outer test”的
漏洞；修复后 tainted group 会向完整组件和所有嵌套分区传播。

## Fixture 与小样本 dry-run

fixture 在不读真实 dataset、不训练的条件下验证：

- 可分 q90 精确放行 10%；
- 大并列整块通过，20% pass / 20% equality 会触发 health fail；
- exact top-decile 对边界并列使用等权 fractional weight，不按 ID/行序切样本；
- 8 个 UTC-week 全正 block 的 exact sign-flip 为 256 种、`p=1/256`；
- 保护对象前后 hash 不变。

P1 小样本 dry-run 使用 1,500 / 600 / 600 行，结构门 accepted：28 轮、596 distinct scores、
q90 pass=10%、runtime/offline set identity=true。其 pressure-net 为负，只作为管道 smoke，
没有用于更改预注册。

## 主模型与 calibration

| 指标 | P2 LightGBM | 单特征 OLS baseline |
|---|---:|---:|
| best iteration / trees | **1 / 1** | N/A |
| distinct calibration scores | **15** | 3,623 |
| calibration AUC | 0.4756 | 0.4745 |
| q90 target / actual selected | 362 / **3,098** | 362 / 362 |
| calibration pass rate | **85.51%** | 9.99% |
| threshold equality rate | **81.23%** | 0% |
| exact-top pressure-net | **-31.92bp** | -23.81bp |
| fixed-gate pressure-net | **-19.74bp** | -23.81bp |

主模型健康门四项全部失败：best iteration≤1、pass rate 超界、equality 超界、distinct
scores<100。它复现了旧模型“一棵树 + q90 落在大并列块”的病，不是合格的 runtime
selector。研究模型保存在 `analysis/output/` 仅为审计证据；selector manifest 明确
`execution_eligible=false`、`promotion_eligible=false`。

与 P0 复核的 legacy v10 同表只比较 selector 病征，不转移收益结论：

| selector | feature semantics | best iter | distinct scores | fixed-gate pass |
|---|---|---:|---:|---:|
| legacy v10 audit-only | `legacy_unaligned` | 1 | 15 | 91.13% |
| P2 immutable P1 main | `side_aligned_v1` | 1 | 15 | 85.51% |

P1 重建修正了数据与特征语义，却没有自动修复模型分辨率；历史 47-feature / fixed-round
研究收益不是本 P2 配置，不能拿来覆盖这次失败。

## 5-fold expanding walkforward

每折使用此前数据的 70% train / 15% early-stop / 15% calibration，并对 train、early-stop、
calibration、test 做完整 interval / event-group purge。threshold 只看该折 calibration scores。

| fold | best iter | cal pass | test selected / pass | AUC | exact-top pressure | fixed-gate pressure | fixed PF | health |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 21 | 9.80% | 158 / 5.38% | 0.5760 | +34.27bp | **+33.61bp** | 1.292 | PASS |
| 2 | **1** | **33.57%** | 1,501 / 51.44% | 0.5011 | -78.68bp | **-69.26bp** | 0.433 | FAIL |
| 3 | 47 | 9.95% | 155 / 5.17% | 0.5042 | -9.28bp | **-5.33bp** | 0.963 | PASS |
| 4 | **1** | **92.08%** | 2,607 / 88.55% | 0.4893 | -19.89bp | **-32.28bp** | 0.659 | FAIL |
| 5 | 38 | 10.12% | 302 / 10.07% | 0.4887 | -6.77bp | **-7.05bp** | 0.954 | PASS |

逐折 exact-top 也只有 1/5 为正；按各折 effective top-n 加权后的 pressure-net 为
**-15.91bp**。fixed gate 合并的是各折 calibration 后的布尔决策集合：

| aggregate | P2 fixed gate | 单特征 fixed gate |
|---|---:|---:|
| selected | 4,723 | 1,671 |
| pass rate | 31.92% | 11.29% |
| gross mean | -24.33bp | -7.67bp |
| taker-net mean | -34.33bp | -17.67bp |
| pressure-net mean | **-39.33bp** | **-22.67bp** |
| pressure PF | 0.641 | 0.873 |
| TP-before-SL rate | 18.84% | 19.27% |

P2 多特征 fixed gate 比单特征基线还差约 16.66bp/trade。按 test rows 加权的逐折 rank
参考指标为 AUC 0.5117、PR-AUC 0.2454、Spearman 0.0552；它们不覆盖经济失败。

## 匹配对照与经济置换

对照只来自同一个 P1 candidate pool 的未选行：同 symbol × 同 UTC week × 折内 ATR
quintile、不放回、排除 selected event groups。它衡量 L2 selector 增量，不能解释为
“detector vs 市场随机入场”。

| 项 | 值 |
|---|---:|
| fixed-gate selected | 4,723 |
| matched pairs | 1,051 |
| match coverage | 22.25% |
| matched selected pressure-net | -44.27bp |
| matched control pressure-net | -45.02bp |
| lift | **+0.74bp** |
| UTC-week blocks | 12 |
| exact sign-flip permutations | 4,096 |
| economic block permutation p | **0.4836** |

lift 数值略正，但远小于噪声，且两边绝对收益都很差。置换统计量是 matched economic lift，
不是 AUC；因此未过 `p<0.01`。

## 预注册成功门

| gate | 结果 |
|---|---:|
| 主模型与 selector health 全过 | **FAIL** |
| main calibration selected ≥300 | PASS（3,098，但来自异常并列） |
| 5 个 walkforward health 全过 | **FAIL**（3/5） |
| fixed-gate 正收益折 ≥4/5 | **FAIL**（1/5） |
| aggregate fixed-gate pressure-net >0 | **FAIL**（-39.33bp） |
| matched lift >0 | PASS（+0.74bp） |
| economic block permutation p<0.01 | **FAIL**（0.4836） |
| offline/runtime set identity | PASS |
| 无 arbitrary tie slicing | PASS |

只有 4/9 门通过，且核心模型健康、时间稳定性、绝对经济收益、置换显著性全部失败，故
P2-L2 必须 REJECTED。

## 聚合纠错记录

full 训练结束后的独立审计发现，初版报告代码把 5 个不同 fold 模型的 raw scores 拼起来做
全局 top-decile，错误得到 +9.04bp。不同模型的 score 尺度不可直接比较；已在**不重训**、
不改任何逐折结果或成功门的条件下，改为逐折计算后按 effective top-n 加权，正确值为
**-15.91bp**。机器 JSON 保留旧值和 correction 原因；最终 REJECTED 未改变。

对应 learning：
`docs/learnings/walkforward-model-scores-must-not-be-pooled-across-folds.md`。

## 产物与 hash

| artifact | SHA256 |
|---|---|
| results JSON | `5bfbd4f4953554fb25a12503cf1b711b14236f330bca458a78f14aa5e298f6da` |
| research model | `2dc1d6e34f3c60e5f9da6e6c5e79cd5a1d986ff528216ad9115193d4f5115ddf` |
| selector manifest | `32ac1688460a52afeb3d30114394966bcf979109d16766a5030b3e0da205df1c` |
| dataset binding | `cf22add35a895ab14e449465fcd17bac09bb41123c101f666792f3eda5f197ab` |
| matched pairs | `fd55f8a2fea2738b7a8c204eb88e651d8979a83d1d8c1e7213ab27f385e22709` |
| independent audit | `12ad393cf3ec1fb5480980fa529f79fcf0039cf60af8ce483533809b2e6653f3` |

完整文件清单和 hash 另见 `analysis/output/p2_l2_hashes_20260803.sha256`。

## 测试

- P2/P1 聚焦测试：16 passed；
- 完整 `tests/`：499 passed、2 skipped、0 failed；
- 独立 artifact audit：17/17 checks true；
- py_compile 通过；
- ACTIVE / forward log / ledger hash 与 P2.0 完全相同；active bundle 始终不存在。

机器结果：`analysis/output/p2_l2_test_results_20260803.json`。

## Commit 列表

| commit | 内容 |
|---|---|
| `80e6388` | P2.0 只读审计与 awaiting-owner 机器预注册 |
| `cbcebbd` | 冻结 Owner 批准的 0.15% cost 与 q90 fixed gate |
| `c2f774f` | P2 split / model / selector / control / permutation、fixture 与 dry-run |
| `009685f` | full REJECTED 结果、研究模型、selector、matched pairs、独立审计与聚合纠错 |

P2.7 报告 / HTML / tests / HANDOFF 关闭提交在本报告提交后形成，最终 hash 以 git log 与交付
回复为准。

## 风险与诚实声明

- 本轮只有约三个月 pre-holdout，不能替代 holdout 或前向新鲜样本；holdout 消耗为 0；
- funding 按 Owner 批准未建模，0.15% 只是 taker+固定 slippage pressure line，不是 all-in
  实盘成本实测；
- matched coverage 22.25%，且是 candidate-pool 内部对照，不能衡量 L1 detector 对市场
  随机入场的增量；
- 主模型、fold 2、fold 4 都出现 best iteration=1，说明 ranking 能力在时间上不稳定；
- 研究 model/selector 文件是失败审计证据，禁止进入 ACTIVE / active bundle / deploy；
- 没有隐藏 dry-run 负结果、主模型退化、失败折或聚合纠错；
- 本轮没有尝试新特征、固定轮数、换 early-stop metric、换 threshold operator 或降低成本。
  这些都属于新实验，P2 完成后不得自动继续。

## 从零复现

```bash
# 预注册与只读审计
PYTHONPATH=. .venv/bin/python scripts/audit_p2_prereg_20260803.py

# 顺序门：fixture → 小样本 dry-run → 唯一 full
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 \
  .venv/bin/python scripts/run_p2_l2_20260803.py fixture
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 \
  .venv/bin/python scripts/run_p2_l2_20260803.py dry-run
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 \
  .venv/bin/python scripts/run_p2_l2_20260803.py full

# full 后只修正跨模型 score 聚合；不重训
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 \
  .venv/bin/python scripts/run_p2_l2_20260803.py finalize

# 独立产物复核与测试
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 \
  .venv/bin/python scripts/audit_p2_l2_results_20260803.py
PYTHONPATH=. MPLCONFIGDIR=/private/tmp/fable-mpl-p2 .venv/bin/pytest -q tests

# HTML
python3 scripts/md_to_html.py analysis/p2_l2_preholdout_validation_20260803.md \
  --out-dir analysis/html
```

## 停止点

**P2 已完成并 REJECTED，立即停止。** 不进入 P3，不读 holdout，不修改 ACTIVE，不创建 active
bundle，不部署，不下单。任何后续模型改法或数据扩展都需要 Owner 新指令与新预注册。
