# ETH 15m Pine：六均线交叉因子与趋势 TP/SL 优化

生成日期：2026-08-21

实验：`exp-pine-eth-15m-v1`

固定范围：ETH-USDT-SWAP，15 分钟，2023-01-01 至 2024-12-31 开发区间
状态：**研究候选已形成；未训练 LR；未读取 holdout；未改 ACTIVE/生产参数**

## 技术结论：先保留六线 W8，TP30+ATR3 只作高回撤备选

1. **兼顾收益与回撤的当前研究候选是六线 W8 gate，不是 TBSL。** 它要求最近 8 根内，
   12 个跨周期快慢组合的方向性交叉数减反向交叉数 `>= 0`。年度收益为
   **+117.84% / +110.61%**，相对 V9 的 +70.50% / +109.26%；最大回撤为
   **22.55% / 10.49%**，低于 V9 的 26.48% / 12.22%。但 2023 匹配对照周检验
   `p=0.0486`，未过项目 `p<0.01`，因此只能列为下一阶段前向候选。
2. **2023 正式选出的六线 W16 第一名不能采用。** 它在 2023 达 +132.02%、DD 14.26%，
   但锁定后 2024 只剩 +43.54%、DD 17.03%。W8 是只用 2023 排出的第二名；报告没有在看完
   2024 后把它改称历史最优。
3. **TP/SL 分阶段搜索选到 30% TP 与 3×ATR 初始止损。** 它把收益提高到
   **+119.15% / +133.91%**，但 DD 同时升到 **35.09% / 14.23%**。平均杠杆由
   0.98x/0.67x 升到 1.33x/0.88x；主要机制是固定 1% 风险下止损变窄、仓位变大，不是胜率提高。
4. **30% 是宽尾部保护，不是频繁止盈。** 两年各只有 2 笔命中 TP；2.5%–20% 的较短 TP
   在 2023 最差半年明显更差。40%、50% 与不设固定 TP 在 TP-only 阶段完全相同，说明原反转退出
   先发生，趋势尾部没有被随意截断。这与“能吃 20% 以上大趋势”的策略定位一致。
5. **低胜率是长尾结构，不是单独的失败证据。** V9 胜率只有 14.46%/19.28%，但平均盈利是
   平均亏损的 10.3x/12.5x，PF 1.92/2.75。六线 W8 的盈亏比进一步达到 14.5x/16.1x。
   优化目标应是成本后期望、PF、回撤与尾部稳健性，而不是把胜率硬拉高。

## 六线 W8 是唯一同时改善两年收益与回撤的单变量方案

下表全部是完整动态状态机，不是从既有 trades CSV 事后删交易。门控会改变入场、反转、cooldown 与
后续持仓；固定成本为 20bp，信号在确认后下一根开盘执行，1% 风险仓位不变。

| 方案 | 年份 | 接受/Raw | 交易 | 净 bp/笔 | 资金收益 | PF | 净胜率 | 最大 DD | 匹配对照 bp | 超额 bp | 周 p | 去 top1 bp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V9 baseline | 2023 | 167/167 | 83 | +52.01 | +70.50% | 1.921 | 14.46% | 26.48% | -18.04 | +70.05 | 0.0228 | +22.39 |
| V9 baseline | 2024 | 168/168 | 83 | +141.35 | +109.26% | 2.748 | 19.28% | 12.22% | +10.89 | +130.47 | 0.0064 | +110.68 |
| 7-EMA W16 | 2023 | 131/167 | 76 | +43.30 | +63.30% | 2.003 | 13.16% | 26.51% | +35.06 | +8.24 | 0.3372 | +10.80 |
| 7-EMA W16 | 2024 | 127/168 | 73 | +97.30 | +69.88% | 2.298 | 16.44% | 15.19% | +8.90 | +88.40 | 0.0580 | +61.75 |
| 6-MA W16 winner | 2023 | 97/167 | 45 | +189.47 | +132.02% | 4.704 | 22.22% | 14.26% | -12.02 | +201.48 | 0.0079 | +126.19 |
| 6-MA W16 winner | 2024 | 106/168 | 65 | +82.22 | +43.54% | 2.053 | 12.31% | 17.03% | -2.24 | +84.46 | 0.0493 | +44.27 |
| 6-MA W8 runner-up | 2023 | 135/167 | 59 | +119.21 | +117.84% | 3.353 | 16.95% | 22.55% | +46.03 | +73.18 | 0.0486 | +70.00 |
| 6-MA W8 runner-up | 2024 | 147/168 | 70 | +179.15 | +110.61% | 3.160 | 18.57% | 10.49% | -3.39 | +182.54 | 0.0053 | +143.25 |
| TP30 + ATR3 | 2023 | 167/167 | 93 | +59.43 | +119.14% | 1.960 | 12.90% | 35.09% | -14.37 | +73.80 | 0.0914 | +27.69 |
| TP30 + ATR3 | 2024 | 168/168 | 89 | +122.89 | +133.91% | 2.393 | 16.85% | 14.23% | +11.90 | +110.99 | 0.0122 | +90.42 |

