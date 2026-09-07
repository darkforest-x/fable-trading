# V30 saved-result review

## 身份、范围与结论

本文件由 `v30_core` 完成。本轮核心统计模块的作者也是 `v30_core`，
因此这是**核心作者的二次只读检查，不是第三方独立审计**。
此前 root 编写了不导入 `yoyo` 的独立重算脚本
`scripts/audit_hourly_impulse_classifier_economics_v30.py`，并生成 `audit.json`。
应区分“独立计算路径”和“独立第三方审核”，不能混为一谈。

结论：保存结果与本次复算一致，四项联合继续门未通过。共振过滤使固定时钟
成本阈值后的结果少亏，但保留组仍亏；不能由正的 `policy_delta` 宣称盈利，
不能改用辅助 horizon 翻案，也不能推进 TradingView/实盘或执行参数优化。

本次只读取已授权的 V24/V29/V30 保存表、源码、元数据及收据。
没有读取 raw/new prices、holdout，没有改参数、删异常值、重抽随机控制，
也没有修改核心或冻结结果。

## 证据身份与审计范围

- V30 source-first builder：`917f33358fcc5f7da1ab0dd85e380a72b7e51a2b`。
- `results/summary.json` SHA256：
  `6bf476890a08f0aa14731cc5bcca03ba1c30c295eb30dab3450b59ef5383d21a`。
- root auditor SHA256：
  `eb53e28e620bdf004e5907f86fef2a9ba36adebea318b05d87967d19c008adef`。
- root 审计收据为 `passed`：3980 个原标签行、1004 个母样本×horizon 行、
  四项主要推断、24 个固定月份簇、9999 次抽样、Holm 调整均已重算。
- root 的 `check_description()` 实际检查计数、均值、和、中位数、分位数和正负零计数；
  它没有逐字段检查所有 SD/min/max。不能把该函数描述成所有描述字段的完整验证。
- 本次另外用标准库 `statistics.stdev` 重算四主要序列的样本 SD，
  并核对 min/max，全部与保存结果一致；另外核对三张输出 ledger 的唯一完整网格。
- 本次还从 case ledger 重新按半年、月份、gate 和 horizon 分组，
  核对报告需要的分母、均值、正负贡献及成本分解。

这些检查验证的是保存身份、保存终点与独立算术，不验证原始行情真实性、
完整盘中路径、原生 Pine 运行 parity 或真实可执行收益。

## 分母与三个未知

原 SMA40 母群为251个，不是251期均线。三张 ledger 均无重复或缺失的
`(event_id, horizon_hours)`：

| 表 | 唯一事件数 | horizon | 唯一行数 |
|---|---:|---|---:|
| case_ledger | 251 | 1/4/12/24 | 1004 |
| control_ledger | 744 | 1/4/12/24 | 2976 |
| mother_ledger | 251 | 1/4/12/24 | 1004 |

4h 的78个 accepted 标签全部已知，其条件对照是**234个原始控制**：
51个自己的 gate 通过，183个不通过。这183个仍保留在条件收益对照中，
不能只取51个通过者，更不能替换成全池136个通过者。

主要 `accepted_cost` / `accepted_excess` 的分母均为78。
`policy_excess` / `policy_delta` 保留原251行，其中248已知、3未知。
这3个是 gate 预热不足、且原本没有配对控制，不是未来标签缺失：

| event_id | 自己的 E（UTC） | 4h标签 | gate | policy |
|---|---|---|---|---|
| 2023-06-25T03:00:00+00:00_L | 2023-06-25 04:00 | known | warmup/unknown | NaN |
| 2023-06-25T11:00:00+00:00_L | 2023-06-25 12:00 | known | warmup/unknown | NaN |
| 2023-06-25T14:00:00+00:00_S | 2023-06-25 15:00 | known | warmup/unknown | NaN |

即使后来能看到这三个标签，仍不能把 gate unknown 改成弃权0。
与之相反，已知 abstain 表示不交易，policy=0；计算相对 baseline 的
`policy_delta` 时仍需 baseline 标签可用。

## 四个主要结果

下表是保存 summary 的 fraction×10000，单位bp，保留15位小数以便复核；
不代表行情或收益具有这样的经济测量精度。均值区间为固定24月簇 bootstrap
95% percentile-linear；p是月和的一侧 sign-flip，包含 plus-one。

