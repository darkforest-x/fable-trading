# Python 虚拟环境入口要 exec 环境解释器，不能随处软链

- **问题**：2026-09-07 安装独立 Crypto Toolkit 时，把 `venvs/web3-ccxt/bin/python` 软链为 `~/.local/bin/crypto-python`。命令能启动，`import web3` 却失败。
- **死胡同**：解释器软链存在、目标可执行，并不能证明它仍处于原虚拟环境。实测 `sys.prefix` 回到了 uv 的基础 Python 目录；只验路径存在会把失效入口当成功。
- **有效路径**：将入口改成 shell 包装器，`exec` 虚拟环境内 Python 的绝对路径，并原样传递参数。再次验证 `sys.prefix` 指向 `venvs/web3-ccxt`，web3 与 CCXT 导入成功。
- **通用规则**：跨目录发布 venv 的 Python 入口，使用 exec 包装器；验收要检查环境前缀和实际依赖导入。
- **牵连**：`~/.local/bin/crypto-python`、`~/.local/bin/nautilus-python`；安装环境在 `~/.local/share/crypto-toolkit/venvs/`，没有修改研究仓依赖。
