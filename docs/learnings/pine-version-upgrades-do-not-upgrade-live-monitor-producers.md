# Pine 升级不代表信号中心的生产者也已升级

- **问题**：2026-09-23 Owner 发现信号中心有信号，而 TradingView 的 SPIKE V12.8 没有。只读核对 `http://127.0.0.1:8766/#signals`，主列表实际仍是 V9；实时 API 的 `protocol=spike-burst-v9-monitor-v1`，事件的 `strategy_version=spike-v9-entry-bundle-20260915-v1`。另一菜单“突破+spike”的生产者则保留 V11.2 身份。
- **死胡同**：从最新 Pine 文件、页面打开了 TradingView，或脚本内部沿用的 V9 变量名推断两端一致。这些都不能证明扫描器实际使用同一谓词；同样，找到一个新增过滤条件也不能解释所有不一致。
- **有效路径**：沿实际页面、API 事件身份、生产者 import、Pine 默认条件核对。监控 `v9_signals.analyze()` 明确丢弃 `higher`，而 V12.8 默认对 15m 新信号要求多头收盘高于、空头低于上一根已完成 H1 的 SMA60。用现有只读 `/api/chart?symbol=...&timeframe=1H`，选信号 bar 开盘所在小时之前连续完成的 60 根 H1 复算：NEIRO-USDT-SWAP 2026-09-23 北京时间 20:00 收盘空头，价格 0.00009625，SMA60 0.0000959465；SSV-USDT-SWAP 19:45 空头，价格 3.297，SMA60 3.2742666667；两条均会被该默认门拒绝。对应事件 ID 为 `5f428197c2ba21c11a09de1a`、`a0540595e9d3a4240925d698`。同批 LDO 20:15 与 ETH-USDT 19:45 通过这道门，不能据此解释它们在 TV 的显示。
- **原生实例核验**：随后只读检查当前 TradingView 桌面窗口，确为 OKX:NEIROUSDT.P、15m、SPIKE V12.8；输入设置中“15分钟启用1小时 SMA60 方向过滤”为开启，“确认信号与价格”为开启，“仅显示突破+spike 信号”和极简模式均关闭。取消设置检查并恢复侧栏，没有修改参数。该证据支持当前 NEIRO 的方向门解释，不等于整个 Python/Pine 引擎已完成逐事件 parity。
- **通用规则**：先按交易所、合约、周期、信号 bar 开收盘时间及实际协议识别同一事件，再逐门比对。页面使用收盘时间，TV 的 bar 时间需按开盘对齐。区分“源码存在”“服务加载”“逐事件一致”三种证据；未经用户具体图表实例及设置核验，不宣称原生 TV parity。升级需处理事件身份、历史保留、切换时间和通知去重，不能只改版本标题或静默覆盖旧账。
- **牵连**：`yoyo/monitor/__init__.py`、`yoyo/monitor/v9_signals.py`、`yoyo/monitor/spike_lines.py`、`yoyo/evaluation/pine/spike_burst_v12_8.pine`。本次仅诊断，没有切换监控算法、清理历史、重发通知或改 Pine。不是收益研究，不形成交易优势结论。
