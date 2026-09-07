# V37 保存账本独立复核

## 结论与使用范围

**可作为带限制的失败报告证据；不能作为盈利或入口优势已证实的证据。**
原 63 笔的平均净收益由 −22.539719917bp 改为 −12.809320352bp，改善
+9.730399565bp，但平均值和 PF 仍为负面结果，且中位数由 −21.971588727bp
恶化为 −29.938654512bp。改善并非多数交易共同改善：13 笔改善、25 笔恶化、
25 笔不变。最大的两笔改善均没有完整匹配对照，合计超过全组总改善。

本复核仅读取以下两个目录的已保存文件，没有读取原始行情、其他历史结果、
2025 年及以后价格、holdout、注册表或 HANDOFF，也没有重跑策略、筛参数或修改执行代码：

- `experiments/active/exp-btcusdtp-owner-k1k2-transition-exit-20260907-v37/`
- `data/owner_k1k2_transition_exit_v37/`

证据为 `summary.json`、`pre_outcome_receipt.json`、`config.json` 和下文列出的 CSV。
所有时钟为 UTC。结果声明的 source commit 为
`457dc62a10ba592a12babae43417300f852c44e0`，summary 时间为
`2026-09-07T09:51:09.216754+00:00`。
这是内部核对记录，不替代主报告 HTML，也不把合成测试通过等同于经济有效。

## 1. 已独立通过的账本检查

- summary 登记的 **13 个输出文件**逐个重算 SHA-256，全部一致。
- pre-outcome receipt 登记的 3 个请求/分配文件 SHA 一致；其记录时间
  `2026-09-07T09:50:54.496802+00:00` 早于 summary，config SHA 与 source commit
  在两份元数据中一致。该检查证明保存元数据的内部一致性，不是独立重演执行先后。
- case 原请求 **63** 行、control 原请求 **108** 行均唯一；两臂保留相同 ID，
  每个保存请求字段与两臂对应交易字段逐值 exact 一致，包括 direction、decision_time、
  stop、ATR、MA、body_ratio、gap_bars、fold 及 control 的 parent_event_id。
- 两臂 entry_time、entry_price、risk_pct、risk_atr 逐值一致；entry_time 等于 decision_time。
  本次 171 个事件的两臂均已闭合，没有拒单或未决交易；所有保存入场/出场时钟均在
  `[2023-01-01, 2025-01-01)`。
- 用保存报价独立核 `gross = direction × (exit_price − entry_price) / entry_price`，
  再核 `net = gross − 0.002`，全部通过；两臂均无分批退出，成本均为原 notional 的
  20bp 一次。总改善来自毛收益变化，不是少扣费用。
- 原 **36** 个 matched parent 各有 **3** 个唯一 control，108 个 control 无缺失；
  独立逐母重算两臂超额及增量，与 `paired_contrasts.csv` 一致。
  另 **27** 个 case 的配对状态为 `insufficient_exact_controls`：它们自己的收益已知，
  但没有配对超额，不能把这一缺失写成 0，也不能将 36 的结果外推为 63 的对照结论。
- 用各臂保存入场/出场时钟独立顺序重建单仓 mask：旧臂选 63，候选选 59、阻塞 4，
  与两个 `*_case_single_position.csv` 一致。候选被阻塞的 4 个独立事件中有 1 赢、3 亏；
  不能沿用旧 mask，也不能将独立事件均值当成账户收益。

注意：这里没有越出白名单重读 V36 原账本。因此“V36 baseline replay parity”是
summary 中保存的运行声明，本复核独立核实的是 **V37 已保存两臂及其请求之间**的
一致性，不冒充再次完成旧 V36 全字段或原始价格回放。

## 2. 原 63 笔与四个半年

以下均为每笔已闭合交易、原始 notional 的净 bp；本次已闭合数等于各组请求数。

| 分组 | n | 旧均值 | 候选均值 | 候选−旧 | 候选赢家 | 候选硬止损 |
|---|---:|---:|---:|---:|---:|---:|
| 全部 | 63 | −22.539720 | −12.809320 | +9.730400 | 10 | 20 |
| 2023H1 | 23 | −23.934483 | −19.442673 | +4.491810 | 2 | 9 |
| 2023H2 | 19 | −18.476976 | +13.233032 | +31.710009 | 5 | 3 |
| 2024H1 | 15 | −30.634994 | −32.366896 | −1.731901 | 2 | 5 |
| 2024H2 | 6 | −9.820296 | −20.954980 | −11.134684 | 1 | 3 |

