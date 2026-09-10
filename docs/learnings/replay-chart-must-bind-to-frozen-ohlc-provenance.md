# 回放图必须绑定冻结 OHLC，而不是当前市场缓存

- **问题**：历史 replay 信号只带 signal-only 账本字段；用同一合约的当前 OKX 缓存画图会把不同 venue、不同时间的 K 线伪装成当时上下文。
- **死胡同**：只显示“不可预览”。它避免了错误图，却没有满足用户回看原始信号与之后走势的需要；也不能从回测交易结果倒推或补造 K 线。
- **有效路径**：以已经导入的 replay event ID 为唯一入口，服务端从事件读取 venue、symbol、周期与信号 bar，再懒加载本轮冻结的 normalized gzip。Binance/OKX 从冻结 30m 按原 epoch 规则聚合，Gate 使用其冻结的同周期直接文件；响应附文件 SHA 与实验来源，并把之后 K 线标为仅供历史回看。
- **通用规则**：回放可视化必须由事件身份绑定输入来源和时间，不接受客户端宣称的市场或时间；任何未来显示必须与模型输入和实时通知语义物理分开。
- **牵连**：`yoyo/monitor/replay_chart.py`、`server.py`、`store.py`、`static/app.js`、`tests/monitor/test_replay_chart.py`；不读取交易收益、退出、费用或回测经济账本。