| 主要序列 | 已知/原分母 | 均值bp | 95% CI bp | 原p | Holm p |
|---|---:|---:|---|---:|---:|
| accepted_cost | 78/78 | -14.771645951792745 | [-33.908411719183576, 3.940422253169782] | 0.9285 | 0.9285 |
| accepted_excess | 78/78 | 4.652375672789489 | [-16.142074278006579, 25.168389957545561] | 0.3339 | 0.6678 |
| policy_excess | 248/251 | 3.254068745331299 | [-2.450248245629641, 9.013572166596594] | 0.1443 | 0.4329 |
| policy_delta | 248/251 | 14.261384905699108 | [3.006351462499393, 25.575697027898887] | 0.0122 | 0.0488 |

`accepted_cost` 均值为负，前三序列区间均跨零，全部 Holm p 均未达到预设
`<0.01`。虽然 `policy_delta` 的95%区间为正，但其 Holm p=0.0488，
并未达到本轮0.01门槛。不能在看到结果后把门槛改成0.05。

标准库样本 SD 和极值补核结果：

| 主要序列 | SD bp | minimum bp | maximum bp |
|---|---:|---:|---:|
| accepted_cost | 111.813932076209952 | -435.875389224145010 | 263.796021609467971 |
| accepted_excess | 133.638172932134182 | -458.433272011226961 | 388.577903547281323 |
| policy_excess | 69.537149648796728 | -435.875389224145010 | 328.341384757699359 |
| policy_delta | 84.906053910090606 | -341.034475188256010 | 293.546893753213965 |

## 可报告的亏损规律

1. **当前同向不等于之后继续同向。** 78个 accepted 中，4h毛收益为负40个，
   毛正但不够覆盖20bp者3个，净正35个。40/78在固定4h终点已走反，
   所以不能把所有亏损都归因于费用；也不能仅凭终点标签断言“止损被扫”或“利润回吐”。
2. **毛优势太小。** accepted 毛均值仅 `+5.228354048207226 bp`，
   扣固定20bp后为 `-14.771645951792745 bp`；中位净值为负。
3. **不是某一个半年独自拖累。** 四个半年的 accepted 净均值全部为负，
   24个有信号的月份只有9个月均值净正。四半年正均值共同门明确失败。
4. **删掉亏损的同时也删掉赢家。** 170个 abstain 中113个净负、57个净正；
   保留78个中43个净负、35个净正。不能把170个被删入口全部称为坏交易。
5. **相对随机背景稍好不等于赚钱。** 234个未过滤原控制的平均净标签为
   `-19.4240216245821 bp`，所以 accepted 的 `+4.652375672789489 bp`
   配对超额只是相对少亏，且区间跨零，没有证实稳定超额。

| 半年 | accepted数 | 4h净正数 | 4h accepted净均值bp |
|---|---:|---:|---:|
| 2023H1 | 20 | 8 | -5.485001205652897 |
| 2023H2 | 17 | 10 | -3.146759764160822 |
| 2024H1 | 19 | 8 | -18.255013923392998 |
| 2024H2 | 22 | 9 | -29.188553617798856 |

原因分组仅作已预设的描述，不用来选择新参数。67个“仅距离不通过”、
33个“仅斜率不通过”、70个“两项都不通过”的4h净均值分别为
`-18.657418105896028`、`-29.655749177227003`、`-18.687667438140770 bp`。
三组均为负；其中33个“距离达标但斜率不同向”较差，仍不是斜率条件
具有可泛化因果效果的证明，更不能据此改阈值后沿用同一检验结果。

## 改善96.13%来自少扣成本，而不是新增趋势毛收益

同一248个已知 gate 母机会的口径下：

- baseline平均净标签：约 `-18.90730580989199 bp/原机会`。
- filtered policy平均：约 `-4.645920904192879 bp/原机会`。
- 二者相差 `+14.261384905699108 bp/原机会`，但 filtered policy 仍为负。

不要把上面的248分母与全251个 baseline 的 `-18.994374409430439 bp`
直接相减；也不要将每原机会均值当成每实际信号的均值。

已知弃权集合B有170个，成本阈值c=0.002，已知机会数N=248。
accepted 的 policy_delta 恒为0，因此：

```
mean(policy_delta) = -sum(net_i for i in B) / N
                  = [len(B) * c - sum(gross_i for i in B)] / N
```

从保存 ledger 重新计算：

- 少扣20bp阈值贡献：`170 × 20 / 248 = 13.709677419354840 bp`。
- 避免负毛收益贡献：`-sum(gross_B) × 10000 / 248 = 0.551707486344323 bp`。
- 相加约 `14.261384905699163 bp`，与 summary 值的末位浮点差小于
  `1e-10 bp`；这不是统计或经济差异。
- 少扣成本占改善约 `96.13145925173194%`。

