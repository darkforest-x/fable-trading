# External Rank Change

## 技术摘要：变化共振仍未筛出更好的交易

**V22拒绝，盈利目标仍未达成。** 本轮将四个外部币的“强弱位置同向”换成“上一完整小时强弱变化同向”，独立对比未过滤的V18，不叠加V21。原251个BTC小时K1机会留下122笔，每笔平均净收益从−16.7690bp降至**−23.0341bp（−0.23034%）**，四个半年度全部亏损。1bp=0.01%；事件收益加总不是账户复利收益率。

关键不是“信号更少了”：规则避开97笔原亏单，也漏掉32笔原盈利单。它省下2580事件bp费用，却丢失1181.1392事件bp毛收益。相对固定匹配随机入场的增量估计I为+0.1758bp，区间跨零，不能认定共振有效。本轮未更新TradingView、未训练、未部署或操作实盘。

## 固定规则与分母：只改是否参加原K1

原小时K1实体严格贯穿SMA40(HL2)，方向与均线侧一致，收盘位置至少0.70；大实体占比至少0.65且全幅至少1ATR，或真实实体吞没且全幅至少0.65ATR。K1收盘后下一原始5分钟开盘入场。本版本是直接K1，不要求等待K2。K1极值硬止损、72小时上限、20bp往返成本不变。

退出复用V18保存路径：完整原生15分钟先处于同向，再出现真实同向→反向翻色才退出；慢周期仍同向时，真实5分钟反色事件在实际执行开盘盈利超过成本可兑现50%；亏损启动失败需两根连续原生5分钟反色确认，第二次执行仍须慢周期同向且新开盘毛收益不超过20bp才全平。这里的颜色是HL2与SMA40的相对侧，不是单根收阳/收阴。原生完整K线和实际执行时钟不变。本轮没有重新模拟原始分钟内成交。

- 固定251个病例、462个各用自身时间的随机对照、154组三对照及97个未匹配机会，不按本次结果重新挑选。
- 交易均值只除以真正执行的交易数；全机会均值保留全部251，放弃记0但不算成交，未知不能填0。
- D是同机会候选净收益减基准净收益，共251对。
- I是在同154匹配组上，病例D减去三个对照的平均D；剩余97个I仍未知。
- 单仓逐方案重新计算，不能沿用未过滤方案的占仓掩码。

四个半年度按时间切分，72小时标签不跨切点；2023—2024数据已反复用于开发，不是新样本外验证。

## 时间覆盖：支持通过，不代表有效

本轮只读取SHA锁定的V21保存小时表，共70,168行、每币17,542行，小时开盘范围2022-12-29至2024-12-28 21:00 UTC，前段用于预热。没有新读取原始5分钟归档或2025年及以后价格。713个自身上下文和62行支持统计先冻结，支持通过后才读取V18的保存收益。

| 时间段 | 原机会 | 获准 | 放弃 | 未知 |
|---|---:|---:|---:|---:|
| 2023上半年 | 55 | 23 | 32 | 0 |
| 2023下半年 | 66 | 37 | 29 | 0 |
| 2024上半年 | 55 | 29 | 26 | 0 |
| 2024下半年 | 75 | 33 | 42 | 0 |
| 合计 | 251 | 122 | 129 | 0 |

122≥80笔、每半年度至少23≥12笔、23≥12个活跃月、每半年度至少5≥3个活跃月，均通过预注册要求。对照291获准、171放弃、0未知，全部按自己的K1时刻计算。下图是支持数量，不是收益；数量通过不能代替统计功效。

## 版本对照：毛收益和净收益同时恶化

| 同原始251机会的方案 | 执行笔数 | 每笔毛bp | 每笔净bp | 胜率 | PF | 全机会均净bp |
|---|---:|---:|---:|---:|---:|---:|
| V18未过滤 | 251 | +3.2310 | −16.7690 | 21.91% | 0.5918 | −16.7690 |
| V21静态位置门 | 96 | −1.3286 | −21.3286 | 13.54% | 0.5126 | −8.1576 |
| V22变化方向门 | 122 | −3.0341 | −23.0341 | 18.85% | 0.4662 | −11.1959 |

