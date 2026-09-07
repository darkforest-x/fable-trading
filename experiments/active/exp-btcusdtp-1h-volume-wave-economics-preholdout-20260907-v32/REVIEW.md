# V32 保存结果二次核对

## 结论与身份

2026-09-07：本次检查未发现分母、配对、政策收益或最终主门决策不一致。冻结方案的研究结论为 `not_supported`；不构成盈利验收、可执行回测或部署许可。

本记录由 V32 核心实现作者 `/root/v30_core` 二次复核保存产物完成，**不是第三方独立盲审**。另有 root 编写并运行的独立代码路径审计 `scripts/audit_hourly_impulse_volume_wave_economics_v32.py`，其 `audit.json` 为 `passed`。本次补核采用 Python 标准库 `csv/gzip/math/statistics`，不调用核心经济计算函数；下列 CI 和 p 值来自已通过独立脚本复算的冻结 summary，本次没有重复 Monte Carlo 实验。

使用数据验证技能进行分母、算术和结论核查。范围仅为本轮已获授权的 2023–2024 保存结果，没有读取 raw 行情、新价格或 holdout，也没有修改参数、原有代码或结果文件。

## 冻结收据

- Builder：`4cbcc93853993d85b056ecff8b718499b2fbe6de`。
- `results/summary.json` SHA256：`4efa653ecd4d1da79544c4574dd510ed9705784df8bdb7923811433c8b90d76e`。
- `audit.json` SHA256：`ada87bd139ec2d50f0c35e8f7bcab9a6427816caf329a9caabde7e2afdd5fff6`。
- `case_ledger.csv.gz` SHA256：`7606533b23b297011b3432eee57e2eca9d4ce4761dbd0c4a1934df641c135357`。
- `control_ledger.csv.gz` SHA256：`c8732457943aca1846299b11a89c59af92ac08944bb9b6c5c855068958266c2e`。
- `mother_ledger.csv.gz` SHA256：`d45df0c4b2c52c4f178fdcd5ad203f9f097be25a2981bb825b0ca10e493ae898`。

上述三个 ledger 均核对实际字节哈希与 summary 一致。唯一键为 `(event_id, horizon_hours)`：case 与 mother 各 1004 行，即 251 × 4；control 为 2976 行，即 744 × 4。每个事件恰有 1/4/12/24h 四个终点，无重复、无缺周期。

## 主口径：4h、每次执行扣 20bp

收益单位为 bp，`bp = 小数收益 × 10000`；不是本金收益率或资金曲线。原始 251 个母事件未改变；gate 状态为 accepted 100、abstain 148、unknown 3。控制组为 accepted 401、abstain 343、unknown 0。

| 分组 | n | 毛均值 bp | 净均值 bp | 净正 / 净负 |
|---|---:|---:|---:|---:|
| 原始全部母事件 | 251 | 1.00562559056950 | -18.99437440943044 | 93 / 158 |
| accepted | 100 | 1.55497904047638 | -18.44502095952356 | 38 / 62 |
| abstain 的原始未来标签 | 148 | 0.78033956148063 | -19.21966043851930 | 54 / 94 |
| unknown 的原始未来标签 | 3 | -6.19204530460900 | -26.19204530460900 | 1 / 2 |
| accepted 母事件的原始全部对照 | 300 | -0.77734784961750 | -20.77734784961737 | 109 / 191 |

300 个对照来自 100 个 accepted 母事件，每个恰有自身原始 control_slot 0/1/2。它们包含 158 个 accepted 和 142 个 abstain 控制，**没有只取其中 158 个过门控制**。逐母先平均三个原始对照，再做 case-minus-control；在完整等权三配对下，汇总均值也等于 case 总均值减全部 300 控制均值：`-18.44502095952356 - (-20.77734784961737) = +2.33232689009381bp`。

## 四个半年均未覆盖成本

| UTC 半年 | accepted n | 毛均值 bp | 净均值 bp | 净正 / 净负 |
|---|---:|---:|---:|---:|
| 2023H1 | 25 | 0.09801443528056 | -19.90198556471940 | 8 / 17 |
| 2023H2 | 27 | 1.53549243897400 | -18.46450756102589 | 10 / 17 |
| 2024H1 | 23 | 10.38759520179318 | -9.61240479820678 | 10 / 13 |
| 2024H2 | 25 | -5.09301769311668 | -25.09301769311664 | 10 / 15 |

逐事件 `math.fsum(net) / n` 重算，不能把四行简单平均代替 100 笔加权均值。四半年 n 合计 100，净正合计 38。`four_half_accepted_means_positive = false`，而且四行均为负。

## 248 个可决策机会：改善来自少付成本，不是新增毛收益

公平比较基线与过滤政策时，二者都使用 gate 已知的同一 248 个机会。3 个 unknown 保持缺失，不并入弃权零收益，也不拿 251 分母的基线与 248 分母的政策直接相减。