![年度资金收益与回撤](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/charts/cross_tbsl_annual_return_drawdown.png)

图中最值得保留的是六线 W8：2023 的收益提升很大，2024 基本守住基线，同时两个年份的 DD 都下降。
TBSL 的收益更高，但回撤也更高，不能回答“提高收益同时降低回撤”这一目标。

### 为什么 W16 的漂亮 2023 不能相信到底

![六线窗口跨半年稳定性](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/charts/six_ma_cross_window_stability.png)

W16 在 2023 两个半年都漂亮，却在 2024H2 变成 -1.77%；W8 四个半年依次为
**+61.39%、+34.83%、+64.81%、+27.79%**。这说明交叉数量确实含信息，但有效记忆长度不稳定。
W8 可以冻结为 forward hypothesis；不能用当前已看过的 2024 再继续围绕 8 根微调到 6/7/9 根。

## 真正测到的“六条线”是什么

用户原始 Pine 附件本身只有 `SMA(hl2,10)`、`SMA(hl2,60)`、`EMA(close,100)` 三条直接趋势均线；
振荡器内部的 SMA40/HMA10 不是六线趋势束。当前 V9 又增加 EMA200 slope，但仍不是六线。

本轮按项目视觉定义补做真正六线：`SMA20、EMA20、SMA60、EMA60、SMA120、EMA120`。只统计
20×60、20×120、60×120 的全部 SMA/EMA 组合，共 12 对；同周期 SMA20↔EMA20 等 3 对没有天然
快慢方向，因此不硬定义金叉/死叉。每个候选的因子为：

`最近 N 根方向性交叉事件数 - 最近 N 根反向交叉事件数`

全部特征只用信号 bar `t` 及之前数据，入场仍是 `t+1` 开盘。335 个候选在 W16 上的因子中位数为
0，P05–P95 为 -2 至 4。

项目原有 7-EMA `order_score` 只描述“现在是否顺序排列”，没有数最近真正发生了几次交叉。本轮另测
7-EMA 相邻 6 对交叉；其锁定门把年度收益降到 +63.30%/+69.88%，因此淘汰为硬 gate。两类交叉计数
都已导出，可在未来 Pine 专用 LR 中作为特征，而不是现在直接训练。

## TP30 保留长尾，但 ATR3 用更高杠杆换收益

TP 搜索先固定原 4×ATR/3% stop，随后只变 ATR 倍数，再只变百分比 cap；没有同时乱搜三轴。

| 固定 TP | 2023H1 | 2023H2 | 2024H1 | 2024H2 | 2023 最差半年 |
|---|---|---|---|---|---|
| None | +39.80% | +21.83% | +60.32% | +30.52% | +21.83% |
| 2.5% | -14.12% | +1.87% | +1.95% | +2.22% | -14.12% |
| 5% | +7.07% | -4.49% | +8.50% | +7.43% | -4.49% |
| 8% | +29.03% | -4.72% | +29.16% | +20.93% | -4.72% |
| 12% | +23.78% | -4.31% | +48.78% | +27.51% | -4.31% |
| 20% | +25.88% | +11.14% | +74.90% | +24.32% | +11.14% |
| 30% | +44.73% | +28.11% | +55.19% | +32.03% | +28.11% |
| 40% | +39.80% | +21.83% | +60.32% | +30.52% | +21.83% |
| 50% | +39.80% | +21.83% | +60.32% | +30.52% | +21.83% |

![固定止盈网格的 2023 最差半年](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/charts/trend_tp_grid_worst_2023_half.png)