V22胜率较V21高，但平均毛/净收益和PF更差，不能单看胜率就升级。V21和V22是各自对原V18的独立过滤，不是先V21再V22。

| 时间段 | 基准笔数 / 每笔净bp | V22笔数 / 每笔净bp | V22赢 / 亏 |
|---|---:|---:|---:|
| 2023上半年 | 55 / −12.1878 | 23 / −23.3206 | 4 / 19 |
| 2023下半年 | 66 / −25.0855 | 37 / −20.5336 | 7 / 30 |
| 2024上半年 | 55 / −19.0844 | 29 / −23.7181 | 8 / 21 |
| 2024下半年 | 75 / −11.1121 | 33 / −25.0370 | 4 / 29 |

四段毛收益也全部为负；只在2023下半年相对基准少亏。做多56笔、13赢43亏，均净−9.7377bp；做空66笔、10赢56亏，均净−34.3159bp。关闭做空也不能从本表推出盈利。

单仓重算：基准250笔均净−16.9703bp，V22为122笔均净−23.0341bp；全251机会口径为−16.9027→−11.1959bp。候选处理251个机会，不是执行251笔。单仓D=+5.7069bp，95%CI[−2.1889,+12.7962]、p=0.0874，仍未通过。

## 对照增量：减少出手不等于新增优势

| 相同154匹配组、全机会口径 | 基准均净bp | V22均净bp | 改变量bp |
|---|---:|---:|---:|
| K1病例 | −16.3418 | −9.0890 | +7.2528 |
| 三随机对照的组均值 | −22.0132 | −14.9362 | +7.0770 |
| 病例减对照的净优势 | +5.6715 | +5.8472 | **+0.1758** |

总体D251=+5.5732bp，95%CI[−2.2801,+12.6209]，单侧月符号翻转p=0.0905。I154=+0.1758bp，95%CI[−10.8010,+10.5092]，p=0.4899。两者都未达到共同要求的p<0.01与区间下界为正。不能用D251减154组对照D，混用分母会制造另一结论。

控制组D=+7.0770bp，95%CI[+1.3452,+11.6090]，p=0.0056，说明同样的参加/放弃规则在随机入场上也能减少亏损，不能把病例总亏损缩小当专属优势。候选绝对匹配优势+5.8472bp的区间也跨零，它更不等于增量I。

匹配覆盖154/251=61.35%，仍低于90%要求，未通过。没有删除97个未匹配病例提高覆盖率。推断固定9999次、seed20260906、日历月bootstrap/符号翻转；未按结果挑选检验。反复试验和月间依赖使其只能作为探索证据，不是多重搜索校正后的确证结论。

## 逐笔失败：启动失败仍占多数，费用不是唯一原因

| 保留后的实际退出类别 | 笔数 | 赢 / 亏 | 净收益合计事件bp | 亏损分解 |
|---|---:|---:|---:|---|
| 5分钟启动失败两根确认 | 75 | 0 / 75 | −3881.0789 | 61笔毛亏，14笔费用翻负 |
| K1极值硬止损 | 12 | 0 / 12 | −906.3051 | 12笔全部毛亏 |
| 15分钟真实反色退出 | 35 | 23 / 12 | +1977.2244 | 7笔毛亏，5笔费用翻负 |
| 合计 | 122 | 23 / 99 | −2810.1595 | 80笔毛亏，19笔费用翻负 |

退出类别用保存的实际episode_status，并按event_id联结SHA锁定的V18详细成交表核对，不是把“请求已发出”误当成交。99亏单中80笔扣费前已亏，不能只怪0.2%费用；75笔快速启动失败也说明这个共振没有解决入场后持续性不足。

