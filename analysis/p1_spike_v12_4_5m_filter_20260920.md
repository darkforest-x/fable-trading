# SPIKE V12.4：5m加入已完成15m EMA120过滤（2026-09-20）

已交付新的 TradingView 私有脚本 **SPIKE V12.4 · 5m方向过滤** revision 1。原生同根开关对照确认，2026-08-19 14:00 北京时间的 ETH 5m 空头参考被新门拦截。此结论是功能验收，不是盈利或最优参数声明。

## 授权与固定改动

Owner：“你先优化好5min得逻辑吧，加上过滤，保存到tv”。采用前次六线研究中历史描述性表现较好的 EMA120 作为固定功能方案；没有将前次未通过结论改为通过。唯一行为变量为5m最终入场方向许可，配套增加方向线和状态说明。

| 行为 | V12.3 | V12.4 |
|---|---|---|
| 5m最终入场 | 原有门 | 原有门再加已完成15m EMA120，默认开启 |
| 多头/空头方向 | 无此上级门 | 5m确认收盘严格高于线才多、低于线才空；相等/未知不新入场 |
| 15m | 已完成1h SMA60 | 保留 |
| 原始反向事件退出 | 保留 | 保留，不受新入场门阻断 |
| 风控、成本、趋势线历史、既有框后配突破 | 原逻辑 | 保留 |
| 源码与TV脚本 | 独立V12.3 | 新建独立V12.4，前版保留 |

5m主图淡紫色阶梯线表示上一根已完成15m的 EMA120。关闭“显示15分钟方向线”只隐藏图线；关闭过滤开关才绕过新准入。没有加入 SMMA、斜率或新的止损参数。

## 因果口径

同交易所原生15m OHLCV，连续有效1200根预热；EMA以首个close播种，alpha=2/121，无效bar或时间缺口重置。`request.security`表达式内部使用`[1]`与`lookahead_on`，已知上级收盘时间必须等于当前`time("15")`。新门只在5m启用，不用正在形成的15m均线回填历史。

## 验收证据

- 源码先提交：`c66fce874f53e10e117d84ec9fb5b3996bd6af5a`。
- V12.4 SHA256：`1adac144ecdcd3bd5d79a9eb37f765f70a22f916bcfd1217ff5815458f0fbddc`。
- V12.3原文件 SHA256：`ed7999159574e1d3a4c848a46f55d6c096fa3aca0cfeb57c75f16a414b5c3d75`，未修改。
- 48项相关检查通过（0.53秒），包含新增源码还原契约、原有EMA/因果/缺口/反向退出和V12行为检查。移除新增门、显示块及版本字样后与前版源码逐字节一致。
- 独立`budget-mapper`（Luna Max）只读复核，无阻塞逻辑问题；边界保存在`independent_review.json`。
- TV于2026-09-20 12:39:06北京保存新私有脚本revision 1，编译无错误，保留原有954:1 `second`变量遮蔽警告。
- 原生5m开关往返：关闭过滤后紫线消失，开启后恢复。15m切换时无5m紫线，仍显示原1h SMA60，观察值2569.58、方向仅多头。
- 最终恢复OKX ETH5m、新过滤开启，12:47:27布局显示“所有更改已保存”。当前图使用V12.4，旧私有脚本仍在库中。为历史价格视图启用“仅缩放价格图表”，用户三条手绘水平射线保留。

原始案例：5m bar开盘时间2026-08-19 14:00北京，14:05确认；O1909.45 / H1910.29 / L1908.01 / C1908.14。

| 同根原生数据窗口 | 过滤关闭 | 过滤开启 |
|---|---:|---:|
| 已完成15m EMA120 | 门关闭不显示 | 1907.87 |
| V9全部入场过滤通过 | 1 | 0 |
| 确认实际收盘价 | 1908.14 | 空 |
| 确认参考起点 | 1 | 0 |
| 空头确认点 | 出现 | 不出现 |

该根收盘1908.14高于1907.87，因此新方向门拒绝空头。证据来自同根数据窗口，不能用随最新bar更新的状态面板代替历史值。完成A/B后再次开启过滤并保存。

## 复现命令与证据定位

在本仓现有契约环境中运行；无需拉新行情、安装依赖或切换分支：

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v12_4_pine_contract.py tests/evaluation/test_spike_v9_5m_15m_ma.py tests/evaluation/test_spike_h1_sma60_pine_contract.py tests/evaluation/test_spike_v9_htf_sma.py tests/evaluation/test_spike_v12_2_pine_contract.py tests/evaluation/test_spike_v12_pairing_reference.py tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v11_box.py
shasum -a 256 yoyo/evaluation/pine/spike_burst_v12_3.pine yoyo/evaluation/pine/spike_burst_v12_4.pine
```

原生步骤：在已登录TV打开交付图表，选OKX ETH5m及V12.4；定位上述bar，观察数据窗口，分别开关过滤；切15m确认旧H1门；恢复5m和新过滤开启，保存布局。此为人工/UI复验步骤，不是自动化脚本。

实验目录：`experiments/active/exp-spike-v12-4-5m-filter-20260920-v1/`。`native_qa/`保留原生截图与AX原文，PNG为本地证据，git保留文本及哈希清单；`delivery_manifest.json`绑定源码、报告和证据。经验记录：`docs/learnings/higher-timeframe-entry-filter-must-preserve-raw-reverse-exits.md`。

## 风险与诚实声明

- 本轮是功能交付，无新增市场候选池、正类率、时间切分或val样本。AUC、置换检验、top-decile收益、胜率和匹配随机入场指标不适用；对应零变化对照为前版源码还原契约、同根开关A/B及15m保留检查。不能将功能通过当作经济假设通过。
- 前次市场研究保持原结果，见`analysis/p1_spike_v9_5m_15m_ma_20260920.md`；没有新收益回测，也没有证明EMA120最优或能稳定盈利。
- 原生15m与Python由三根5m完整聚合的缺失数据语义，未做全量parity；原生相等、缺口、首个预热bar未逐一制造复现。未从TV导出全文逐字节核对，仅核对粘贴行数、新设置、编译及行为。
- 本次48项专项通过；此前六线研究全仓468通过、8项既有失败已经记录，本轮未重跑或宣称全仓全绿。
- 未创建报警、订单、训练、ACTIVE切换或生产部署。`training_eligible=false`，`production_eligible=false`。注册表inconclusive指收益有效性未确认，功能已完成；Notion“完成”也仅指功能交付。

## 下一步

当前授权交付完成。若后续研究SMMA、方向分别选线、斜率或收益改善，应作为新的单变量实验；风险成本及生产准入仍由Owner决定。

[TradingView交付图表](https://www.tradingview.com/chart/AlGc61US/)；[Notion交付记录](https://app.notion.com/p/3e18856479af81998e51e11e956e48c5)。
