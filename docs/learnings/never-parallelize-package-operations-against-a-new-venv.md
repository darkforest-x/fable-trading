# 新 venv 完成初始化前不得并发包操作

- **问题**：新建独立评估 venv 后，并行启动两个 `pip --dry-run`；其中一路在 `bin/python` 已出现但 `pip` vendor 文件尚未复制完整时进入，报 `ModuleNotFoundError: pip._vendor.pyparsing`。
- **死胡同**：用“`bin/python` 已可执行”作为环境就绪信号不够；它只证明解释器入口已生成，不证明 `venv` / `ensurepip` 的全部文件已经落盘。
- **有效路径**：先让 venv 创建命令完整退出，再用 `python -m pip --version` 验证；同一环境内的解析、安装与校验串行执行。只有目标目录完全不同的环境或工具安装才并行。
- **通用规则**：并行化按“写入目标”划分，而不是按命令名划分；任何共享 site-packages、包缓存元数据或环境目录的操作都先串行。
- **牵连**：`pip --dry-run --report -`、Python `venv` / `ensurepip`、`/Users/zhangzc/.local/share/fable-trading/venvs/p1-label-quality`；项目主 `.venv` 未参与。
