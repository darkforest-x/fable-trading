# Windows PowerShell 的 ErrorActionPreference 不会替原生命令验退出码

- **问题**：Windows PowerShell 5.1 中，`pip` 找不到 CUDA wheel 后返回非零，但 `$ErrorActionPreference = "Stop"` 没有终止脚本；脚本继续安装 Ultralytics，留下一个看似完成、实际可能是 CPU torch 的环境。
- **死胡同**：把 `$ErrorActionPreference = "Stop"` 当成 Bash 的 `set -e`。它主要约束 PowerShell 错误流，不会可靠地把每个原生可执行文件的非零退出码变成 terminating error。
- **有效路径**：所有 `python -m pip`、`msiexec`、`icacls` 调用都通过统一 helper 执行，命令返回后立即保存并检查 `$LASTEXITCODE`，非零就抛错；总结分支有失败时显式 `exit 1`。
- **通用规则**：自动化脚本调用原生程序时，成功条件必须来自该程序的退出码和产物自检，不能从宿主 shell 的错误偏好推断。
- **牵连**：`scripts/windows/setup_3060.ps1`；尤其是任何可能改变 torch flavor 的依赖安装步骤。
