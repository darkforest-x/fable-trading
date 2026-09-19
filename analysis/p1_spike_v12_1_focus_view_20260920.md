# SPIKE V12.1 简洁看盘

原 V12 历史默认30组且向右延伸48根，局部合并 bk 后仍在宽视图形成扇形线群。V12.1 独立文件及 TradingView 私有脚本交付；V12 原文件 SHA256 未变。

|项目|V12|V12.1 默认|
|---|---|---|
|历史趋势线|默认30组共用池|最近联合1组、其后最新普通突破最多1组|
|当前候选|本周期与上级可各1条|全局最多1条，本周期优先|
|历史右延|48根|止于确认根|
|ABC|最近8组|最新保留事件1组|
|复盘|默认全历史|切换完整复盘恢复原显示控制|

52项本地检查通过，新增契约对照证明识别、配对、风险、事件计算段与V12一致（版本名称归一）。Luna Max只读复核类别裁剪、候选优先级与对象清理，未发现阻断问题。明确边界：主动开启配对调试标签或诊断面板文字仍可显示；全候选诊断线在简洁模式硬关闭。均线、BB、盈亏框和退出R继续按原设置显示。

TradingView 新建 `SPIKE V12.1 · 简洁看盘`，revision1，2026-09-20 04:18:54 北京时间编译保存，无错误；保留旧有 `second` 命名警告。OKX BTC15m 约9月2日至20日视口覆盖原截图区间，简洁模式实际2条自动趋势线；04:21:14切到完整复盘恢复历史扇形线群；04:22:21切回简洁模式，2条线恢复。移除当前图表上已隐藏的旧版实例，旧私有脚本仍保留。

## 复现

源提交 `a65eb0f8edb33cf4c9983cd2e615f71e0ea8694d`。不拉取新行情；TV原生图表数据。测试命令：

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v12_pairing_reference.py tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v12_pine_contract.py tests/evaluation/test_spike_v12_1_pine_contract.py tests/evaluation/test_spike_v10_4.py tests/evaluation/test_spike_v11_box.py
```

TV新建指标，粘贴 `yoyo/evaluation/pine/spike_burst_v12_1.pine`，另存新名称后添加OKX BTCUSDT.P15m；调整时间轴覆盖9月4日至20日；在输入的04B切换简洁/完整复盘/简洁。源码与原生操作凭据见同名实验目录JSON。

## 风险与诚实声明

这是显示功能验证，非收益实验；样本数、胜率、AUC、置换p和随机入场对照不适用。严格对照为相同数据视口切换显示模式，以及计算段逐字比较。截图只在会话中展示，未保存磁盘截图，不将手工观察冒充逐事件导出或广泛策略验证。未修改止损、成本、阈值或生产配置，无盈利改善结论。完整复盘会按设计恢复较多历史线。

后续每次交付升小版本，保留旧文件并在TV新建独立脚本。知识记录：https://app.notion.com/p/3e08856479af8130b6ecfa78e42d12f5
