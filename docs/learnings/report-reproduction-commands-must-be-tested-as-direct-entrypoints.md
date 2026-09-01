# 报告复现命令必须按直接入口执行测试

- **问题**：报告构建器在测试里能被 `scripts.*` 正常导入，但 Owner 报告记录的直接命令 `python scripts/build_*.py` 会把 `scripts/` 而不是仓库根放在 `sys.path`，因此在产物全部完成后才以 `ModuleNotFoundError` 失败。
- **死胡同**：只测试构建器里的纯函数和包导入。这能证明算法可调用，却没有覆盖报告真正交付给 Owner 的命令形态，所以错误一直潜伏到最终复现步骤。
- **有效路径**：入口先从 `__file__` 解析仓库根并显式加入 `sys.path`；同时用子进程执行真实脚本的 `--help`，让测试验证入口解析、顶层依赖导入和参数解析，而不触碰市场数据或重建产物。
- **通用规则**：凡报告写出 `python scripts/foo.py ...` 作为复现命令，至少增加一次该文件的直接子进程入口测试；模块级单元测试不能替代命令行入口测试。
- **牵连**：`scripts/build_15m_ma_launch_model_compare_all3d_report.py`、`tests/test_scan_15m_ma_launch_model_compare_all3d.py`；只影响报告可复现性，不影响已冻结的快照、模型推理、阈值、候选账本或高清图像素。