只有 2023H2 的候选净均值为正；该半年贡献 +602.490164426 event-bp，
占全 63 总改善 +613.015172598 event-bp 的 **98.283075%**。
event-bp 指逐事件 bp 之和，不是复利账户收益。

候选总体毛均值 +7.190679648bp，低于固定 20bp 成本；净 PF
0.544555693，胜率 10/63 = 15.873016%，持仓中位数 5→35 分钟。
53 笔净亏损可互斥分为 **39 笔毛亏损 +14 笔毛正但被费用翻负**，没有毛收益恰好为 0
或净收益恰好为 0 的事件。硬止损由 5→20：旧 5 笔仍为硬止损，另 15 笔原 colour exit
延长后成为硬止损。5 笔旧亏损转赢，1 笔旧赢家转亏，另外 5 笔旧赢家仍赢。

候选重算单仓的 59 笔净均值为 −13.292582037bp、PF 0.533618742；
总事件净 bp 为 −784.262340164。单仓结论同样不支持盈利。

## 3. 36 笔匹配组与 27 笔未匹配组不能混为一个已验证结论

| case 组 | n | 旧净均值 bp | 候选净均值 bp | 均值改善 bp | 总改善 event-bp | 候选赢家 |
|---|---:|---:|---:|---:|---:|---:|
| 有完整原三控 | 36 | −22.532472298 | −23.615583054 | **−1.083110757** | −38.991987245 | 7 |
| 缺完整对照 | 27 | −22.549383410 | +1.599029918 | **+24.148413328** | +652.007159843 | 3 |

27 笔未匹配组的中位数仍为 −22.115090432bp，24 笔净亏损。
这不是“未匹配”可作为盈利过滤条件的证据；匹配状态是支持属性，且该组没有相应对照。
原始匹配覆盖仅 **36/63 = 57.142857%**。

同 36 母样本及其 108 控制：

- 母样本自身 D = **−1.083110757bp**；控制 D = **−3.147967949bp**。
- I = 母 D − control D = **+2.064857192bp**。小幅正 I 来自控制恶化更多，
  不代表这 36 笔自身赚钱或自身得到改善。
- 旧绝对匹配超额 −3.500415788bp，候选仍为 **−1.435558596bp**。

| 匹配母样本半年 | n | 母自身 D bp | control D bp | I bp |
|---|---:|---:|---:|---:|
| 2023H1 | 16 | −10.469721 | +0.821734 | −11.291455 |
| 2023H2 | 12 | +15.375428 | −2.539219 | +17.914647 |
| 2024H1 | 6 | −8.335714 | −14.291520 | +5.955805 |
| 2024H2 | 2 | −2.983654 | −5.127425 | +2.143772 |

保存的 24 月簇推断给出 I 的 95% CI **[−7.826763982, +10.605148486]bp**、
单侧 p **0.3697**，36 组只覆盖 17 个活跃月；候选绝对超额 CI
[−19.304375626, +14.506950363]bp，p 0.5495。
本次独立重算了点估计与逐母三控算术，**没有重新抽 bootstrap 或 sign-flip**；
这些 CI/p 明确来自保存 summary。原研究门失败，不能把 p/CI 写成确认级证据。

## 4. 改善是否主要来自少数赢家

答案是“是”，但这里的排序仅用于事后集中度诊断，不形成新的样本选择或策略。

- 13 个改善事件合计 +1084.641848393 event-bp；25 个恶化事件合计
  −471.626675795；抵消后 +613.015172598。
- 最大一笔改善 +419.882620010bp，占总净改善 68.494654%。
- 最大两笔合计 **+732.256843731bp（119.451667%）**，且两笔均未匹配。
  其余 61 笔改善均值为 **−1.954781494bp**。这只是敏感性描述，不是删除异常值后的主结果。
- 最大三笔合计 +824.427173402bp；其余 60 笔改善均值为 −3.523533347bp。
- 候选最终 10 个赢家合计贡献 +993.317973563 event-bp 改善；最终 53 个亏损事件
  合计反而恶化 −380.302800965 event-bp。按未来赢家分组仅解释账务，不能用于入场。

前三笔改善的审计定位（旧/新收益均为净 bp）：

| UTC 入场 | event_id 前缀（唯一） | 是否匹配 | 旧净 bp | 候选净 bp | 改善 bp | 候选持仓分钟 |
|---|---|---|---:|---:|---:|---:|
| 2023-11-15 15:00 | `k1k2_04b4020652275e33` | 否 | +5.026300 | +424.908920 | +419.882620 | 580 |
| 2023-06-23 14:00 | `k1k2_742e1137b8fcf017` | 否 | −24.124630 | +288.249593 | +312.374224 | 270 |
| 2023-12-07 16:00 | `k1k2_a1ab3fe7730814cc` | 是 | −31.630037 | +60.540293 | +92.170330 | 305 |

