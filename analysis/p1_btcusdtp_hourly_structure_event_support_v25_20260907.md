# Structure Event Support

BTCUSDT.P · 1h K1结构事件支持审计 V25 · 2026-09-07

## 结论：仅9笔，不进入收益调参

冻结的251个原始K1入口中，仅9笔（3.59%）在自身K1本根首次建立或翻转同向结构。
四个半年分别1、2、4、2笔，只有8个活跃月；四项事前支持门全部失败。
本轮结论是 **insufficient_support_no_outcomes（支持不足，未评估收益）**，
不是“已证明亏损”，更不是可部署盈利策略。

250/251入口可判断、744/744控制可判断，因此主要原因不是数据缺失，而是事件定义
与同根K1同时成立过于稀疏。9笔全部有完整原三控，但样本完整不等于样本足够。
本轮没有读取这道新条件的收益，不用这9笔结果决定pivot、均线长度或样本下限。
整体赚钱目标仍未达到；TradingView、实盘、ACTIVE、frozen和训练均未改变。

## 问题、唯一变化与当前规则

本轮保留原K1产生方式、MA40、止损和20bp往返成本合同，不增加退出或改资金管理。
只检验原V20“已经处于同向结构状态”变为“当前K1本根发生方向事件”是否有继续研究的支持。
这是旧结构家族的另一个问题，不是全新独立共振，也不是所有同向BOS突破。

1. 每请求用自己的方向、signal_time和decision_time；E=signal_time+1h。
2. 仅连接open_time恰好等于signal_time的完整小时，available_at必须等于E。
3. 10左/10右、21根完整小时确认pivot；中心开盘时间+11h才可用。等高等低按冻结Python近似。
4. 维持原稳定价格检查、缺小时状态重置；同向state已经存在时，再次同向突破不产生事件。
5. 原结构未知或缺自身小时为unknown；known内，本根break且break方向等于自身方向才accepted，其他abstain。
6. 所有251 case与744原随机control保留；控制在自己的时钟判断，不继承母单门、不补抽。

accepted只表示符合这一形态门；abstain是已知但不符合；unknown是不具备可判断上下文。
本CLI无经济计算入口，支持通过也必须另预注册收益阶段。

## 与上一阶段同表对照

| 项目 | V24 固定入口持续性（已完成） | V25 当前结构事件（本轮） |
|---|---|---|
| 原case / control | 251 / 744 | 251 / 744，成员及自身时钟不变 |
| 原三控支持 / 无三控 | 248 / 3 | 248 / 3，不删原无对照 |
| 问题 | 原入口4h方向持续性是否覆盖20bp并胜背景 | 新事件门是否有足够可观察样本 |
| 主要结果 | 全case平均毛+1.0056bp、减20bp后−18.9944bp | 9笔accepted、241笔abstain、1笔unknown |
| 相对背景 | 配对248超额+4.7403bp，95%CI[−12.3611,+23.8876]，p=.314 | 未读取新门收益；不适用 |
| 四段 / 时间支持 | 四个半年成本阈值均负 | 1/2/4/2笔；8活跃月 |
| 决策 | not_supported，固定时钟也非可执行策略收益 | 支持不足，不进入收益阶段 |

V24数字来自已经交付的 `analysis/html/p1_btcusdtp_hourly_fixed_clock_v24_20260907.html`，
本轮没有再读取它的标签文件。不能把V24整体负数推断为本轮9笔必然亏损。

## 全体、时间与方向分母

| 群体 | 原数 | accepted | abstain | unknown | accepted / 原数 |
|---|---:|---:|---:|---:|---:|
| 全case | 251 | 9 | 241 | 1 | 3.59% |
| case 2023H1 | 55 | 1 | 53 | 1 | 1.82% |
| case 2023H2 | 66 | 2 | 64 | 0 | 3.03% |
| case 2024H1 | 55 | 4 | 51 | 0 | 7.27% |
| case 2024H2 | 75 | 2 | 73 | 0 | 2.67% |
| case 多 | 130 | 2 | 127 | 1 | 1.54% |
| case 空 | 121 | 7 | 114 | 0 | 5.79% |
| 全control | 744 | 3 | 741 | 0 | 0.40% |
| control 2023H1 | 156 | 3 | 153 | 0 | 1.92% |
| control 2023H2 | 198 | 0 | 198 | 0 | 0% |
| control 2024H1 | 165 | 0 | 165 | 0 | 0% |
| control 2024H2 | 225 | 0 | 225 | 0 | 0% |