TP30 位于已扩展至 50% 的网格内部，不是搜索上边界。然后 ATR 倍数网格
`2/3/4/5/6/8` 选出 3；2×ATR 在一个 2023 半年亏 -28.49%，说明继续缩止损会破坏策略。
百分比 cap 的 3%/4%/5%/6% 在 2023 两个半年结果完全相同，所以 **3% 不是被数据识别出的优势**，
只是并列时保留原默认值。最终 TP 价与初始 stop 都按 0.01 tick；同 15m 双触碰用保守 stop-first 并
显式标记，本轮最终交易 0 次碰撞。

### 为什么胜率降了，资金收益反而升了

| 方案 | 年份 | 盈利笔/总笔 | 平均盈利 bp | 平均亏损 bp | 盈亏比 | 最大单笔 bp |
|---|---|---|---|---|---|---|
| V9 baseline | 2023 | 12/83 | +849.91 | -82.85 | 10.3× | +2480.39 |
| V9 baseline | 2024 | 16/83 | +1102.27 | -88.12 | 12.5× | +2656.58 |
| 6-MA W8 runner-up | 2023 | 10/59 | +1060.88 | -72.97 | 14.5× | +2973.39 |
| 6-MA W8 runner-up | 2024 | 13/70 | +1325.66 | -82.33 | 16.1× | +2656.58 |
| TP30 + ATR3 | 2023 | 12/93 | +975.96 | -76.35 | 12.8× | +2980.01 |
| TP30 + ATR3 | 2024 | 15/89 | +1117.00 | -78.62 | 14.2× | +2980.01 |

TP30+ATR3 的胜率为 12.90%/16.85%，反而低于 V9；它提高收益的原因是少数大趋势、更多动态再入场，
以及 3×ATR 在固定风险预算下放大仓位。2023 去掉最大一笔后只剩 +27.69bp/笔，且 DD 增加 8.61pp，
所以它不是当前“收益/回撤双优”方案。若未来采用，必须先接受更大的资金路径风险。

## 随机对照与统计门说明

每笔交易匹配 3 个不复用随机入场，条件为同 ETH、同 UTC 月、同香港 6 小时时段、用前一个 UTC 月
形成的同 ATR 五分位、相同方向与复制持仓 horizon；策略与对照共用 stop、BE、TP、tick 和 20bp 成本。
每个 arm×年份跑 32 个确定性分配种子，再对 UTC 周做 20,000 次 sign-flip。

- 六线 W8：2023 超额 +73.18bp、`p=0.0486`；2024 超额 +182.54bp、`p=0.0053`；32 个种子
  的超额 P05 均为正，但只有 2024 过 `p<0.01`。
- TP30+ATR3：2023 超额 +73.80bp、`p=0.0914`；2024 +110.99bp、`p=0.0122`；两个年份都
  未过严格门槛。
- W16：2023 通过、2024 失败，直接暴露阶段依赖。

因此没有任何新配置获得 promote 资格。AUC、top-decile 毛/净收益在本轮不适用：用户明确暂停标签经济
审计，且本轮没有训练或评分模型。这里用可执行动态账本、匹配随机净收益、周置换与去 top1 代替，避免
编造一个不存在的分类验证集。

## “保留管线、跳过第二步、专门训练”分别是什么意思

1. **保留自动标签管线**：335 个候选仍可由程序自动判断 +1.5%/stop/opposite first-touch，供以后研究；
   不等于把 ATR expansion 写进交易脚本。淘汰的是 `atr_pct_ratio96 >= 1` 这个 entry gate。
2. **第二步已按要求跳过**：本轮没有继续做 P0/P1 标签经济性拆分，也没有要求人工审核。
3. **“专门训练”不是不能用 LR**：意思是不能拿现有不相容的 short-side YOLO/LightGBM 直接套 335 个
   Pine 候选。未来要用同一 Pine raw surface、同一标签/结算目标、时间切分，重新训练 Pine 专用 LR。
4. **现在未训练**：项目仍在 P0→P1，`active_bundle.json` 不存在，训练与 promote 被 fail-closed；335 行
   也不足以支持大量自由度。六线交叉净数、churn、breadth、当前排列、六线带宽及带宽变化已经准备好，
   等 P1 放行后才进入 LR 单变量/正则化消融。

## 数据、方法与证据边界

