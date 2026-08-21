# ETH 15m Pine V12 优化与最近半年回测前置报告（2026-08-21）

> **当前结论**：已把优化落实成 3 个彼此隔离的 Pine v6 paper 版本。按尚未触碰
> repository holdout 的证据，当前应冻结 **V12F：六均线 W8 全状态门**，而不是 TBSL。
> 2025-01～2026-02 的已消费 preholdout 区间里，V12F 为 **+31.41% / DD 18.22%**，
> 对照 V9 的 **+22.82% / DD 20.03%**；2026 年 1–2 月小样本为 **+3.26% / DD 6.62%**，
> 对照 V9 的 **-4.62% / DD 11.40%**。但它的最终匹配超额 `p=0.3100`，没有通过项目
> `p<0.01` 门槛，仍只能叫 paper hypothesis。

> **最近半年尚未完成**：按报告日期 2026-08-21，最近半年定义为
> `2026-02-21T00:00:00Z` 至 `2026-08-21T00:00:00Z`。本轮只评价了安全重叠
> `2026-02-21` 至 `2026-03-01`；`2026-05-04` 以后是 repository holdout，未获 owner
> 专项批准前没有读取、哈希、绘图或评分。安全重叠只有 3 笔且全部亏损，不能冒充半年结果。

## 一、落地了什么 Pine

| 版本 | 唯一改动 | 反向信号语义 | 当前资格 |
|---|---|---|---|
| V9 | 冻结基线 | 下一开盘反手 | research comparator |
| V12F | 六线 W8 净交叉门控完整状态转换 | W8 拒绝的 guarded signal 不平仓、不反手、也不消耗 cooldown | **当前冻结候选；paper only** |
| V12E | 同一个 W8 只控制新开仓 | W8 拒绝时仍平反向旧仓，但不新开 | 机制拆解；paper only |
| V12T | V9 信号不变；分阶段预选的 TP30 + ATR3 composite，SL cap 仍 3% | 与 V9 相同 | 高回撤趋势备选；paper only |

W8 与 TBSL 没有组合。V12F/V12E 各是相对 V9 的一个 gate 变量；V12T 内部则是此前先选 TP、
再选 ATR 的已冻结 barrier composite，不冒充严格单变量。这样仍可把 W8、反转语义和 TBSL
效果分开，不把三项打包后失去归因。

Pine 文件：

- `allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine`，SHA-256
  `9e03c2959e403632a8db06c66ee43487d7388e0dfdaf31abe5ae32218c7567de`；
- `allin_eth_15m_v12e_ma6_w8_entry_only_paper.pine`，SHA-256
  `593f7b3ae9da0832073edd57b96f631a63bc450def95f786d17ea2e2c219e6e9`；
- `allin_eth_15m_v12t_tbsl_paper.pine`，SHA-256
  `1fb6731f6ff96ed12429a71eecfb0dffe8e238be8f880215c1f170388f6da457`。

所有新脚本都 fail-closed 到 ETH base、15 分钟、确认 bar、next-open 成交；没有
`request.security` 或 lookahead。Pine 标题、HUD 和 alerts 均写明 `PAPER ONLY`。

## 二、六均线 W8 到底是什么

六条线严格使用 `close`：SMA20、EMA20、SMA60、EMA60、SMA120、EMA120。只计算有明确
快慢方向的 12 对：20×60、20×120、60×120，每组四个 SMA/EMA 组合。同周期 SMA/EMA
没有天然快慢方向，因此排除。

对多头，bar `t` 的金叉事件是 `fast[t] > slow[t]` 且 `fast[t-1] <= slow[t-1]`；空头为
相反条件。W8 在 `[t-7, t]` 统计方向一致交叉数减方向相反交叉数。阈值是 `>=0`。

必须把语义说准确：**这不是“至少出现 N 次交叉”的密集度门**。阈值 0 意味着“最近 8 根里，
相反方向交叉不能多于本方向交叉”；如果 8 根内完全没有交叉，也会通过。因此它目前更像
“拒绝近期方向冲突”因子。2023 / 2024 / 2025-至2026-02 分别拒绝 32/167、21/168、16/168
个 guarded candidates。此前时间开发选择了 W16 第一名，但它锁定后 2024 明显失稳；W8 是
开发期第二名，也是唯一同时改善 2023/2024 收益与回撤的版本，因此被固定成新的 forward
hypothesis。本轮不再围绕 6/7/9 根或新的 churn 下限挖已消费数据。

## 三、主回测结果

共同条件：OKX ETH-USDT-SWAP 15m、本金 500、每单止损风险 1%、最大 13x、往返成本 0.20%、
Hong Kong 日历过滤、原 cooldown、break-even 和 next-open 反转均保持。收益为策略资金曲线复利
收益，不是 ETH 价格涨跌幅。DD 是 15m mark-to-market 最大回撤。