28笔后来真正触发盈利半仓的交易有23赢5亏，另94笔没有盈利半仓的交易全部亏损。**这是持仓后发生的结果分组，不能拿“将来会半仓止盈”作为入场过滤。** 它也不能证明把所有失败全平改半平就有效；剩余仓位必须独立回放。

## 错过赢家与费用分解：为何总亏损缩小仍应拒绝

129个放弃机会中，97笔原亏损被避开，节省5046.9708事件bp；32笔原盈利被错过，损失3648.1101事件bp，净改善1398.8608事件bp。另一等价分解是：少支付129×20=2580事件bp费用，同时丢失1181.1392事件bp毛收益。因此改善不是抓到更好的毛收益机会。

原55个赢家只保留23个（41.82%），原196个亏单却保留99个（50.51%）；对赢家的淘汰更重。拒绝组原均净−10.8439bp也为负，不能简单反向使用本规则。

最大的三个被错过赢家分别是2024-12-04 18:00 UTC多头K1、2024-09-06 13:00 UTC空头K1和2024-03-11 06:00 UTC多头K1。前者单笔净+436.9934bp。逐笔身份、方向、外部两时点评分、保留/拒绝及收益差均在case_mechanics.csv.gz，不仅展示截图里的成功交易。全部大赢大亏均留在正式统计，没有删尾部来改善结果。

## 收益增量分布：保留零值、长尾和未知

全251对D有97改善、32恶化、122不变；0是保留原路径。下图是不等宽bp分箱的机会计数，不是概率密度，也不是参数优化依据。I的154个已知值有71改善、69恶化、14不变，另97未知保留。

D中位0、SD68.9368bp、IQR35.7470bp，范围[−436.9934,+183.4160]bp。I中位0、SD77.5503bp、IQR55.5791bp，范围[−364.8221,+239.2140]bp。月均一阶相关分别−0.0970和−0.1742，不能据此断言独立。

实际运行统计分析技能的诊断工具：D的Shapiro W=0.6924、p≈3.76e−21；I的W=0.8201、p≈1.76e−12。IQR标出的31个D、15个I值全部保留。这些描述诊断不满足IID随机样本前提，不能替代原月块推断；没有改用IID t检验或借不正态选择另一门。

## 源码与时钟：变化方向也不等于资金流