case/control的事件率差不是收益优势，也不是随机处理分配的因果效果。
control沿用V24的同月、UTC6h、因果波动桶随机背景，K1本身不是被随机分配的。

## 月份支持

每月accepted case（UTC，包含所有零月）：

| 月份 | 2023 | 2024 |
|---|---:|---:|
| 01 | 0 | 1 |
| 02 | 0 | 0 |
| 03 | 0 | 1 |
| 04 | 0 | 2 |
| 05 | 1 | 0 |
| 06 | 0 | 0 |
| 07 | 0 | 0 |
| 08 | 1 | 0 |
| 09 | 0 | 1 |
| 10 | 1 | 1 |
| 11 | 0 | 0 |
| 12 | 0 | 0 |

24个月中16个月没有事件。四半年活跃月数1/2/3/2；没有用control事件填充case活跃月。
图展示完整月份序列，表提供精确查数，均不按后来盈亏筛选。

## 为什么过滤后只剩9笔

同一保存上下文上，旧持久结构门同向137笔、不同向113笔、未知1笔。
137笔同向中，128笔没有本根同向结构事件；严格同根事件仅9笔。
这解释了信号数量骤降：大多数“已处于同向趋势”的入口，不等于“恰在该K1翻转结构”。

9笔的state_before全部是相反方向；本样本里没有首次从未知建立方向的accepted事件。
这只是本次样本事实，不改变通用公式允许首次建立的语义。

唯一unknown：`2023-06-25T03:00:00+00:00_L`，E为2023-06-25 04:00 UTC，
原因`no_confirmed_break`，原本也无三控。该小时有数据，但结构尚未确认，不能改成abstain。
不得用unknown剔除后分母250冒充全251通过率。

## 原三控完整性与事前门

| 覆盖项 | 分子 / 分母 | 比例 |
|---|---:|---:|
| case可观察 | 250 / 251 | 99.60% |
| control可观察 | 744 / 744 | 100% |
| 完整known三组 / 全原母 | 248 / 251 | 98.80% |
| 完整known三组 / 原已匹配母 | 248 / 248 | 100% |
| accepted且完整known / 全accepted | 9 / 9 | 100% |

完整known要求case及原三个control都known，不要求控制也accepted。
9笔accepted的27个control中，1个accepted、26个abstain、0个unknown；原分组均保留。
没有把一个控制代表全组三控，也没有把未交易的控制从对照中删除。

| 事前继续条件 | 要求 | 实际 | 结果 |
|---|---:|---:|---|
| 全case事件数 | ≥80 | 9 | 不通过 |
| 每半年事件数的最小值 | ≥12 | 1 | 不通过 |
| 活跃月份总数 | ≥12 | 8 | 不通过 |
| 每半年活跃月的最小值 | ≥3 | 1 | 不通过 |

这些是事前实务支持屏障，不是正式统计功效保证。四门未通过后，不再计算该门收益、
改80为9、改pivot长度或扩宽事件时间窗以追求通过。registry归类inconclusive，明确只因支持不足。

## 来源、时钟与实施验证

源码、测试、config和计划在 `b1cca1eaebdf563544e0f3a59210fe78831e414a` 提交后才实跑。
运行仅重用SHA冻结V20完整小时trace、V24请求及随机成员、V4原身份与来源元数据。
trace共18,222行，2022-11-30 16:00至2024-12-28 22:00 UTC；评价入口属于2023–2024四个半年。
来源时间范围不是新价格下载，不读取原5m归档、2025+价格或holdout。

先读trace时间列校验UTC、唯一、顺序、hour网格与pre2025，再读保存OHLC/structure。
保留缺小时、按原公式重放状态，逐字段核对保存trace；原251身份与55/66/55/75半年数核对，
请求严格在各fold起点至end−72h之前。结果冻结前再次核输入和源码哈希。

V20历史失败没有被隐藏：首次经济检查发生错误，但特征冻结在失败之前；首次及恢复检查点
指向相同context_frozen SHA。本轮只验此特征来源链，不读取旧经济结果，也不声称恢复标记
独自证明当时完整执行源码。当前重用的两份纯特征源码与原提交一致。

