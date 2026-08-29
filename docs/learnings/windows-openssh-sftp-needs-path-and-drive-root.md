# Windows OpenSSH 的命令通不代表现代 scp 能传文件

- **问题**：公钥 SSH 与远程 PowerShell 均正常，但现代 `scp` 立即断开；修好 SFTP 后，`host:C:/fable/...` 仍不能稳定表达 Windows 盘符路径。
- **死胡同**：加 `scp -O` 退回 legacy SCP。它只能绕过 SFTP，掩盖服务端 subsystem 配置问题，并把未来兼容性继续押在旧协议上。
- **有效路径**：Win32-OpenSSH MSI 安装目录加入 Machine PATH 后重启 `sshd`，让相对配置的 `sftp-server.exe` 可解析；远程 PowerShell 保持 `C:/fable/...`，SFTP/scp 单独使用根路径 `/C:/fable/...`；分别做 SSH exec、SFTP list、scp 上传与哈希复核。
- **通用规则**：Windows OpenSSH 验收要拆成四门：认证、命令 shell、SFTP subsystem、双向文件传输。前一门通过不能替代后一门。
- **牵连**：`scripts/train_on_3060.sh`、`scripts/windows/setup_3060.ps1`、`docs/ops/RECONNECT_3060.md`。