- 同口径原始基线均值：`-18.90730580989199bp`。
- 过滤政策均值：`-7.43750845142079bp`，100 个执行值加 148 个弃权零值，除以 248。
- 政策减基线：`+11.46979735847120bp`，仍是**少亏**。

令 `R` 为 148 个 abstain 事件、`g_i` 为其原始毛收益、`N = 248`、`c = 0.002`。accepted 上的 policy_delta 恒为 0；abstain 上为 `0 - (g_i-c)`。因此：

`mean(policy_delta) = |R|c/N + sum(-g_i for g_i<0)/N - sum(g_i for g_i>0)/N`。

| 对政策改善的贡献 | 每个原始可决策机会 bp |
|---|---:|
| 148 次弃权省下的成本 | +11.93548387096774 |
| 避开被弃权事件的负毛贡献 | +20.35302719427207 |
| 同时错过被弃权事件的正毛贡献 | -20.81871370676858 |
| 毛贡献净变化 | -0.46568651249651 |
| 合计 policy_delta | +11.46979735847120 |

成本项占改善的 `104.060111072081%`，可以超过 100%，因为毛贡献变化为负；并非计算错误。该占比只描述本次固定 20bp 成本假设下的已实现标签分解，不是成本敏感性参数搜索。

净贡献也核对：被弃权的 94 个净负事件贡献 `-6843.847901543698bp` 被避开，同时 54 个净正事件贡献 `+3999.338156642841bp` 被错过；二者之差为 `+2844.509744900857bp` 的事件简单和，再除 248 即 `+11.46979735847120bp`。这些简单和不是账户回报。

对保留信号而言，38 个净正事件总贡献 `+3236.156739371770bp`，62 个净负事件总贡献 `-5080.658835324126bp`，合计 `-1844.502095952356bp`。在 248 个已知机会的 92 个净正事件中只保留 38 个，即 `41.30434782608695%`；156 个净负事件也保留 62 个，即 `39.74358974358974%`。它并未在本样本中表现出强烈的盈亏分离。

## 四个共同主门与分布

| 主序列 | 已知 n / 原始分母 | 均值 bp | 95% 月聚类 CI bp | 原始单侧 p | Holm p | 门 |
|---|---:|---:|---|---:|---:|---|
| accepted_cost | 100 / 100 | -18.44502095952356 | [-44.18941237338018, 6.59509675157721] | 0.9163 | 0.9163 | 失败 |
| accepted_excess | 100 / 100 | 2.33232689009381 | [-26.62678985964243, 31.65445930529410] | 0.4463 | 0.8926 | 失败 |
| policy_excess | 248 / 251 | 5.02422618303016 | [-6.46326429535828, 17.28638294011230] | 0.2227 | 0.6681 | 失败 |
| policy_delta | 248 / 251 | 11.46979735847120 | [0.51645604338144, 21.20945960814484] | 0.0272 | 0.1088 | 失败 |

每门要求均值 > 0、CI 下界 > 0 且 Holm p < 0.01。policy_delta 的 CI 虽为正，但 Holm p 不达标；它本身也不衡量绝对盈利。四个共同门全部失败，再加四半年均值规则失败，summary 的 `all_four_primary_evidence_gates_passed=false`、`exploratory_continue=false`、`status=not_supported` 与独立条件重算一致。

本次还用标准库复算四序列样本 SD 和极值；均与 summary 一致，未删极端值：

| 序列 | 样本 SD bp | 最小 bp | 最大 bp |
|---|---:|---:|---:|
| accepted_cost | 111.68157768058335 | -435.87538922414500 | 326.42457054364996 |
| accepted_excess | 124.33170023780734 | -458.43327201122696 | 481.89324099346464 |
| policy_excess | 83.33194161987434 | -480.80612554459200 | 351.95338381549000 |
| policy_delta | 78.17039767102438 | -341.03447518825600 | 293.54689375321396 |

## 未知与辅助周期

三个未知均为 2023H1 的 warmup，不是未来标签缺失：

| event_id | T−2 的连续历史计数 |
|---|---:|
| `2023-06-25T03:00:00+00:00_L` | 51 |
| `2023-06-25T11:00:00+00:00_L` | 59 |
| `2023-06-25T14:00:00+00:00_S` | 62 |

三个计数均未达到冻结的 100；它们的 policy、policy_excess、policy_delta 保持缺失，原始事件及未来诊断标签仍保留。

accepted 100 个事件在 1/4/12/24h 的净均值分别为 `-17.21096058328041 / -18.44502095952356 / -22.10227436547705 / -14.66305217106460bp`。1/12/24h 仅描述，不能改选 24h 或其它终点翻案。它们不是均线动态退出的回测。

## 失败模式解释与边界

冻结的入场前 volume-wave 改善条件没有在本组 BTC 1h 事件里产生足以覆盖 20bp 成本的 4h 方向收益：保留事件平均毛收益只有约 1.55bp，原始控制约 -0.78bp，净优势差异的不确定区间很宽。盈亏保留率接近，说明本次过滤对未来盈利和亏损的区分都很有限；少开 148 次显著减少成本暴露，却同时移除了略为正的毛收益贡献，导致政策结果只是少亏。以上是冻结条件和保存终点标签的证据，**不是**证明入场逻辑永远无效，也不能归因于假突破、盘整、止损过紧或动态止盈失效：本轮没有重放这些路径、退出或执行规则。

