# P2-M：ATR 尺度与形态关联的只读机制审计

**日期**：2026-08-03

**执行边界**：唯一数据源为 P1 immutable dataset；P2-R 产物只用于冻结五折与既有 20 个
稳定特征集合。不训练、不拟合、不选 feature、不调 threshold、不读 holdout、不修改 ACTIVE、
不创建 active bundle、不部署、不访问交易 client、不下单；P2-M 完成后停止。

**机器结果**：`analysis/output/p2m_mechanism_audit_20260803.json`

## 技术摘要：大部分 raw return IC 含 ATR 尺度成分，但仍有小幅稳健残余

**P2-M 已完成并停止；P2 仍为 REJECTED。** 预注册机制审计得到的是混合结论：

- P1 的 TP / SL gross return 在 ATR 单位上中位数精确为 **+5.000 / -2.000**，确认 raw
  return target 同时编码了胜负与 ATR 决定的盈亏幅度；
- P2-R 冻结的 20 个稳定 feature 中，**14/20（70%）**在 TP 标签、ATR-normalized gross、
  折内 ATR 五分位 net IC 三条控制线上均衰减到 raw net IC 的一半以内；机械尺度成分占多数，
  但未达到预注册的 75% `global_mechanical_dominance` 门；
- **8/20** feature 在三条控制线上仍满足至少 4/5 折同号且 `abs(median rho)>=0.03`；
  `global_scale_robust_signal=true`。其中 3 个既衰减超过一半又保持稳定，说明“主要受尺度影响”
  与“仍有较小残余”不是互斥结论；
- scale-robust 只表示粗粒度尺度控制后仍有关联，不等于形态因果 edge，更不等于经济
  top-decile 可盈利。P2-M 已查看全部控制结果，不能据此挑 feature 在相同 P1 上重新验收。

因此不支持立即废弃所有 L2 信息，也不支持直接训练。诚实裁决是：**ATR/barrier 机械结构解释
了大部分 raw IC；少量残余值得记录，但证据等级只能是 exploratory。**

## 障碍机制被精确复原：TP=+5 ATR、SL=-2 ATR

定义：

- `atr_return_scale = atr_at_signal / entry_price_research`；
- `atr_normalized_gross = gross_ret / atr_return_scale`；
- 使用 gross 而不是 net，是因为固定 taker fee 不随 ATR 比例缩放。

| exit | rows | ATR-normalized gross median | q10 → q90 | 预注册检查 |
|---|---:|---:|---:|---:|
| TP | 3,594 | **+5.0000** | +5.0000 → +5.0000 | PASS：距 +5 ≤0.25 |
| SL / ambiguous SL | 9,721 | **-2.0000** | -2.0000 → -2.0000 | PASS：距 -2 ≤0.25 |
| timeout | 1,480 | +1.4566 | -0.4063 → +3.3480 | 非固定障碍，不设门 |

这证明 raw return 的绝对幅度必然随 ATR 放大。某 feature 只要与 ATR / range 同向，即使完全
不改变 TP 概率，也可能产生 return IC；因此 P2-R 的 raw IC 不能直接解释成形态 edge。

## 14/20 关联明显衰减，正式全局门仍为 FALSE

机械尺度 feature 的固定定义：该 feature 已属于 P2-R frozen stable set，且以下三个中位
Spearman 的绝对值都不超过 raw net-taker median IC 的 50%：

1. feature vs TP-before-SL；
2. feature vs ATR-normalized gross；
3. feature vs fold-local ATR quintile 内的 net-taker。

| 分类 | 数量 | feature |
|---|---:|---|
| mechanical only | 11 | `close_vs_ema55`, `ext_up`, `ret_12`, `spread_mean24`, `ret_24`, `spread_mean8`, `ret_4`, `drawdown24`, `ret_48`, `spread_chg8`, `dense_run_len` |
| mechanical + scale-robust | 3 | `pre_range48`, `full_spread`, `ma_spread_pct` |
| scale-robust only | 5 | `atr_pct`, `pre_range168`, `fast_slow_gap`, `close_vs_ema200`, `dense_frac48` |
| mixed | 1 | `atr_pct_ratio96` |

mechanical flag 合计 14/20=70%。这是多数且说明 P2-R 的大 IC 普遍被 ATR/barrier 结构放大；
但预注册全局线是 75%，所以机器结论必须保留
`global_mechanical_dominance=false`，不能事后把门降到 70%。

## 8 个 feature 在三条尺度控制后仍稳定，但效应已明显变小