这些事件在 `case_changes.csv`、`transition_case_trades.csv` 均可按前缀唯一定位。

## 5. 入场时可知与事后才知必须分开

保存的 request 字段包括 direction、decision_time、gap_bars、initial_stop、signal_atr、
ma、body_ratio、fold。entry_price 及由它计算的风险只在真实入场 open 时可知，
不能提前冒称信号小时特征。

本次所有 63 条 `transition_initial_open_time +5min == entry_time`，
initial side 均为 ±1，state 与 side/direction 的对应完全一致；initial reason 全为 valid，
reset_count 全为 0。因此保存账本所声明的 **入场前最后完整 native5 初态**有精确时钟，
不需要读取入场后价格即可作为初态诊断。不过本复核没有从 raw5 重算 SMA/HL2，
这里只验证保存初态的时钟与映射，不独立证明其原始价格计算。

| 入场已知初态 | n | 候选净均值 bp | 候选赢家/亏损 | 新硬止损 | 相对旧臂改善/恶化/不变 |
|---|---:|---:|---|---:|---|
| aligned | 16 | −21.646676370 | 2 /14 | 2 | 0 /0 /16 |
| opposite | 47 | −9.800858729 | 8 /39 | 18 | 13 /25 /9 |

这能说明入场时已有 **47/63** 与持仓方向反色，旧 colour-exit 在该条件下存在“反色
状态延续被当作退出”的机制冲突；不能说明这些 47 笔应全部被拒绝或可以提前辨认赢家。
aligned 组同样仍亏，opposite 组包含最大两笔赢家。直接从这次结果增加初态过滤会是
新假设，必须另行冻结，不能在报告中悄悄变成已验证改进。

原 `color_exit_without_new_flip` 的 **38** 笔是“入场反色 + 首持仓 bar 仍反色 +
首 5 分钟实际 colour exit”的组合，包含入场后的完整 bar 和旧退出结果：**这是事后标签**。
恰好这 38 笔全部发生变化（13 好、25 坏），其他 25 笔逐事件净收益不变。
它能定位改动作用的路径，不能直接成为新的 entry feature。

同理，“最终曾激活”“最终未激活”“MFE”“最终变色退出”等都是持仓未来标签：

- 17 笔最终从未激活，全部硬止损；候选净均值 −47.476444048bp，旧均值
  −28.618532211bp，平均恶化 −18.857911837bp。这 17 笔都来自入场 opposite 的 47 笔。
- 另外 30 笔初态 opposite 后来激活，再加 16 笔初态 aligned，共 46 笔曾激活；
  这组候选净均值仅 +0.002442753bp，36 亏/10 赢。不能将这点近零的事后组均值说成
  “事前只抓激活趋势即可盈利”。
- 多头 31 笔候选净均值 −3.796342615bp，空头 32 笔 −21.540642534bp；方向是入场已知，
  但两组仍负，也没有据此进行参数选择或提出只做某方向的回测结论。

可在入场前知道的失败风险是“当前尚不同向、并未预先获得趋势确认”，
而不是“此后一定不会激活”。本轮保存证据不足以提前区分这 47 笔中的 17 个未激活止损、
30 个后来激活，以及其后的赢家/亏损。

## 6. 复算方法与可复现命令

运行于仓库根目录，使用现有 `.venv`。仅读取本轮保存输出，不导入策略或特征模块，
不触发任何行情读取。bp = return×10000；总贡献 = 逐事件 bp 之和；PF = 正净收益和 /
负净收益绝对值和；所有分组先按 event_id 一对一联结。未匹配 case 保留自身 markout，
不伪造控制均值；以下命令可复算关键表、集中度、配对点估计与单仓选择。

