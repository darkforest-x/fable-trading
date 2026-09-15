# 修复虚拟环境启动链接，要指向真实 Python 可执行文件

- **问题**：监控的旧进程仍在运行，但 `.venv/bin/python3` 指向已不存在的 Xcode Python，重启会失败。
- **死胡同**：把链接直接改为 `/usr/bin/python3` 后，经 `.venv/bin/python` 调用仍出现 `xcode-select: Failed to locate 'python'`。macOS 的 `/usr/bin/python3` 是开发工具定位入口，链接存在不等于能作为虚拟环境解释器运行。
- **有效路径**：用直接运行 `/usr/bin/python3` 的 `sys.executable` 找到 CommandLineTools 内的真实 Python 路径，再修复虚拟环境链接及 `pyvenv.cfg` 的 home；验证实际启动、Python 3.9.6、pandas 2.3.3、numpy 2.0.2，随后才重启服务。没有安装或更换依赖包。
- **通用规则**：服务重启前验证实际启动命令；修复解释器位置时同时核对可执行文件、虚拟环境配置和现有包版本，不把操作系统的定位入口当成真实解释器。
- **牵连**：本机 `.venv/bin/python3`、`.venv/pyvenv.cfg`、`com.fable.impulse-monitor`。环境链接属于本机状态，不提交到 Git。
