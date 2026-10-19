# 分目录通过的测试也必须能整仓收集

- **问题**：发布前合并运行纪律 App、监控和执行模块测试，发现多个目录使用 `test_service.py`、`test_live.py`、`test_source.py`，pytest 默认导入方式把它们当成同名顶层模块，发生文件身份冲突。
- **死胡同**：只分别运行各目录能通过收集，却看不到共享导入命名空间的冲突；清理 pycache 不能解决源文件名重复。直接切换 importlib 模式还会影响现有测试之间的辅助函数导入。
- **有效路径**：给冲突的测试文件增加模块前缀，并同步修改辅助函数导入；对另一处被第三方 `tests` 包遮蔽的导入，沿用该目录已有的直接辅助模块导入方式。整仓收集验证成功，保留策略测试实际失败，不修改策略规则来掩盖失败。
- **通用规则**：新增测试目录时检查模块名唯一性；交付前同时验证目标测试和整仓收集，收集成功不等于断言全部通过。
- **牵连**：`tests/discipline_app/test_discipline_service.py`、`tests/monitor/pro/test_pro_live.py`、`tests/okx_execution/test_execution_source.py`、`tests/evaluation/test_spike_v10_report.py`。