新增130项合成测试，联合冻结结构、会计、固定时钟及层间边界共447项通过。
负对照包括未来pivot、错自身时钟、重复/缺失身份、边界72h、无事件月份、unknown、
原三控破坏及运行中来源变化；这些破坏因果/身份契约的输入必须失败。
独立stdlib审计器48511b4先提交后实际执行：18,222行保存小时结构、995个自身上下文、
62行计数、251母配对及覆盖分母全部重算通过；核8份本轮源、18+12份历史源、13输入和4结果表SHA。
审计没有调用原yoyo计算函数；这仍不是原5m聚合、Pine内建或经济结果的验证。
HTML官方验证与打包通过；仅structural_only，未找到已装Chromium headless-shell，
未完成浏览器/手机布局和来源弹窗交互检查。收据保留在实验目录QA与independent_audit.json。

## 九个事件的可复核身份

下表只列信号K1开盘时刻及方向；实际决定时刻一小时后，不展示未来收益。

| K1时刻（UTC） | 方向 |
|---|---|
| 2023-05-01 01:00 | 空 |
| 2023-08-29 14:00 | 多 |
| 2023-10-06 12:00 | 空 |
| 2024-01-03 11:00 | 空 |
| 2024-03-12 16:00 | 空 |
| 2024-04-01 05:00 | 空 |
| 2024-04-10 19:00 | 多 |
| 2024-09-24 00:00 | 空 |
| 2024-10-07 22:00 | 空 |

完整251/744行及62组计数在实验目录results内，未只保留这9笔。

## 复现与交付文件

本轮目录：`experiments/active/exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25/`。
INPUTS的13份SHA、两份特征SHA与8份运行源收据见config及results/started.json。
结果表SHA由support_frozen与summary双重登记。没有数据源静默替换或新增依赖。

```bash
cd /Users/zhangzc/fable-trading
git show b1cca1eaebdf563544e0f3a59210fe78831e414a:yoyo/evaluation/hourly_impulse_structure_event_research.py
python3 -m pytest -q tests/test_hourly_impulse_structure_event_research.py tests/test_hourly_impulse_structure_event_support.py tests/test_hourly_impulse_structure.py tests/test_hourly_impulse_structure_accounting.py tests/test_hourly_impulse_fixed_clock.py tests/boundaries/test_layer_imports.py
# 首次运行的实际命令；已有results时预期拒绝覆盖，不能删除历史证据重跑。
python3 -m yoyo.evaluation.hourly_impulse_structure_event_research
python3 experiments/active/exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25/audit_saved_support.py --self-test
python3 experiments/active/exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25/audit_saved_support.py
python3 scripts/md_to_html.py analysis/p1_btcusdtp_hourly_structure_event_support_v25_20260907.md --out-dir analysis/html
python3 experiments/active/exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25/build_artifact.py
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25/artifact.json --output analysis/html/p1_btcusdtp_hourly_structure_event_support_v25_20260907.html
```

从零复现实跑需要原始13份固定输入、来源git对象及对应源码版本；当前保存运行已经存在，
再次调用主CLI的正确行为是拒绝覆盖，不是重复计算。独立审计用于验证现存产物。

## 风险与诚实声明

- 本轮非收益实验，未运行分类器或经济标签：AUC、收益置换p、top-decile毛/净收益、胜率、PF和单特征收益基线均不适用；严格负对照是上述破坏输入测试。没有编造这些值。
- 2023–2024已经多次开发使用，不是新独立样本；9笔不能支持稳定盈利推断。没有消耗holdout，任何最终验收需单独记录配置及次数。
- 验证的是保存小时特征一致性；不等于原5m聚合、交易所行情真实性、Pine内建pivot细节或实盘时延parity。
- 往返20bp合同未改，但本轮未计费用或撮合，没有可执行收益。动态止盈、滑点、资金费率和单仓验证不会由支持率替代。
- 双重SHA能捕捉持久文件变化，但不保证探测瞬时替换后恢复；严格并发写需要不可变快照或锁。
- HTML最终视觉与独立审计的实际完成范围以QA.md为准，不把结构验证称为已做手机实测。

## 下一步

该同根事件配置到此停止，不用放松门或挑9笔收益来挽救。继续寻找适配1h的入口机制，
先查重历史单变量研究，再预注册一个明确变化及时间验证路径；不返回V19已停止的纯退出比例扫描。
源码定义与支持分母先于收益选择，由实验设计、数据质量及验证流程落实。
源码版本核验参考 [pandas 2.3 read_csv](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html)。
此研究交付不是赚钱目标验收；具体下一实验边界见同目录NEXT_EXPERIMENT.md。
