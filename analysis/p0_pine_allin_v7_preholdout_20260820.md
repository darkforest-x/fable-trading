# P0 — Pine ALLIN-V7.2 优化与预留验证回放（2026-08-20）

> 实验目录：`experiments/active/exp-pine-allin-v7-preholdout-v1`
> **结论：机械执行优化保留；当前收益假设拒绝。未训练、未 promote、未读 holdout、未动实盘。**

## 技术摘要

用户提供的 ALLIN-V7.2 确实包含可测的进场信息，但还不是一条可盈利策略：

- 在 2025-01-01 至 2026-03-01 的预留验证期，V7 每笔**毛收益 +18.37 bp**，扣除项目冻结的
  20 bp 往返成本后为 **-1.63 bp/笔**；净胜率 13.22%。
- 同币 × 同月 × 香港 6 小时时间块 × ATR 五分位的匹配随机入场为 -22.24 bp/笔，
  V7 相对它多 **+16.89 bp/笔**，配对符号置换 `p=0.0001`。这证明进场不是纯随机，
  但**相对随机更好不等于绝对赚钱**。
- 原 4x + 日历加杠杆在验证期组合收益 **-61.22%**、最大回撤 **67.66%**。
  改为每笔 2% 初始止损风险预算后，收益 **-1.97%**、最大回撤 **19.20%**。
  风险预算改变的是生存路径，不改变 -1.63 bp/笔的单位期望。
- 振荡器强度不能用于提纯：验证期 top-decile **毛收益 -12.00 bp、净收益 -32.00 bp/笔**，
  置换 `p=0.9972`。提高振荡器阈值会是验证集追参，不是改进。

因此，本轮交付的 Pine v6 是**执行语义更安全的研究稿**，不是通过收益门的生产策略。

## 交付物

| 产物 | 路径 | 用途 |
|---|---|---|
| Pine v6 优化稿 | `experiments/active/exp-pine-allin-v7-preholdout-v1/pine/allin_v8_research.pine` | TradingView 研究与导出对账 |
| 冻结配置 | `experiments/active/exp-pine-allin-v7-preholdout-v1/config.json` | 时间、成本、参数与实验臂契约 |
| 因果回放层 | `yoyo/layers/l3_backtest/pine_allin_v7.py` | 15m 信号、成交、止损与风险预算 |
| 运行器 | `scripts/backtest_pine_allin_v7.py` | 54 币回放、对照、统计与图表 |
| 独立验证器 | `scripts/validate_pine_allin_v7_backtest.py` | 逐笔重算与 holdout 守门 |
| 执行 notebook | `experiments/active/exp-pine-allin-v7-preholdout-v1/notebooks/pine_allin_v7_preholdout_audit.ipynb` | 从结果到结论的可执行审计 |

原附件 SHA-256：
`d721a4864a13cad1aae17ab634c87437a648897d1324d389e51ea591fb9aca8a`。

## Pine 静态审计与修复

| V7.2 问题 | 后果 | V8 research 处理 |
|---|---|---|
| 未设置佣金与滑点 | 回测默认不扣真实摩擦 | 佣金设为单边 0.10%，简单往返即项目冻结的 0.20%；滑点保持 0，未擅改成本假设 |
| `calc_on_every_tick=true` | 实时 bar 与历史 reload 的计算路径不同，可能重绘 | 改为 `false`，只在 `barstate.isconfirmed` 后发信号 |
| 注释写 UTC+8，过滤却直接用 `hour` / `dayofweek` | 过滤随交易所时区变化，日历加杠杆又用了另一时区 | 全部显式使用 `Asia/Hong_Kong` |
| `pos_sizing_type`、`risk_per_trade` 只声明不生效 | 界面看似风险计量，实际始终 4–13x | 实现 `risk% / stop%` 仓位，默认 2%，13x 封顶 |
| 反向信号同时 `strategy.entry` 和 `strategy.close` | `strategy.entry` 本身会反转，重复订单会污染成交 | 反转只发一个 entry；另提供 close-only 模式 |
| 新方向先覆盖 `sl_price`，旧仓后退出 | 旧仓在反转 bar 可能拿到新方向止损 | 多空 pending stop 分离；持仓状态只在成交后切换 |
| 入场后下一次计算才提交止损 | 默认下一根开盘成交后，入场 bar 无保护 | entry 同时排队 `strategy.exit(loss=...)`，相对真实成交价生效 |
| 冷却判断先于最新平仓更新 | 同一 bar 的新信号可能绕过刚产生的冷却 | 先读取最新 closed trade，再判断本 bar 信号 |
| “入场时间”实际写成禁入窗，跨午夜失效 | 配置含义不清，夜间窗口错误 | 明确 Off / Block / Allow，半开区间且支持跨午夜 |
| 无研究日期边界 | 容易无意读取项目 holdout | 默认结束于 2026-03-01，距 holdout 64 天 |

