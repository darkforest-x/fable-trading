# 启动虚拟环境 Python 时保留 venv 路径

- **问题**：离线训练后处理 watcher 的启动器对 .venv/bin/python 调用了 Path.resolve()，记录显示实际启动系统 Python，后续统计依赖可能无法导入。
- **死胡同**：把解释器路径规范化成真实文件路径。虚拟环境常用指向系统解释器的软链接；入口路径本身决定 pyvenv.cfg 的发现，解析软链接并不保留环境语义。
- **有效路径**：在 watcher 仅做只读训练轮询时停止该 watcher，保留错误启动记录；采用绝对但不解软链接的 .venv/bin/python 路径重启。控制指标子进程也显式使用仓库 venv 路径。
- **通用规则**：Python 可执行文件的身份不仅是最终二进制字节，还包括启动路径与环境。检查 argv、sys.executable 与依赖上下文；不要对 venv 入口调用 resolve()。
- **牵连**：scripts/research/watch_ma_profit3r_owner1500_v4.py；原3060训练从未中断，错误 watcher 未执行评估。