下表只展开预注册规则判为 scale-robust 的 8 个 feature。`ATR rho` 是 feature 与 `atr_pct`
的五折中位相关，用来显示尺度耦合；它不是第四个成功门。

| feature | raw net rho | TP-label rho | ATR-normalized rho | within-ATR net rho | ATR rho | 分类 |
|---|---:|---:|---:|---:|---:|---|
| `atr_pct` | -0.3002 | -0.1374 | -0.0900 | -0.3124 | +1.0000 | robust only |
| `pre_range168` | -0.2632 | -0.0728 | -0.0526 | -0.1659 | +0.7713 | robust only |
| `pre_range48` | -0.2558 | -0.0919 | -0.0518 | -0.1106 | +0.8430 | mechanical + robust |
| `full_spread` | -0.2107 | -0.0872 | -0.0512 | -0.0853 | +0.5648 | mechanical + robust |
| `ma_spread_pct` | -0.1971 | -0.0597 | -0.0365 | -0.0613 | +0.5589 | mechanical + robust |
| `fast_slow_gap` | -0.1522 | -0.0812 | -0.0589 | -0.0784 | +0.3954 | robust only |
| `close_vs_ema200` | -0.1464 | -0.0766 | -0.0482 | -0.0392 | +0.2527 | robust only |
| `dense_frac48` | +0.2177 | +0.0400 | +0.0379 | +0.1145 | -0.5441 | robust only |

最重要的限制是：这不是 feature shortlist。`atr_pct` 在自己的 quintile 内仍是连续值，所以
`within-ATR rho` 不能完全去除 ATR；其他 range/spread feature 也与 ATR 高度相关。8 个残余中
TP / normalized rho 多数只有 0.03–0.09，方向稳定但经济幅度未知。

## 五折 outcome 换挡在 ATR 单位下仍存在

| fold | rows | median ATR scale | TP rate | gross mean | net mean | ATR-normalized gross mean | timeout median ATR |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,937 | 0.6743% | 33.03% | +35.47bp | +25.47bp | +0.633 | +2.084 |
| 2 | 2,918 | 0.6656% | 13.50% | -49.32bp | -59.32bp | -0.666 | +1.360 |
| 3 | 2,996 | 0.5231% | 30.07% | +18.24bp | +8.24bp | +0.408 | +1.776 |
| 4 | 2,944 | 0.5355% | 20.41% | -13.95bp | -23.95bp | -0.200 | +1.346 |
| 5 | 3,000 | 0.5467% | 24.27% | +7.09bp | -2.91bp | +0.049 | +1.189 |

fold 2 即使换成 ATR-normalized gross 仍最差，说明上一轮观察到的 regime shift 不只是不同
fold ATR 水平造成；TP base rate 与 timeout outcome 同样在变。P2-M 是描述性审计，不把这种
时间共变声称为单一市场因果。

## 与 P2-R 同表：raw IC 被拆成尺度成分与残余

| 项 | P2-R | P2-M 新信息 | 裁决 |
|---|---:|---:|---|
| raw net-return stable features | 20/28 | 固定输入，不重选 | 关联结构存在 |
| mechanical attenuation | 未测 | **14/20** | 多数 raw IC 含强尺度成分 |
| scale-robust controls all pass | 未测 | **8/20** | 有小幅稳定残余，仍是 exploratory |
| exact TP / SL barrier scale | 未测 | **+5 / -2 ATR** | target 幅度机械结构确认 |
| global mechanical ≥75% | 未测 | **FALSE（70%）** | 不降低预注册门 |
| any scale-robust feature | 未测 | **TRUE（8）** | 不选择 feature、不训练 |

P2 的经济失败仍是控制结论：test-row 加权 AUC 0.5117；fold-local exact top-decile gross
-0.91bp、taker-net -10.91bp、pressure-net -15.91bp、TP-before-SL 22.51%；single-feature
fixed baseline -22.67bp；matched candidate lift +0.74bp、exact p=0.4836。P2-M 没有新模型、
新 score 或新 matched selection，因此没有新的 AUC / top-decile / permutation 数字，也不能
用机制关联覆盖这些失败经济门。

## 数据、方法与安全边界

| 项 | 值 |
|---|---:|
| dataset SHA256 | `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a` |
| rows / symbols | 18,103 / 230 |
| signal range | 2026-02-01 01:00 → 2026-05-03 05:15 UTC |
| max label interval end | 2026-05-03 22:45 UTC |
| holdout signal / interval rows | 0 / 0 |
| derived target missing / non-finite / non-positive scale | 0 / 0 / 0 |
| fold test rows | 2,937 / 2,918 / 2,996 / 2,944 / 3,000 |
| training / fitting calls | 0 |