基于ChartPrime [Multi Asset Histogram公开源码](https://www.tradingview.com/script/KkoxM97D-Multi-Asset-Histogram-ChartPrime/)，固定ETHUSDT、SOLUSDT、BNBUSDT、XRPUSDT四个外部币，不含BTC。每小时HL2逐一对比此前50根HL2，大于或相等记+1，小于记−1。长度50是固定源码默认，不是最优参数。

自有K1开盘时刻T，分别使用外部开盘T−1h和T−2h的两个完整小时；可用时刻分别是T与T−1h，BTC入场仍是T+1h。每个rank需51连续小时，两个窗口并集52小时。四币任一缺失则整体未知，不做最近值模糊联结、前填或用当前K1小时价格。

唯一门为：方向×（四币当前原始整数rank之和−上一时点之和）>0。整数差除200得到原均值变化，范围[−2,2]；除400的半变化仅用于旧会计接口[-1,1]符号记录，不裁剪、不改变准入边界。零变化是已知弃权；原始八个整数、两个均值和两种尺度均保存，防止因缩放丢失语义。

rank变化同时受到旧窗口成员退出的影响：即使当前价格不变，较高的旧比较值移出窗口，也可能让rank上升。因此它不是直接价格速度、真实资金流或已确认趋势启动。它还可能由少数币的大幅分数变化主导，不等于多数投票。

原山寨日线change5广度、BTC15m的ETH动量和ETH15m的BTC均线共振已做过；本轮只检验这个明确小时rank差分在原K1队列的表现，不能称跨币变化是全新因子。Binance保存小时仅作为外部输入，BTC仍为原OKX保存执行路径，未静默更换执行数据源。

## 复核范围与验收

联合测试530项通过。独立stdlib验证器从保存小时OHLC重新计算HL2、rank50和两时点差分，核对70,168小时、713自身上下文、62支持行、154匹配组/97未匹配、24个已提交源文件、23个输出CSV哈希和冻结顺序；实际验证通过。

另有只读保存账本复核：122/291获准病例/控制与本轮基准旧字段完全相同；129/171放弃无执行、无费用、0收益并释放自身决策时刻；四臂单仓掩码和40行×17个指标独立重算。重解析原V18 gzip时，episode_net_return有小于1e−16的CSV往返差，满足冻结1e−12数值契约，不能夸称所有数值与原V18逐bit相同。

范围限制：保存小时重算不等于原始5分钟聚合复核、源真实性、Pine实时运行、实时馈送延迟或分钟内经济独立回放；会计审阅未独立重算p/CI。没有训练或拟合排名模型，val AUC、模型top-decile毛/净收益不适用；单变量门本身是本轮特征基线，严格对照是原未过滤策略和同币、同时间块/方向/波动条件的固定随机入场，不编造ML分数。

盈利净值、PF>1.1、四段稳定正收益、匹配90%及共同D/I门都未通过。支持数量和测试通过不能代替这些条件。

## 风险与诚实声明

1. V22仍亏，四段毛净均负；不能称最优参数或可实盘盈利。
2. 开发数据反复使用，p/CI未涵盖整个研究搜索家族；无独立时间验收。
3. 四币相关且为事前固定的主流存活面板，不是四份独立证据，不能外推到全山寨或黄金。
4. 没有新读取2025+价格，holdout消耗0；原手续费、硬止损、时间切分和退出未变。
5. “盈利半仓组较好”是结果选择，不允许未来特征回填；本轮只确定失败门不采用，尚未证明下一方案有效。
6. 本地HTML采用官方便携报告流程，16块、2个原生图；官方validation/package通过，verification为structural_only。本机未安装可用无头Chromium，浏览器、手机视口和来源弹窗未验证，没有为此安装浏览器或绕过预览限制。没有改动TradingView或实盘。

## 下一步：停止堆叠共振，验证一次已准备的风险半仓方案

V20结构门、V21静态外部位置门、V22外部变化门都没有证明保留交易盈利，不再沿着同一批数据机械扫描rank周期、阈值或币组合。下一项回到已准备但未跑的V19：保持两根失败确认，只将确认后的100%全平改为50%风险减仓，剩余仓位依旧跟随原15分钟颜色/K1硬止损/72小时退出。

这是亏损时减风险，不是止盈，也不是承诺能救回大趋势。必须先核对并提交原有准备代码，再真实回放双腿、费用、同开盘优先级、未知余仓和各自单仓掩码，不能平均V16/V18整笔收益拼结果。其已写明的停止规则继续有效：若仍非正收益或共同D/I失败，停止这一队列的纯退出微调，改做退出独立时钟的入场持续性审计，并解决对照覆盖问题。整体盈利目标仍未完成。

## 复现命令与证据

策略builder/config/plan在f5a0e50先提交，之后首次运行完成；不是反复改参重跑。诊断/报告builder在c84f12e先提交，独立审计器在8752fea先提交后执行。以下研究和诊断命令拒绝覆盖既有产物，应在获准且保留旧证据的干净环境重建，不删除原结果。已有结果用不带--out的独立验证命令只读检查。

```bash
git show f5a0e50:experiments/active/exp-btcusdtp-1h-external-change-preholdout-20260907-v22/config.json
.venv/bin/python -m pytest tests/test_hourly_impulse_breadth_change.py tests/test_hourly_impulse_breadth_change_research.py tests/test_hourly_impulse_breadth.py tests/test_hourly_impulse_breadth_accounting.py tests/test_hourly_impulse_breadth_research.py tests/test_verify_hourly_impulse_breadth_v21.py tests/test_verify_hourly_impulse_breadth_change_v22.py tests/test_hourly_impulse_structure_accounting.py tests/boundaries/test_layer_imports.py tests/contracts/test_registries.py -q --tb=short
.venv/bin/python -m yoyo.evaluation.hourly_impulse_breadth_change_research
.venv/bin/python scripts/verify_hourly_impulse_breadth_change_v22.py
MPLBACKEND=Agg python3 scripts/diagnose_hourly_impulse_breadth_change_v22.py
python3 scripts/md_to_html.py analysis/p1_btcusdtp_hourly_breadth_change_v22_20260907.md --out-dir analysis/html
.venv/bin/python experiments/active/exp-btcusdtp-1h-external-change-preholdout-20260907-v22/build_artifact.py
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-1h-external-change-preholdout-20260907-v22/artifact.json --output analysis/html/p1_btcusdtp_hourly_breadth_change_v22_20260907.html
```

本机固定输入包括V4请求/匹配、V21保存小时、V18四张冻结输出。父小时SHA为870e898c0db830ad7c724bb93726f89b6842e6eb7462b3eac1c56bba03e853e6；父pre-outcome冻结SHA为bae01b79e34a0782598e18a9197db1853492fe6f04cb92d0b992fb4015700403。额外退出类别核对的V18详细case_trades.csv.gz先校验SHA f8b5009e58b5f098004c7ea5c3e8a65cfe5be135459f3f0b55f3fae83f903b9e。来源与输出哈希、时间、门和逐机会差异均在本实验results下，风险与负面结果完整保留。

以下补充命令只读保存账本，可复核本报告的退出、费用和半仓分解；它不是上述完整独立时钟验证器的替代品：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
R = Path('experiments/active/exp-btcusdtp-1h-external-change-preholdout-20260907-v22/results')
P = Path('experiments/active/exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18/results/candidate/case_trades.csv.gz')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for name, expected in json.loads((R/'summary.json').read_text())['output_hashes'].items():
    assert sha(R/name) == expected
assert sha(P) == 'f8b5009e58b5f098004c7ea5c3e8a65cfe5be135459f3f0b55f3fae83f903b9e'
read = lambda p: pd.read_csv(p, float_precision='round_trip').set_index('event_id')
old, new, trades = read(R/'baseline_case_episodes.csv.gz'), read(R/'candidate_case_episodes.csv.gz'), read(P)
accepted, abstain = new.breadth_gate_state.eq('accepted'), new.breadth_gate_state.eq('abstain')
pd.testing.assert_frame_equal(old.loc[accepted], new.loc[accepted, old.columns], check_exact=True, check_dtype=False)
assert len(new) == 251 and accepted.sum() == 122 and abstain.sum() == 129
assert new.loc[abstain, ['episode_net_return', 'episode_gross_return', 'policy_fee_fraction']].eq(0).all().all()
assert not new.loc[abstain, ['executed', 'completed_trade']].any().any()
np.testing.assert_allclose(trades.net_return, old.episode_net_return, rtol=1e-12, atol=1e-12)
np.testing.assert_allclose(trades.gross_return-.002, trades.net_return, rtol=1e-12, atol=1e-12)
kept, omitted = trades.loc[accepted], trades.loc[abstain]
losers = kept.loc[kept.net_return.lt(0)]
print('loss exits', losers.outcome.value_counts().to_dict())
print('gross negative / fee flip', losers.gross_return.lt(0).sum(), losers.gross_return.gt(0).sum())
print('fees saved', len(omitted)*20, 'gross discarded', omitted.gross_return.sum()*1e4)
for name, part in [('half', kept.loc[kept.partial_fraction.gt(0)]), ('no_half', kept.loc[kept.partial_fraction.eq(0)])]:
    print(name, len(part), 'net event-bp', part.net_return.sum()*1e4)
print(omitted.nlargest(3, 'net_return')[['entry_time', 'exit_time', 'net_return']])
PY
```