CI/p 来自反复使用过的开发期内探索性检验；Holm 只校正本轮四个预注册主序列，不覆盖此前大量实验。无独立 holdout、资金曲线、滑点资金费或订单执行验证，不能称作可交易盈利结果。

## 复现补核

从仓库根目录执行以下只读标准库核查，可重算四半年、300 原始控制、248 机会分解、所有政策逐行恒等式与最终门；不会读任何 raw/new price 文件或重做随机抽样：

```bash
python3 - <<'PY'
import csv, gzip, json, math
from pathlib import Path
from collections import defaultdict
p = Path('experiments/active/exp-btcusdtp-1h-volume-wave-economics-preholdout-20260907-v32/results')
def read(name):
    with gzip.open(p / (name + '_ledger.csv.gz'), 'rt') as f:
        return list(csv.DictReader(f))
def value(row, key):
    return float(row[key]) if row[key] else math.nan
def mean(values):
    return math.fsum(values) / len(values)
def same(a, b):
    assert (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
def policy(row):
    state = row['wave_gate_state']
    return 0.0 if state == 'abstain' else value(row, 'cost_threshold_markout') if state == 'accepted' else math.nan
cases, controls, mothers = read('case'), read('control'), read('mother')
for rows, size in [(cases, 1004), (controls, 2976), (mothers, 1004)]:
    assert len(rows) == size == len({(r['event_id'], r['horizon_hours']) for r in rows})
case_map = {(r['mother_id'], r['horizon_hours']): r for r in cases}
control_map = defaultdict(list)
for r in controls:
    control_map[r['mother_id'], r['horizon_hours']].append(r)
for r in cases + controls:
    same(policy(r), value(r, 'policy_cost_threshold_markout'))
    same(value(r, 'gross_markout') - .002, value(r, 'cost_threshold_markout'))
for r in mothers:
    key = (r['mother_id'], r['horizon_hours'])
    c, cs = case_map[key], control_map[key]
    if cs:
        assert len(cs) == 3 and {x['control_slot'] for x in cs} == {'0', '1', '2'}
    cn = mean([value(x, 'cost_threshold_markout') for x in cs]) if cs else math.nan
    cp = mean([policy(x) for x in cs]) if cs else math.nan
    net, paid, accepted = value(c, 'cost_threshold_markout'), policy(c), c['wave_gate_state'] == 'accepted'
    expected = dict(case_policy=paid, control_policy_mean=cp,
        accepted_cost=net if accepted else math.nan,
        accepted_excess=net-cn if accepted else math.nan,
        policy_excess=paid-cp, policy_delta=paid-net)
    for key, expected_value in expected.items():
        same(value(r, key), expected_value)
c4 = [r for r in cases if r['horizon_hours'] == '4']
a = [r for r in c4 if r['wave_gate_state'] == 'accepted']
b = [r for r in c4 if r['wave_gate_state'] == 'abstain']
assert (len(a), len(b)) == (100, 148)
half_means = []
for half in ['2023H1', '2023H2', '2024H1', '2024H2']:
    rows = [r for r in a if r['fold'] == half]
    m = mean([value(r, 'cost_threshold_markout') for r in rows])
    half_means.append(m)
    print('half', half, len(rows), m * 10000)
ids = {r['mother_id'] for r in a}
cs = [r for r in controls if r['horizon_hours'] == '4' and r['mother_id'] in ids]
assert len(cs) == 300
print('original300', mean([value(r, 'cost_threshold_markout') for r in cs]) * 10000)
n = len(a + b)
baseline = mean([value(r, 'cost_threshold_markout') for r in a + b])
filtered = mean([policy(r) for r in a + b])
cost = len(b) * .002 / n
avoided = -math.fsum(value(r, 'gross_markout') for r in b if value(r, 'gross_markout') < 0) / n
missed = math.fsum(value(r, 'gross_markout') for r in b if value(r, 'gross_markout') > 0) / n
same(filtered - baseline, cost + avoided - missed)
print('baseline/policy/delta/cost/avoided/missed bp', *[x*10000 for x in [baseline, filtered, filtered-baseline, cost, avoided, missed]])
print('cost_share_pct', cost / (filtered-baseline) * 100)
s = json.loads((p / 'summary.json').read_text())
gates = [q['mean'] > 0 and q['monthly_cluster']['ci95'][0] > 0 and q['holm_p'] < .01 for q in s['primary'].values()]
assert s['decision']['all_four_primary_evidence_gates_passed'] == all(gates)
assert s['decision']['four_half_accepted_means_positive'] == all(x > 0 for x in half_means)
assert s['decision']['exploratory_continue'] == (all(gates) and all(x > 0 for x in half_means))
print('decision', s['decision'])
PY
```