TradingView 官方说明：策略默认在创建订单后的下一可用 tick 成交，通常是下一根开盘；
`strategy.entry()` 会自动反转；不声明 commission/slippage 就不会替使用者补上这些成本；
`calc_on_every_tick` 也可能导致实时与历史结果不同。上述修复按这些 broker emulator 语义实现，
详见 [TradingView Strategies 文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。
日历函数的时区可显式指定，见 [TradingView Time 文档](https://www.tradingview.com/pine-script-docs/concepts/time/)。

**静态限制：本地没有 Pine 编译器，优化稿尚未在 TradingView Editor 编译。**
它已通过本地结构检查与 Python 语义测试，但在宣称 Pine/Python parity 前，必须导出 TradingView
交易清单做逐笔对账。

## 数据、切分与定义

| 项 | 值 |
|---|---|
| 数据 | 本仓 `data/kline_deep/okx_*_USDT_SWAP_15m_*.csv` |
| 原始数据量 | 54 币，5,828,556 根 15m bar，缺口 0 |
| 原始数据范围 | 2022-01-03 14:15 UTC 至 2026-02-28 23:45 UTC；2022 仅作指标 warm-up |
| 开发期 | 2023-01-01 至 2025-01-01（不含）；48 个有交易的币 |
| 预留验证期 | 2025-01-01 至 2026-03-01（不含）；54 个币 |
| holdout 起点 | **2026-05-04；读取次数 0** |
| V7 原始信号 | 全缓存 59,214 个；经过日期/日历/波动/持仓/skip 后，开发 16,346 笔、验证 12,333 笔 |
| 验证正类率 | 净收益 > 0：1,630 / 12,333 = **13.22%** |
| 成交 | 信号 bar 收盘确认，下一 bar 开盘成交 |
| 初始止损 | `min(4 × ATR14, 3%)`，从成交 bar 生效；本轮不改障碍参数 |
| 保本 | 浮盈达到 1.5% 后，下一 bar 起锁定 +0.1%；本轮不改 |
| 成本 | 固定 0.20% 往返，即 20 bp/笔 |
| 组合口径 | 每个可用币各自从 500 起步，再按日等权；不是交易所账户的并发保证金模拟 |

这里的“正类率”是净收益为正的交易比例，不是金标标签比例。

## 方法

### 因果性

决策 bar `t` 只读取 `t` 及之前的 OHLC：SMA(hl2, 10/40/60)、EMA(close, 100)、
Pine/Wilder ATR/RMA(14)、`hl2-SMA40` 的 200 bar 线性插值 99 分位、10 bar 差分和 HMA(10)。
只有成交后的 high/low/close 用于退出。入口固定为 `t+1` 开盘。

### 对照与检验

每笔候选配最多 3 个随机入场，匹配：

- 同币、同月、香港时间 6 小时块、ATR 月内五分位；
- 同方向、同持有 bar 数；
- 同初始止损、保本规则和 20 bp 成本；
- 排除策略信号 bar，并排除与候选持有区间重叠的近邻。

匹配覆盖率 99.81%–100%。`paired_signflip_p` 对候选减匹配对照的逐笔差做 10,000 次
符号翻转；`permutation_p` 把振荡器 score 与毛收益打乱 10,000 次，检验 top-decile 排名。
最小可报告 p 值为 `1/(10000+1)=0.0001`。

### 独立验证

验证器通过 **19/19** 检查：时间全部早于安全截止、safe end 早于 holdout、54 币全无缺口、
entry 确为下一 bar、持有期一致、205,918 笔跨臂交易与 617,142 个对照逐笔收益可重算、
匹配 strata/方向/期限一致、成本恒等式成立、组合权益有限且非负。

## 结果一：预留验证期主表

| 臂 | n | 毛 bp/笔 | **净 bp/笔** | 净胜率 | PF | 均值杠杆 | 组合收益 | 最大回撤 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SMA cross only（单特征基线） | 37,705 | +7.71 | **-12.29** | 17.07% | 0.807 | 4.00x | -99.33% | 99.51% |
| V7 fixed + boost（原始风险形态） | 12,333 | +18.37 | **-1.63** | 13.22% | 0.906 | 4.74x | -61.22% | 67.66% |
| V7 fixed, no boost | 12,333 | +18.37 | **-1.63** | 13.22% | 0.889 | 4.00x | -63.64% | 70.10% |
| **V7 risk 2%, no boost（优化默认）** | 12,333 | +18.37 | **-1.63** | 13.22% | 0.990 | 0.90x | **-1.97%** | **19.20%** |
| V7 risk 2%, no skip | 13,615 | +17.46 | **-2.54** | 13.26% | 0.981 | 0.89x | -4.14% | 20.00% |

PF 是按动态仓位后的美元盈亏计算，所以风险预算臂与固定杠杆臂即使单位收益相同，PF 也不同。
固定杠杆和日历 boost 不改变进出点，只放大资金路径。

![验证期权益与回撤](../experiments/active/exp-pine-allin-v7-preholdout-v1/results/validation_equity_drawdown.png)

### 与开发期同表对照

| 臂 | 开发净 bp/笔 | 验证净 bp/笔 | 开发组合收益 | 验证组合收益 | 开发回撤 | 验证回撤 |
|---|---:|---:|---:|---:|---:|---:|
| SMA cross only | -19.94 | -12.29 | -95.88% | -99.33% | 99.93% | 99.51% |
| V7 fixed + boost | -1.19 | -1.63 | -84.63% | -61.22% | 97.57% | 67.66% |
| V7 fixed, no boost | -1.19 | -1.63 | -74.05% | -63.64% | 91.67% | 70.10% |
| **V7 risk 2%, no boost** | -1.19 | -1.63 | **+11.39%** | **-1.97%** | **22.50%** | **19.20%** |
| V7 risk 2%, no skip | -4.06 | -2.54 | +1.67% | -4.14% | 23.30% | 20.00% |

这是第一个本仓规范下的 Pine 回放版本，没有旧 canonical Pine 报告可比；上表同时给出单特征基线、
原配置和每个隔离 overlay，作为后续版本的固定前值。

## 结果二：匹配随机对照

| 分割 | V7 risk 2% 净 bp/笔 | 匹配随机净 bp/笔 | **配对超额 bp** | sign-flip p |
|---|---:|---:|---:|---:|
| 开发 | -1.19 | -21.76 | **+16.11** | 0.0001 |
| **验证** | **-1.63** | **-22.24** | **+16.89** | **0.0001** |

![验证期策略与匹配随机对照](../experiments/active/exp-pine-allin-v7-preholdout-v1/results/validation_matched_control.png)

解读：V7 的 regime + oscillator 过滤把 SMA 单特征基线的验证净收益从 -12.29 改善到 -1.63 bp，
并稳定地优于匹配随机。可是成本线在 20 bp，策略毛收益只有 18.37 bp。**池内超额显著，绝对净值仍负。**
这正是本项目禁止“只报对照超额或只报 AUC”的原因。

## 结果三：top-decile、置换与 AUC

| 分割 | AUC（score→净正收益） | top 10% 毛 bp/笔 | **top 10% 净 bp/笔** | permutation p |
|---|---:|---:|---:|---:|
| 开发 | 0.5090 | +45.57 | **+25.57** | 0.0258 |
| **验证** | **0.5112** | **-12.00** | **-32.00** | **0.9972** |

项目成功门是 top-decile 扣成本为正且 `p<0.01`。验证期两条都失败，而且方向相反。
AUC 约 0.51 没有决策价值；开发期看起来较强的振荡器高分在验证期成为最差组。
**不得据此提高 `osc_threshold`、选 top-decile 或继续挖 score 分位。**

## 结果四：为什么风险预算有效、收益仍不够

### 方向不稳定

| 分割 | 多头净 bp/笔 | 空头净 bp/笔 |
|---|---:|---:|
| 开发 | **+10.56** | -12.86 |
| 验证 | -12.16 | **+8.37** |

多空优势完全翻转。不能看到验证期空头为正就切成 short-only；这会把预留验证当训练集。

### “保本”没有覆盖交易成本

验证期 12,333 笔中，9,420 笔（76.38%）因 stop 退出。其中 **5,604 笔**恰好在
`+10 bp` 毛收益的保本价退出，占全部交易 **45.44%**；扣 20 bp 后每笔其实是 `-10 bp`。
因此 V7 的“break-even offset 0.1%”只是价格保本，不是经济保本。

若这些路径在不改变后续成交的理想反事实下多锁 10 bp，机械空间约为
`5604 / 12333 × 10 = 4.54 bp/笔`，高于当前 -1.63 bp 缺口；**这不是回测结果**，因为更高止损会
改变退出时间、反转与 cooldown。把 offset 从 0.1% 改到成本线属于障碍参数改动，必须 owner
批准后作为下一轮唯一变量重跑。

### skip 有可重复的风控价值

关闭 skip 多出来的交易：开发 1,738 笔，净 **-26.20 bp/笔**；验证 1,426 笔，净
**-15.10 bp/笔**。保留 skip 使优化臂验证收益从 -4.14% 改善到 -1.97%，回撤从 20.00%
降到 19.20%。它目前应保留，但仍不能把整体单位期望变正。

### 日历加杠杆没有稳定证据

1.5x 时间组开发为 +27.62 bp/笔、验证变成 -43.12 bp/笔；2x 组开发 -4.19、验证 +48.65。
分组方向翻转。boost 对整条固定杠杆资金曲线也是开发更差、验证略好，不能算稳定规律；优化稿默认关闭。

## 与当前项目的关系

这个 Pine 与项目共享“均线启动 + 因果下一 bar 执行”的研究主题，但不是同一个模型：

- Pine 是 SMA10/60 + EMA100 + 振荡器的数值规则；当前项目是 Local Signal V2 的形态协议，
  并采用 L1 检测 + L2 判断的两层架构。
- 本轮代码只放在 `yoyo/layers/l3_backtest/`，没有跨层 import，没有把 Pine 冒充 L1 detector、
  金标或 L2 模型。
- 它最合适的角色是**透明单特征/规则基线和风险 overlay 试验台**。不能接入 tip-smoke、forward、
  `models/ACTIVE` 或执行器。
- 项目仍处 P0/P1，`models/active_bundle.json` 不存在、生产 0 模型。本轮没有训练、promote、
  改阈值、改新鲜度门或动实盘。

如果将来研究接入，信号时间必须记为完整决策窗右端，执行从下一 bar 开始，并重新走项目的
因果、tip-smoke、前向新鲜 100 笔与 owner 批准；不能拿本报告的历史验证直接越门。

## 如何提高收益并降低回撤

### 现在可以采用的研究默认值

- `Risk Based = 2%`，而不是固定 4x–13x；
- `Enable legacy boosts = false`；
- `Skip logic = true`；
- `calc_on_every_tick = false`、收盘确认、下一开盘成交；
- 显式香港时区、初始止损随 entry 排队；
- 佣金单边 0.10%，不把零成本结果当收益。

这些设置把回撤显著压低并修复执行 bug，但**没有让策略通过收益门**。

### 下一轮按优先级、每次只改一个变量

1. **TradingView parity（推荐，非收益实验）。** 在 BTC/ETH 各选一段 pre-holdout 固定窗口，
   导出 Pine trade list，与 Python 逐笔比 entry time/price、stop、reverse、cooldown。
   零假设是时间戳/价格误差应为 0；不通过先修语义，不优化参数。
2. **经济保本 offset：0.1% → 0.2% 或 cost-aware。需 owner 批准。** 只改这一变量，
   先在 2023–2024 内做 anchored walk-forward；不碰 ATR4、3% cap、成本或 holdout。
3. **风险预算 2% → 1%。** 目标是把资金路径回撤再压低，不声称改善单位收益；若未来改实盘仓位，
   需 owner 逐次批准。
4. **单一低换手确认门。** 例如仅测试“交叉后再确认 1 根”这一变量，目标是每笔毛收益增加至少
   1.64 bp 以越过成本线。由于本轮已经看过 2025–2026 验证结果，后代配置应先在开发期内部
   预注册/筛选，再把该验证段视作 research-seen，不能冒充未见 acceptance。
5. **停止 score 调阈值、方向择时和日历倍数挖掘。** 它们在开发/验证间翻转，继续挖只会过拟合。

## 风险与诚实声明

1. **不是 TradingView broker emulator。** Python 是可审计翻译，没有 Pine 编译或交易导出 parity；
   TradingView 的 bar magnifier、mintick、订单舍入可能产生差异。
2. **没有滑点、资金费、借贷费和交易所清算。** 只用了 owner 冻结的 20 bp 成本。固定杠杆臂接近
   归零时，Python 把损失封顶为账户权益，不虚构负余额；其资金曲线不可当清算模拟。
3. **当前 54 币是生存者宇宙。** 不是每个历史日期当时可上市/可交易币的快照，存在选择偏差；
   新币按自身上市后数据进入。
4. **匹配对照复制候选的持有期。** 它隔离 entry timing，但不复现随机入场自己产生的反向信号，
   所以是严格的入场时点对照，不是完整随机策略。
5. **组合等权是研究汇总。** 它不模拟一个账户同时持有 54 个币的保证金、相关性和容量；单图 Pine
   的收益会随品种与窗口变化。
6. **验证已被看见。** 它没有消耗正式 holdout，但从今天起对这个策略家族不是“未见数据”。
   后续选择不能反复看它后再报告为 OOS。
7. **builder 与产物尚在未提交工作树。** 依据项目复现法，本报告是可重跑的 workspace 诊断，
   在代码提交并记录 commit 前不宣称 canonical artifact provenance。
8. **生产资格全部为 false。** 当前净期望为负、top-decile 门失败、无 fresh forward evidence；
   不得部署、promote 或连接真金账户。

## 复现命令

```bash
# 主回放（只读取 safe_end 之前的有界前缀）
PYTHONPATH=. .venv/bin/python scripts/backtest_pine_allin_v7.py

# 独立逐笔验证
PYTHONPATH=. .venv/bin/python scripts/validate_pine_allin_v7_backtest.py

# notebook 依赖只能进隔离评估 venv；先 dry-run
python3 -m venv --system-site-packages /tmp/fable-pine-eval-venv
python3 -m pip --python /tmp/fable-pine-eval-venv install --dry-run \
  --report /tmp/fable-pine-notebook-dryrun.json nbformat nbclient ipykernel
python3 -m pip --python /tmp/fable-pine-eval-venv install nbformat nbclient ipykernel
/tmp/fable-pine-eval-venv/bin/python -m ipykernel install \
  --prefix /tmp/fable-pine-eval-venv --name fable-pine-eval --display-name "Fable Pine Eval"
JUPYTER_PATH=/private/tmp/fable-pine-eval-venv/share/jupyter \
  PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_allin_v7_report.py

# 项目规定的 HTML 交付
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \
  analysis/p0_pine_allin_v7_preholdout_20260820.md --out-dir analysis/html

# 测试
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pine_allin_v7_backtest.py
```

## 下一步选项（需 owner 决策的已标明）

- **A（推荐）：先做 TradingView/Python 逐笔 parity。** 不改参数、不读 holdout，先证明研究工具正确。
- **B：批准一个“经济保本”单变量实验。** 这是最直接的收益缺口候选，但属于障碍参数改动，
  需要 owner 明确批准；本轮没有先试。
- **C：批准 1% 风险预算研究。** 只为进一步降回撤；未来任何真实仓位改动仍需逐次授权。
- **D：暂不继续。** 保留优化 Pine 作为研究基线，当前策略维持 production_eligible=false。

无论选择哪条，正式 holdout（≥2026-05-04）仍为 **0 次消耗**；读取它必须另行明确批准。