| 区间 | 版本 | 交易 | 收益 | DD | 胜率 | PF | 净 bp/笔 |
|---|---|---:|---:|---:|---:|---:|---:|
| 2023 | V9 | 83 | +70.50% | 26.48% | 14.46% | 1.921 | +52.01 |
| 2023 | V12F W8 full | 59 | **+117.84%** | **22.55%** | 16.95% | **3.353** | **+119.21** |
| 2023 | V12E W8 entry | 65 | +97.07% | 22.55% | 16.92% | 2.810 | +87.08 |
| 2023 | V12T TP30/ATR3 | 93 | +119.14% | 35.09% | 12.90% | 1.960 | +59.43 |
| 2024 | V9 | 83 | +109.26% | 12.22% | 19.28% | 2.748 | +141.35 |
| 2024 | V12F W8 full | 70 | **+110.61%** | **10.49%** | 18.57% | **3.160** | **+179.15** |
| 2024 | V12E W8 entry | 77 | +107.78% | 11.52% | 19.48% | 2.855 | +152.70 |
| 2024 | V12T TP30/ATR3 | 89 | +133.91% | 14.23% | 16.85% | 2.393 | +122.89 |
| 2025-01～2026-02 | V9 | 110 | +22.82% | 20.03% | 10.00% | 1.366 | +30.22 |
| 2025-01～2026-02 | V12F W8 full | 97 | **+31.41%** | **18.22%** | 11.34% | **1.589** | **+57.63** |
| 2025-01～2026-02 | V12E W8 entry | 102 | +26.75% | 18.22% | 10.78% | 1.458 | +43.06 |
| 2025-01～2026-02 | V12T TP30/ATR3 | 114 | +27.59% | 24.92% | 8.77% | 1.354 | +24.99 |

相对 V9，最终已消费区间的隔离变化如下；V12T 是分阶段预选 composite，不属于严格单变量：

| 版本 | 收益变化 | DD 变化 | 解释 |
|---|---:|---:|---|
| V12F W8 full | **+8.59 个百分点** | **-1.81 个百分点** | 点估计最佳，且收益/回撤同向改善 |
| V12E W8 entry | +3.93 个百分点 | -1.81 个百分点 | 只删坏开仓有帮助，但弱于 full-state |
| V12T TP30/ATR3 | +4.77 个百分点 | **+4.89 个百分点** | 收益增加但回撤显著恶化，不适合作为当前默认 |

V12F 高于 V12E，说明 W8 的历史改善不只来自过滤新开仓，也来自拒绝某些过早反转。判断层以后
接入时，必须分别定义“是否消耗 cooldown / 是否关闭反向仓 / 是否允许开新仓”，不能简单把模型
布尔值 AND 到 signal 上。

## 四、最近的安全诊断

### 2026 年 1–2 月

| 版本 | 交易 | 收益 | DD | 胜率 | PF | 净 bp/笔 | 对 V9 收益变化 |
|---|---:|---:|---:|---:|---:|---:|---:|
| V9 | 21 | -4.62% | 11.40% | 9.52% | 0.634 | -61.78 | — |
| V12F W8 full | 13 | **+3.26%** | **6.62%** | 15.38% | **1.455** | **+51.01** | **+7.88pp** |
| V12E W8 entry | 18 | -2.42% | 10.43% | 11.11% | 0.770 | -47.22 | +2.20pp |
| V12T TP30/ATR3 | 21 | -5.20% | 13.24% | 9.52% | 0.661 | -55.86 | -0.58pp |

这支持 full-state 而不是 entry-only，但只有 13 笔，不能独立证明稳定性。

### 最近半年安全重叠：2026-02-21～2026-03-01

| 版本 | 交易 | 收益 | DD | 胜率 | 结论 |
|---|---:|---:|---:|---:|---|
| V9 | 3 | -2.40% | 3.39% | 0% | 三笔全亏 |
| V12F W8 full | 3 | -2.40% | 3.39% | 0% | 三个 signal 全通过 W8，因此与 V9 相同 |
| V12E W8 entry | 3 | -2.40% | 3.39% | 0% | 同上 |
| V12T TP30/ATR3 | 3 | -2.54% | 3.85% | 0% | 未改善 |

这 8 天的结果是明确的坏消息，但样本量不允许外推到半年。

## 五、匹配随机对照与统计门

对照严格匹配 ETH × UTC 月 × 香港 6 小时时段 × **前一个月** ATR 五分位，并复制候选持仓
时长、方向、障碍和成本；每笔 3 个不复用 controls。`p` 为 UTC 周区块单侧 sign-flip，项目门槛
是 `<0.01`。

