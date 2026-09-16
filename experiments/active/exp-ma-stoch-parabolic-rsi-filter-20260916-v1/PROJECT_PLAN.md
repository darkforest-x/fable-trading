# MA + Stoch 新增 Parabolic RSI 区域过滤

## 授权与单变量

Owner 2026-09-16：再加 Parabolic RSI [ChartPrime]，只有超卖做多、超买做空。
本轮仅改变入场放行；退出沿用已选 wide_tp2_no_arrow：5m ATR14 × 6 初始止损，2R 全平止盈，无反向箭头退出。
单仓、1倍权益名义、1000 USDT、开平各10bp、0.01价格步长全部保持。无训练、部署或真金操作。

## 事前冻结的解释

- RSI 周期5m，长度14，close源，严格 RSI<30 允许多，RSI>70 允许空；等于阈值不放行。
- 在 Stoch 箭头那根5m收盘同时检查 RSI；没有记忆窗，不以之前曾超卖代替当前超卖。
- 仍需最近已完成15m MA Shift SMA40/hl2颜色方向允许及原 Stoch5/3/3 极区箭头。
- 信号确认后下一根5m open成交（同一时刻边界）；过滤只限制新入场，持仓中不因RSI退出。
- 这是公开默认参数假设。当前 TradingView 会话断开且图上无指标，未核实用户保存的实时参数。
- 用户未指定强信号大菱形。已经询问区别；本版按“RSI曲线处于区域”执行，不把 SAR 翻转事件混入。
- 源码：`experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/NI0Qhwy7.pine`，SHA256 `ca5785ff28a10a51c9d08ec60af14491e62ca50bb56ed3bbc39698aadde51098`。
- 官方 https://www.tradingview.com/script/NI0Qhwy7-Parabolic-RSI-ChartPrime/ 。该脚本 RSI 使用 ta.rsi(close,14)，强菱形还要求SAR翻转及SAR值越阈值；两者不是同一过滤。

## 数据与比较

仅原生OKX ETH-USDT-SWAP 5m已有源；timestamp-first读价至2026-05-01之前。
1—2月与3—4月独立空仓开始，预热自2025-12-20；两段此前都看过，属于探索历史复用。
不读取近一月或其他holdout价格。本配置holdout消耗0。
两组事前冻结：unfiltered（上一版完整规则）、rsi_zone（增加本过滤）。不搜索RSI阈值/周期，不按后段择优。
先提交builder/config/protocol/tests，再运行。两组、两段全披露，即使0交易也不改阈值凑样本。
保留候选箭头、MA通过、RSI通过、实际成交计数；串行占仓改变后续机会，因此不把净差完全归因于被删单。

## 对照、验收与风险

每组使用同币同方向同UTC日同历史波动桶随机入场，同6ATR/2R和费用；一次哈希抽样，不因未平/亏损重抽。
种子/波动桶/9999日块符号置换沿用v2。随机对照是事件级，有重叠，不能当单仓账户。
无评分模型，AUC和top-decile不适用；全样本毛/净均值、胜率、PF、收益、回撤、连亏/连止损、随机超额必报。
指标少于30笔只描述，不声明稳健；无论多少笔，本轮都不是独立验证。
账户净收益含末仓估值和预留费用；5m收盘回撤非盘中最大回撤。OHLC双触发止损先；跳空实际open。
费用不包含资金费率、额外滑点或冲击，实际执行可能更差。
只声称按公开源码公式移植RSI，不声称实时TradingView逐bar parity。

## 复现

```bash
.venv/bin/python -m pytest -q tests/test_ma_stoch_rsi_filter.py tests/test_ma_stoch_exit_v2.py tests/test_ma_stoch_exit_engine.py tests/test_ma_stoch_exit_study.py tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py
git branch --show-current
# main; only explicitly owned builder/protocol/test files are staged and committed.
.venv/bin/python -m yoyo.evaluation.ma_stoch_rsi_study --phase dev
.venv/bin/python -m yoyo.evaluation.ma_stoch_rsi_study --phase recheck
```

引擎拒绝覆盖目录。复现输出可用 --output 指向本实验下新的命名目录，不改既有冻结产物。
交付逐笔/候选/随机对照/摘要/账本验算、Markdown与HTML；密集曲线本地保留并在manifest绑定哈希。