所以主要效果是“少交易，少扣成本阈值”，而不是获得了足以覆盖成本的大趋势毛优势。
这只是固定时钟标签的贡献拆账，不是实际账户累计盈利、成交费率测量或资金曲线。

## 辅助horizon不能翻案

| horizon | accepted数 | accepted净均值bp | 角色 |
|---|---:|---:|---|
| 1h | 78 | -17.882498690997359 | 仅描述 |
| 4h | 78 | -14.771645951792745 | 唯一主要时钟 |
| 12h | 78 | -12.364652914160562 | 仅描述 |
| 24h | 78 | -9.794147983038746 | 仅描述 |

四个时钟的保留组平均净标签都负。即使未来某个辅助时钟出现正值，也不能
替换已冻结4h主时钟；这里更不存在“把持仓拉长就已经盈利”的证据。
没有读取止损/部分止盈的盘中路径，因此本轮不能诊断或宣称某个动态退出改善有效。

## 只读复核复现

从仓库根目录执行下列命令；不写任何文件，不读取原始行情。
以下数值复核允许 `rel_tol=1e-12, abs_tol=1e-12` 的fraction算术差，
以容纳CSV往返及浮点求和顺序；不允许修改经济门槛或计数。

```bash
python3 - <<'PY'
from pathlib import Path
import csv, gzip, json, math, statistics

p = Path('experiments/active/exp-btcusdtp-1h-classifier-economics-preholdout-20260907-v30/results')
def load(name):
    with gzip.open(p / (name + '.csv.gz'), 'rt', newline='') as stream:
        return list(csv.DictReader(stream))
c, k, m = (load(name) for name in ('case_ledger', 'control_ledger', 'mother_ledger'))
s = json.loads((p / 'summary.json').read_text())
for name, rows, expected in [('case', c, 251), ('control', k, 744), ('mother', m, 251)]:
    events = {r['event_id'] for r in rows}
    grid = {(r['event_id'], int(r['horizon_hours'])) for r in rows}
    assert len(events) == expected and len(grid) == len(rows) == expected * 4
    assert grid == {(event, h) for event in events for h in (1, 4, 12, 24)}
    print('unique_grid', name, len(grid))
for name in ('accepted_cost', 'accepted_excess', 'policy_excess', 'policy_delta'):
    vals = [float(r[name]) for r in m if r['horizon_hours'] == '4' and r[name] != '']
    row = s['primary'][name]
    for actual, wanted in [(statistics.stdev(vals), row['sd']),
                           (min(vals), row['minimum']), (max(vals), row['maximum'])]:
        assert math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
    print('primary', name, 'mean_bp', row['mean'] * 10000,
          'sd_bp', statistics.stdev(vals) * 10000, 'Holm_p', row['holm_p'])
b = [r for r in c if r['horizon_hours'] == '4' and r['classifier_gate_state'] == 'abstain']
gross_avoided = -math.fsum(float(r['gross_markout']) for r in b) / 248
cost_avoided = len(b) * .002 / 248
assert len(b) == 170
assert math.isclose(gross_avoided + cost_avoided, s['primary']['policy_delta']['mean'],
                    rel_tol=1e-12, abs_tol=1e-12)
print('delta_components_bp', cost_avoided * 10000, gross_avoided * 10000,
      'cost_share', cost_avoided / (cost_avoided + gross_avoided))
for h in (1, 4, 12, 24):
    vals = [float(r['cost_threshold_markout']) for r in c
            if r['horizon_hours'] == str(h) and r['classifier_gate_state'] == 'accepted']
    print('accepted_horizon', h, len(vals), math.fsum(vals) / len(vals) * 10000)
for fold in ('2023H1', '2023H2', '2024H1', '2024H2'):
    vals = [float(r['cost_threshold_markout']) for r in c
            if r['horizon_hours'] == '4' and r['classifier_gate_state'] == 'accepted'
            and r['fold'] == fold]
    print('accepted_half', fold, len(vals), math.fsum(vals) / len(vals) * 10000)
assert s['decision']['exploratory_continue'] is False
assert s['decision']['economic_acceptance'] is False
assert s['decision']['production_eligible'] is False
PY
```

以上代码已从本文件提取并实际只读执行，退出码0，全部断言通过。

## 风险与诚实声明

这是反复使用过的2023–2024开发集。四检验内Holm不修复历史多次实验选择，
月簇法也不自动证明月间独立或符号可交换。方向策略的随机背景对照保留，
但观察性事件没有被随机分配。未证实盈利不等于数学上证明该类过滤器永远无效；
本轮能说的是：**冻结的这一个版本没有达到预先声明的继续条件**。
审计一致性不能升级成价格真实性、成交可执行性或实盘验收证据。