```bash
.venv/bin/python -B - <<'PY'
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd

E = Path('experiments/active/exp-btcusdtp-owner-k1k2-transition-exit-20260907-v37')
D = Path('data/owner_k1k2_transition_exit_v37')
s = json.loads((E/'summary.json').read_text())
for name, meta in s['files'].items():
    p = Path(name)
    assert p.parent == D
    assert hashlib.sha256(p.read_bytes()).hexdigest() == meta['sha256']
def read(name):
    return pd.read_csv(D/(name+'.csv'), float_precision='round_trip')

a, b = read('state_case_trades'), read('transition_case_trades')
ca, cb = read('state_control_trades'), read('transition_control_trades')
assignments = read('assignments')
for cohort, old, new in [('case',a,b), ('control',ca,cb)]:
    req = read(cohort+'_requests').set_index('event_id').sort_index()
    for f in [old, new]:
        assert f.event_id.is_unique and f.closed.all()
        indexed = f.set_index('event_id').sort_index()
        pd.testing.assert_frame_equal(req, indexed[req.columns],
                                      check_dtype=False, check_exact=True)
        assert np.allclose(f.gross_return-f.net_return, .002, rtol=0, atol=1e-15)
        assert np.allclose(f.direction*(f.exit_price-f.entry_price)/f.entry_price,
                           f.gross_return, rtol=0, atol=1e-14)
    keys = ['entry_time','entry_price','risk_pct','risk_atr']
    pd.testing.assert_frame_equal(old.set_index('event_id')[keys].sort_index(),
                                  new.set_index('event_id')[keys].sort_index(),
                                  check_exact=True)

m = b.merge(a[['event_id','net_return']], on='event_id',
            suffixes=('','_old'), validate='one_to_one')
m = m.merge(assignments[['event_id','match_status']], on='event_id', validate='one_to_one')
m['D_bp'] = (m.net_return-m.net_return_old)*10000
for key in ['fold','match_status','transition_initial_state','direction']:
    print(key, m.groupby(key).agg(n=('event_id','size'),
          old_mean=('net_return_old',lambda x:x.mean()*10000),
          new_mean=('net_return',lambda x:x.mean()*10000),
          mean_D=('D_bp','mean'), sum_D=('D_bp','sum')).to_string())
ordered = m.sort_values('D_bp',ascending=False)
print('top2_total_D', ordered.D_bp.head(2).sum(), 'all_D', m.D_bp.sum(),
      'remaining61_mean_D', ordered.D_bp.iloc[2:].mean())
print(ordered[['event_id','decision_time','D_bp','match_status']].head(3).to_string(index=False))
print('loss decomposition', int(b.gross_return.lt(0).sum()),
      int((b.gross_return.gt(0)&b.net_return.le(0)).sum()), int(b.net_return.gt(0).sum()))

ctrl = cb[['event_id','net_return']].merge(ca[['event_id','net_return']],
          on='event_id',suffixes=('','_old'),validate='one_to_one')
ctrl = ctrl.merge(read('control_requests')[['event_id','parent_event_id']],
                  on='event_id',validate='one_to_one')
assert ctrl.groupby('parent_event_id').size().eq(3).all()
cm = ctrl.groupby('parent_event_id')[['net_return','net_return_old']].mean()
q = m.loc[m.match_status.eq('matched')].set_index('event_id').join(cm,rsuffix='_control')
q['state_excess_net'] = q.net_return_old-q.net_return_old_control
q['transition_excess_net'] = q.net_return-q.net_return_control
q['delta_excess_net'] = q.transition_excess_net-q.state_excess_net
p = read('paired_contrasts').set_index('event_id')
assert len(q)==36 and set(q.index)==set(p.index) and p.complete_pair.all()
for k in ['state_excess_net','transition_excess_net','delta_excess_net']:
    assert np.allclose(q[k],p.loc[q.index,k],rtol=0,atol=1e-15)
    print(k,len(q),q[k].mean()*10000)

for arm in ['state','transition']:
    t = read(arm+'_case_trades')
    t['E'] = pd.to_datetime(t.entry_time,utc=True)
    t['X'] = pd.to_datetime(t.exit_time,utc=True)
    assert t.closed.all() and t.E.notna().all() and t.X.notna().all() and t.E.is_unique
    busy, chosen = None, []
    for row in t.sort_values(['E','event_id']).itertuples():
        if busy is None or row.E >= busy:
            chosen.append(row.event_id)
            busy = row.X
    saved = read(arm+'_case_single_position')
    assert np.array_equal(saved.event_id.isin(chosen),saved.portfolio_selected)
    print('single_position',arm,len(chosen),t.loc[t.event_id.isin(chosen),'net_return'].mean()*10000)
print('saved-ledger checks passed; no raw replay, SMA reconstruction or p/CI resampling')
PY
```

本次实际执行的检查还包括 initial side/clock 映射、171 条事件时钟授权范围、receipt
时间/hash 一致性，以及上述各分组的 PF、胜率、毛亏/费用翻负和总贡献计数。
未验证范围：原始 raw5 聚合和 SMA40 计算、真实成交可达性/滑点/资金费、外部独立样本、
原 V36 原始数据重放、簇 p/CI 再抽样、HTML 视觉质量。没有进行新的可盈利子群搜索。
