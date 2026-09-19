# SPIKE V12.3 / V9.1：15m 增加已收盘 1h SMA60 入场过滤

2026-09-20。Owner 在上一轮参数研究后明确选择 SMA60，仅用于 15m，并要求新建 V12.3、同时升级独立 V9。两份新私有脚本已在 TradingView 保存并编译通过；这是授权的功能升级，不是新收益验证。

## 规则与版本对照

| 项目 | V12.2 / V9 | V12.3 / V9.1 |
|---|---|---|
| 15m 新入场 | 原过滤 | 原过滤且多头收盘 > 已完成 1h SMA60；空头收盘 < 均线 |
| 等于均线或均线未知 | 无此门 | 不开新参考框、不发该入场确认 |
| 非15m或关闭开关 | 原行为 | 跳过新门，保留原行为 |
| 原始反向确认退出 | 保留 | 保留，即使反向新入场被 SMA 拒绝 |
| 风险、成本、趋势线、配对规则 | 原规则 | 不改；被拒绝的 V9 不再创建新的可配对框 |
| 原脚本 | 保留 | 新建文件与 TV 私有脚本 |

开关默认开启。使用同交易所、同合约原生 1h close SMA60，要求连续60根有效小时数据；取上一根已完成小时的均线，在15m当前小时内保持一致。使用 `request.security` 表达式内部 `[1]` 配合 `lookahead_on`，并检查其收盘时间等于当前小时开盘时间，防止旧值跨缺口使用。依据 [TradingView 高周期数据说明](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。

仅过滤最终 V9 入场确认，原始反向事件仍能结束旧参考。V12 的“已有框→后来突破”可以继续使用先前获准的框，并不在后来突破时重新检查 SMA；本次没有把入场过滤改成持仓退出或联合信号的持续重审条件。

## 验证

- 源码先提交：`1e1f1bab5d1e0c28ccde7f101e60de7df28ded11`。父版本完整保留；排除明确的新门、诊断行和版本号后，其余有效源码逐行相同。
- 38项相关检查通过（0.45s）：已完成小时的可见时点、未知/相等/缺口、前缀因果、反向退出与新旧源码契约。Luna Max 独立读取两个入口和下游消费者，无阻塞问题。
- TV 私有 `SPIKE V12.3 · 15m · 1h SMA60` revision2，06:15:31保存，编译0错误；保留原有 `second` 变量遮蔽警告。首次保存发生粘贴/保存不同步，revision1为占位内容，最终revision2才是本轮源码。
- TV 私有 `SPIKE V9.1 · 15m · 1h SMA60` revision1，06:18:50保存，06:18:51编译完成并加到图表，无错误或警告显示。
- OKX BTC15m，两版设置默认开启，面板均显示 `79309.1 · 仅允许多头`。V12.3设置关闭再开启已核对；切到1h两版显示 `仅15m启用`。最后恢复15m，当前图仅保留V12.3，V9.1仍在私有脚本库，06:22:41保存布局。
- UI证据为本会话原生截图和可访问性观察，详见 `verification_receipt.json`；未把观察伪称为磁盘截图、全历史事件导出或完整原生逐笔一致性。

## 复现

```bash
git show 1e1f1bab5d1e0c28ccde7f101e60de7df28ded11:yoyo/evaluation/pine/spike_burst_v12_3.pine
git show 1e1f1bab5d1e0c28ccde7f101e60de7df28ded11:yoyo/evaluation/pine/spike_burst_v9_1.pine
.venv/bin/python -m pytest -q tests/evaluation/test_spike_h1_sma60_pine_contract.py tests/evaluation/test_spike_v9_htf_sma.py tests/evaluation/test_spike_v12_2_pine_contract.py tests/evaluation/test_spike_v12_pairing_reference.py tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v11_box.py
```

在 TradingView 为两文件各建一个私有指标，粘贴、等待编辑器内容更新后保存编译；在15m检查默认开关，在1h检查跳过提示，返回15m。原脚本不覆盖。源文件SHA与大小见实验目录收据。

## 风险与诚实声明

本轮是固定入场条件的版本交付，未新采交易样本、未训练、未搜索参数；候选数/正类率/val样本数/AUC/p值/top-decile毛净收益/胜率及随机交易对照不适用。相应功能对照为完整父版源码还原契约、现有因果行为测试与原生15m/1h切换，不能据此推出收益改善。

前次29币研究使用完整5m桶合成高周期，本轮TV使用原生1h；两者缺失数据与交易所来源可能不同，不能承诺逐笔相同。SMA60是Owner在看过全历史后指定的值，不宣称样本外最优。回测收益结论仍以 `analysis/p1_spike_v9_htf_sma_20260920.md` 的限制为准。

没有模型训练、生产准入、VPS/监控部署、新建报警或实际下单。用户已取消例行HTML交付，本轮只保留Markdown与收据。

## 后续

本次功能交付已完成。如需证明组合策略收益，应另行固定V12.3全链路定义、原生行情及时间分段做逐笔回放，不能把纯V9过滤研究直接当V12联合策略收益。

知识库新版本记录：[SPIKE V12.3 / V9.1交付](https://app.notion.com/p/3e08856479af81808071e4ea7014f6ed)。