| 区间 | 版本 | 候选净 bp/笔 | 对照净 bp/笔 | 候选−对照 bp/笔 | p |
|---|---|---:|---:|---:|---:|
| 2025-01～2026-02 | V9 | +30.22 | +58.61 | -28.39 | 0.3901 |
| 2025-01～2026-02 | V12F W8 full | +57.63 | +43.19 | **+14.44** | 0.3100 |
| 2025-01～2026-02 | V12E W8 entry | +43.06 | +14.05 | +29.01 | 0.2156 |
| 2025-01～2026-02 | V12T TP30/ATR3 | +24.99 | +25.05 | -0.06 | 0.4073 |
| 2026-01～02 | V9 | -61.78 | -29.36 | -32.42 | 0.4742 |
| 2026-01～02 | V12F W8 full | +51.01 | +3.39 | **+47.62** | 0.2350 |
| 2026-01～02 | V12E W8 entry | -47.22 | -35.08 | -12.15 | 0.4425 |
| 2026-01～02 | V12T TP30/ATR3 | -55.86 | -17.93 | -37.93 | 0.4852 |

点估计支持 V12F，但没有一个最终/近期结果通过 `p<0.01`。所以“历史收益更高”成立，
“已证明存在稳定 alpha”不成立。

## 六、AUC 与 top-decile

这些 Pine 版本不是训练模型。为满足统一评价口径，只把原 V9 振荡器绝对值当作单特征排序分数；
AUC 的标签是该笔扣成本后是否盈利。它只是诊断，不是新 gate。

| 最终已消费区间 | AUC | top 10% 笔数 | top 10% 净 bp/笔 | 排序置换 p |
|---|---:|---:|---:|---:|
| V9 | 0.517 | 11 | +202.97 | 0.1586 |
| V12F W8 full | 0.415 | 10 | +224.27 | 0.1823 |
| V12E W8 entry | 0.469 | 11 | +186.20 | 0.2031 |
| V12T TP30/ATR3 | 0.510 | 12 | +202.60 | 0.1266 |

全部未过 `p<0.01`，因此当前振荡器强度不能承担 LR 层的替代品。池内排序也不能替代上面的随机
入场对照。

## 七、为什么胜率仍然低

这是典型低胜率、高盈亏比趋势策略。最终已消费区间中，V12F 97 笔只有 11 笔净盈利，平均盈利
`+13.21%`，平均亏损 `-1.04%`，平均盈亏幅度约 **12.7:1**，最大单笔净盈利 `+38.44%`。
因此 11.34% 胜率仍能得到 PF 1.589 和正收益。

代价也很明确：没有大趋势时连续小止损会非常难看。2026-02-21 后可见的 3 笔全部亏损；如果未来
半年没有足够长的 runner，低胜率结构不会自动变好。优化目标不应是机械提高胜率，而是减少震荡
假启动，同时保留少数大趋势。

## 八、TBSL 的真实作用

V12T 把 TP30 距离在信号收盘冻结成 tick，与 entry 一起提交初始 stop+TP，因此 TP 从入场 bar
起就存在。成交后再用平均成交价加减冻结 tick 维护绝对 limit。Python replay 和随机对照使用相同
`take_profit_distance_basis="signal_close"`。

最终 114 笔里只有 3 笔真正达到 30% TP，104 笔止损、7 笔反转；same-15m stop/TP 双触为 0。
它确实允许极少数大趋势跑远，但最终匹配超额约 0，DD 升至 24.92%，2026 年 1–2 月也弱于 V9。
因此当前不把 TBSL 放进 V12F，更不能宣称“TP30 已优化完成”。

## 九、数据统计与边界

- 有界读取 145,666 根 15m bar，`2022-01-03T15:30:00Z` 至
  `2026-02-28T23:45:00Z`；bounded-prefix SHA-256
  `17f091b5e9b88f35feb5560c5a774a9277a21b64444d6ee2ea963cfacfc09159`。
- 本轮在 git commit `a5fd22a764fa3a96924c73cb5500fb20618e28c2` 的 dirty working tree
  执行；摘要另存 backtest script、Pine generator、execution engine 与 cross-feature 文件的内容
  SHA-256，复现身份以内容 hash 为准，不能只靠 commit。
- 重复时间、空 OHLCV、非 15m 间隔、K 线体约束错误均为 0。
- 主时序 2023 / 2024 / 2025-至2026-02 有 167 / 168 / 168 个 guarded raw candidates；
  V9 实际执行 83 / 83 / 110 笔。
