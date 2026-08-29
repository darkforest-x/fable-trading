# OpenSSH 是否可用先看服务，不要只看 Windows capability

- **问题**：build 26200 上 `Add-WindowsCapability` 长时间不返回，capability 仍显示 `NotPresent`；安装微软签名的 Win32-OpenSSH MSI 后，`sshd` 已经正常运行，但按 capability 状态重跑脚本仍会再次卡住。
- **死胡同**：把 `Get-WindowsCapability` 当成所有 OpenSSH 安装方式的统一事实源。MSI 安装与 inbox capability 是两套状态，后者不会描述前者。
- **有效路径**：幂等脚本先检查 `sshd` service；服务不存在才尝试 capability 或显式提供的 MSI。MSI 使用前核对 Authenticode 为 Microsoft 且安装后重新检查 service；Preview 构建只在 LAN 受限训练箱使用并记录迁回计划。
- **通用规则**：同一能力有多个安装通道时，运行态验收优先于包管理器状态；包管理器只负责决定安装路径，不能覆盖已经通过实测的服务事实。
- **牵连**：`scripts/windows/setup_3060.ps1`、`docs/ops/RECONNECT_3060.md`。
