# 3060 离线扫描的成功必须由终态 receipt 证明

- **问题**：五模型全币种扫描首次在 3060 上被提前终止以提高 batch 吞吐；Windows PowerShell 包装器随后写出了 `scan.exit=0`，但既没有 `scan_receipt.json`，也没有任何候选/episode ledger。若只读 exit code，收集端会把部分日志误当成完整 holdout 结果。
- **死胡同**：先按旧文档连接 `192.168.1.3`，主机在线但 SSH 端口拒绝；找到指纹一致的 `win-zzc` 新地址 `.5` 后，旧 RPC 又因 SFTP 把 `C:/...` 当错误远端路径、PowerShell execution policy 阻止 `.ps1` 而没有输出。改用 `Start-Process` 虽返回 PID，却没有可靠地启动子脚本；提前终止 child 后，父 PowerShell 的 `$LASTEXITCODE` 仍是零。
- **有效路径**：用已记录的 ED25519 指纹确认新地址，RPC 的 scp 路径固定为 `/C:/...`，显式传 `-ExecutionPolicy Bypass`，并用 WMI 创建脱离 SSH session 的 `cmd.exe /c powershell.exe` 扫描进程。最关键的是 worker 只有在 Python 退出且 `scan_receipt.json` 实际存在时才写 `scan.exit=0`；否则强制非零。收集端再只接受这两个条件同时成立的 archive。
- **通用规则**：Windows 远程长任务的“完成”不是进程退出码、更不是日志尾部，而是一个原子、可校验、由任务本身写出的终态 receipt。先验证这个 receipt，再允许下载或引用任何结果；中断/性能重启要单独留痕且不得消耗为正式结果。
- **牵连**：`scripts/ssh_ps.sh`（SFTP 根路径与 execution-policy）、`scripts/run_15m_ma_launch_model_compare_all3d_on_3060.sh`（WMI 启动/状态/收集）、`scripts/windows/run_model_compare_all3d_scan.ps1`（receipt gate）、`experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/results/aborted_gpu_attempt_001.json`；外部约束是 3060 的 DHCP 地址会变化、OpenSSH 的 SFTP 盘符语法及 PowerShell 5.1 的 child-exit 语义。