方法固定为：每个 frozen test fold 分别计算 Spearman；ATR 桶使用 outcome-blind fold-local
`atr_pct` qcut 五分位；桶内 rho 以桶 rows 加权；stable 需要五折均 finite、至少 4/5 同号、
中位绝对值≥0.03。没有 p-value 多重扫描、没有模型拟合、没有 threshold 或 feature selection。

报告不画趋势图：只有五个预注册折，且 20×4 机制结果需要精确查值；表格和机器 CSV 比连线
更不容易暗示连续趋势或突出事后最优 feature。

## 风险、限制与诚实声明

- `atr_normalized_gross` 只移除 payout magnitude，不移除 ATR 对 TP 概率或 timeout path 的影响；
- ATR quintile 是粗控制，桶内仍保留连续 ATR；`atr_pct` 自身的 within-bucket rho 尤其不能
  解释为已排除波动；
- 多个 feature 与 ATR 高度共线，Spearman 不是独立贡献或因果效应；
- 8 个 scale-robust feature 是对已查看 P1 的全表审计，不是可直接进入模型的 shortlist；
- P2-M 没有逐行 score，不能产生新的 selector、top-decile 或匹配对照；
- funding 仍未建模；holdout 消耗 0；没有训练、调 threshold、ACTIVE/bundle、部署或订单。

## 下一步与未解决问题

**本轮动作：无；立即停止。** P2 remains rejected，`training_allowed=false`，
`threshold_change_supported=false`。

若 Owner 将来另行授权，下一轮只能先选择一个明确问题，而不是打包搜索：

- target 机制路线：预注册只比较 TP label 或 ATR-normalized target 是否比 raw return 更合理；
- 形态机制路线：预注册一个 feature family 的 within-volatility 解释，不做 28-feature 扫描；
- 确认路线：不再用已查看的 P1 宣称 confirmation，等待预注册后的新鲜前向样本。

以上都不是本轮授权；本轮不自动进入任何一项。

## 测试与产物

- P2-M 专项：7 passed；
- 完整 `tests/`：513 passed、2 skipped、14 warnings、0 failed；
- AST 静态门确认无 estimator `.fit` / `train_regressor` / training call；
- ACTIVE / forward log / ledger hash 不变；active bundle 不存在。

| artifact | SHA256 |
|---|---|
| prereg JSON | `5173f168a45161cea8587d0eb32792bfd263a2cfb4f730160b823bda7353691e` |
| mechanism audit JSON | `ce39a3867645ee0fcd3fe62866200919347b192c0476c48827d4e2ec069881aa` |
| feature mechanism CSV | `7ad203377e25a9f002cba5c482c19d658b94720cf8f0a2afe69f70f2faf55e88` |
| fold target CSV | `a664e4095188414d46257fa9555ededb15dc4fc77584bde5a25649335a7642bc` |

机器测试：`analysis/output/p2m_test_results_20260803.json`；完整 hash 清单：
`analysis/output/p2m_hashes_20260803.sha256`。

## Commit 列表

| commit | 内容 |
|---|---|
| `e8ba9a9` | P2-M 只读机制预注册与固定判定门 |
| `ad3788b` | ATR target decomposition、五折尺度控制、机器 JSON/CSV 与专项测试 |

报告、HTML、full tests、learnings 与 HANDOFF 的关闭提交在本报告之后形成，最终列表以 git log
与交付回复为准。

## 从零复现

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2m-pycache \
  MPLCONFIGDIR=/private/tmp/p2m-mpl \
  .venv/bin/python scripts/audit_p2m_mechanism_20260803.py

PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2m-pycache \
  MPLCONFIGDIR=/private/tmp/p2m-mpl \
  .venv/bin/pytest -q tests/test_p2m_mechanism_audit.py

PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/p2m-pycache \
  MPLCONFIGDIR=/private/tmp/p2m-mpl \
  .venv/bin/pytest -q tests

python3 scripts/md_to_html.py analysis/p2m_readonly_mechanism_audit_20260803.md \
  --out-dir analysis/html
```

## 停止点

**P2-M 已完成，立即停止。** 不选择 feature、不训练、不调 threshold、不读 holdout、不修改
ACTIVE、不创建 active bundle、不部署、不下单。任何后续 target/feature/forward 方案都需要
Owner 新指令与单独预注册。