- W8 full 通过 135 / 147 / 152 个 candidates，最终实际执行 59 / 70 / 97 笔。
- `holdout_rows_read=0`。但本实验家族历史上已有未经批准的 EOF 字节访问事故，所以不能把本轮
  写成“项目从未看过 holdout”；只能写本次 bounded replay 没有读取 holdout 行或字节。
- 研究源是 OKX swap proxy，不证明等于 TradingView 上未指定 venue 的 `ETHUSDT.P`。

## 十、测试与 Luna Max 复核

本地 `41 passed`：覆盖生成文件 hash/equality、12 对交叉、W8 因果窗口、六线 MA warmup ready、
15m guard、full-state cooldown、entry-only 反向平仓、signal-close TP tick、matched-control TP
语义和 holdout-safe periods。

可见 Codex 任务使用 `gpt-5.6-luna`、`thinking=max` 做两轮只读语义复核。第一轮在新 Pine 落盘前
指出两个需要显式处理的风险：entry-only 尚未迁移，以及依赖成交价的 TP 无法天然保证入场当根
保护。本轮实现分别增加独立 V12E 状态路径和信号收盘冻结 tick。第二轮确认 12 对交叉、W8
`[t-7,t]`、full-state、entry-only 和 TP 设计基本成立，同时发现 Python 缺少 Pine 的六线 ready
门，并指出 V12T 不能叫严格单变量；两处都已修正。其最终裁决仍是 **LIMITED / Pine parity FAIL**：
新 Pine 没有官方 trade export，对 same-bar stop/TP 路径也没有 Pine 侧 stop-first 保证。Luna 不修改
文件、不读取 holdout。

## 十一、风险与诚实声明

- V12F 是 post-selection paper hypothesis；2025-至2026-02 也已被查看，不能再叫 unseen OOS。
- W8 阈值 0 会让“零交叉”通过，它不是严格的六线密集度因子。
- 最终匹配超额和 top-decile 排序都未过 `p<0.01`；收益仍高度依赖少数大 runner。
- V12T 的 Python stop-first 只在同 bar 双障碍触发时与 TradingView emulator 路径有关；本轮该事件
  为 0，但这不等于未来永远没有。
- 三个新 Pine 尚未在 TradingView 官方编译器逐个编译，也没有 trade-export parity；V9 编译通过
  不能自动外推到 V12。
- 20 bp 成本已扣；滑点、资金费、强平、最小下单量仍未进入资金曲线。
- 没有训练 LR/LightGBM，没有 promote、deploy、改 ACTIVE、写 forward_log 或操作真金。
- 完整最近半年尚未跑；任何把本报告的 8 天结果写成半年结果都属于错误陈述。

## 十二、下一步：先锁配置，再只消费一次 holdout

建议在 owner 明确批准后，只对以下**已冻结配置**做一次完整最近半年最终验收：

1. 候选：V12F，SHA-256 `9e03c295...`；
2. 对照：冻结 V9，不参与再选参；
3. 区间：`2026-02-21T00:00:00Z` 至 `2026-08-21T00:00:00Z`；
4. 固定 15m、20 bp、1% risk、13x cap、原 BE/cooldown/reversal；
5. 输出逐笔 trades、匹配随机 controls、资金曲线、月度分解，并同步 Notion；
6. 记录“V12F 配置第 1 次消耗 holdout”；看完后不再用该 holdout 调 W8、阈值或 TBSL。

不建议把 V12E、V12T 或 V12F+TBSL 一起送入 holdout 后择优；那会把最终集重新变成调参集。

## 十三、复现命令

```bash
git branch --show-current  # 必须是 main

PYTHONPATH=. .venv/bin/python \
  scripts/generate_pine_eth_15m_optimized_variants.py

PYTHONPATH=. .venv/bin/python \
  scripts/backtest_pine_eth_15m_v12_preholdout.py

PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_backtest_pine_eth_15m_v12_preholdout.py \
  tests/test_generate_pine_eth_15m_optimized_variants.py \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_pine_cross_features.py \
  tests/test_research_pine_eth_15m.py

python3 scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_v12_preholdout_20260821.md \
  --out-dir analysis/html
```

逐笔产物：

- `optimized_pine_variants_primary_trades.csv`：三个不重叠主区间、四个版本的 1,042 笔记录；
- `optimized_pine_variants_recent_safe_trades.csv`：最近半年安全重叠的 12 笔版本化记录；
- `optimized_pine_variants_preholdout_controls.csv`：逐笔 3 个匹配 controls；
- `optimized_pine_variants_preholdout_pairs.csv`：候选与 control mean 的成对差；
- `optimized_pine_variants_preholdout.json`：全部配置、hash、边界和汇总指标。