- 主数据：OKX `ETH-USDT-SWAP` 15m，104,962 行；分析最后一根 2024-12-31 23:45 UTC。
- 选择：只用 2023H1/H2；2024H1/H2 在配置锁定后展示，但属于已查看开发证据，不是新鲜 OOS。
- 未读取：2025-01-01 之后 consumed-final 0 行；项目 holdout（>=2026-05-04）0 行。
- 执行：next-open；20bp 往返成本；slippage=0；funding 未建模；BE +1.5% 触发、下一 bar 锁 +0.1%；
  反向信号反手；bar magnifier 关闭。
- 原始附件显示 `ETHUSDT.P`，但没有指定 TradingView 交易所；本地 OKX 只是研究代理，不能声称逐 bar 一致。
- TradingView 逐笔 parity 尚未通过；本轮 Python 候选不可直接粘贴成生产 Pine 参数。
- Luna Max 做了只读独立复核，确认 V9 信号因果、指出原脚本不是六线、建议区分 12 个跨周期 pair，
  并发现固定 TP tick 与 canonical runner 的易错点。本线程本地验证后才采用其意见。

## 现在怎么继续优化

1. **冻结六线 W8 为唯一新 forward hypothesis。** 先实现研究版 Pine parity；前向至少积累 100 笔新鲜
   交易，再判断是否替代 V9。不要再用 2024 微调 W8 阈值。
2. **先拆门控语义，再谈组合。** 当前 gate 同时改变新入场与反转退出，2023 W8 持仓中位数从 46 根变
   103 根。下一轮做 entry-only 与 full-signal 两臂，确定收益来自筛入场还是延长趋势。
3. **暂不把 W8 与 TP30+ATR3 打包。** 两者都是独立变量，组合会失去归因；且 ATR3 已显示更高 DD。
   若 owner 明确批准组合，先固定 W8，再只加 TP30，最后才单独考察 ATR3。
4. **LR 放到 P1 后。** 第一批只放六线交叉净数、反向 churn、alignment、bandwidth、价格离 120 组 ATR
   距离；用 2023 calibration→2024 evaluation，不读 holdout，不以 AUC 单独裁决，而以 top-decile 成本后
   净收益、匹配随机与动态状态机验收。

进一步问题：六线 W8 的改善有多少来自拒绝坏入场，有多少来自拒绝过早反转？这是下一轮最值得回答的
核心逻辑问题；它比继续扫几十个 TP/SL 数字更能提高真实可迁移性。

## 逐笔交易与复现

完整逐笔文件不是抽样：最终 5 个方案×2 年共有 **736 笔**，锚定匹配对照 **2208 行**；
每行包含 signal/entry/exit 时间与价格、方向、持仓 bars、止损/TP、退出原因、杠杆、毛/净收益、手续费与
碰撞标记。

- [全部逐笔交易 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_tbsl_final_trades.csv)
- [逐笔匹配随机对照 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_tbsl_final_controls.csv)
- [逐交易聚合匹配对 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_tbsl_final_pairs.csv)
- [32-seed 对照敏感性 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_tbsl_control_sensitivity.csv)
- [335 候选六线/7-EMA 特征 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_candidate_features.csv)
- [全部交叉门网格 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_gate_search.csv)
- [全部分阶段 TP/SL 网格 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/trend_tbsl_staged_search.csv)
- [机器可读总结果 JSON](/Users/zhangzc/fable-trading/experiments/active/exp-pine-eth-15m-v1/results/cross_count_tbsl_optimization.json)

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_pine_cross_features.py \
  tests/test_research_pine_eth_15m.py \
  tests/test_pine_cross_count_tbsl_artifacts.py

PYTHONPATH=. .venv/bin/python scripts/optimize_pine_eth_15m_cross_count_tbsl.py
PYTHONPATH=. .venv/bin/python scripts/build_pine_eth_15m_cross_tbsl_report.py
python3 scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_cross_tbsl_optimization_20260821.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 2024 是锁定检查，但整个研究家族此前已多次查看 2024；它不是一次新的独立 OOS。
- W8 是 2023 排名第二名，不能因 2024 更稳定就改写成 2023 winner；它只获得 forward 候选资格。
- 六线门会改变反转退出，当前结果不是纯 entry feature attribution。
- TBSL 的更高回报伴随更高杠杆与 DD；不能只报最终净值。
- 无 funding、slippage 与 TradingView venue parity；20bp 是固定研究成本假设，不是未来成交保证。
- 未读取 holdout、未训练模型、未 promote、未修改 ACTIVE、未触碰真金操作。
