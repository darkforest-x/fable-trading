# 可复现测试结果必须绑定仓库解释器

- **问题**：同一工作树用系统 `python3` 跑测试会因缺少 `torchvision` 产生 dependency failure，而仓库 `.venv` 已固定兼容的 `torch`、`torchvision` 与 `ultralytics`，不删减测试即可全绿。只报告 pytest 数字而不报告解释器，会把环境缺口误写成代码缺口。
- **死胡同**：直接 deselect 缺依赖测试只能描述“当前系统 Python 可运行集合”，不能证明仓库预期环境完整，也不能判断该依赖是否属于后续 detector 路径。
- **有效路径**：先枚举系统解释器和仓库 `.venv` 的包版本，再检查失败测试与候选生成的依赖图；使用 `.venv/bin/python -m pytest` 重跑完整集合，并在测试前后核对受保护文件哈希。
- **通用规则**：任何验收报告都同时记录 Python 可执行文件、关键包版本、完整命令与 skip 原因；仓库存在受控虚拟环境时，先用它复现，再决定是否属于真实依赖阻塞。
- **牵连**：`.venv/`、`tests/conftest.py`、`tests/test_eth3m_v2_classification.py`、15m YOLO candidate build 的 `ultralytics` 运行时、P0/P1 报告中的测试覆盖边界。
