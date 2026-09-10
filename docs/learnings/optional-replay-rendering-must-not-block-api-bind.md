# Optional replay rendering must not block API bind

- **问题**：监控服务重启后超过 90 秒仍未监听本机端口，进程采样全部停在 Python import/dlopen。
- **死胡同**：先把延迟归因于状态接口的 SQLite 读取；状态快照已经将该读取移出请求处理器，仍无法解释监听器尚未创建。
- **有效路径**：沿 API 顶层导入链检查，发现历史回放图模块会加载 pandas、NumPy、PyArrow 和冻结评估特征。把该模块改为仅在选中 replay 图卡时导入，使服务先绑定端口；用隔离 Python 进程断言 API 导入不会加载这些依赖。
- **通用规则**：遇到服务在监听前停顿，先审计顶层导入及其原生依赖；可选的报告、图表和评估代码必须在对应请求路径按需加载。
- **牵连**：`yoyo/monitor/server.py`、`yoyo/monitor/replay_chart.py`、`tests/monitor/test_api_startup_imports.py`；回放图仍只读取冻结 OHLC，未改其来源或因果语义。
