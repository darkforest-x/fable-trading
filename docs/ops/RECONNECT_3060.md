# 重装后把 3060 接回来

> 2026-08-20 现场恢复记录与下一次重装手册。当前只完成训练机环境恢复，
> **没有启动训练、没有读取 holdout、没有 promote，也没有改 ACTIVE/frozen。**

## 当前真相（已实机验证）

| 项 | 当前值 | 验证结果 |
|---|---|---|
| 主机 / 用户 | `win-zzc` / `Administrator` | SSH 公钥免密登录成功 |
| Windows | Windows 11 Pro x64，build `26200` | 正常 |
| 局域网地址 | `192.168.1.3`（WLAN） | 当前可达；DHCP 仍可能漂移 |
| GPU | NVIDIA GeForce RTX 3060，12 GB | CUDA 计算与 NMS 实测通过 |
| NVIDIA 驱动 | `610.88` | `nvidia-smi` 正常 |
| Python | `3.9.13`，`C:\Program Files\Python39\python.exe` | 正常 |
| venv | `C:\fable\.venv` | `pip check` 无损坏依赖 |
| torch | `2.8.0+cu126` | `torch.cuda.is_available() == True` |
| torchvision | `0.23.0+cu126` | CUDA NMS 通过 |
| ultralytics | `8.4.89` | 与 Mac 契约一致 |
| numpy | `2.0.2` | 与 Mac 契约一致 |
| OpenSSH | Win32-OpenSSH Server `9.8.3.0` Preview | `sshd` Running + Automatic |
| 防火墙 | 入站 TCP 22，仅 `LocalSubnet` | IPv4/IPv6 均监听 |
| 远端根目录 | `C:\fable` | venv / trainer / datasets / logs 已建 |

Mac 侧完整验收命令：

```bash
FABLE_3060_HOST=Administrator@192.168.1.3 \
  bash scripts/train_on_3060.sh --check
```

通过只表示“机器与受控训练入口可用”。是否允许开训仍以 `HANDOFF.md` 和
`ROADMAP.md` 的阶段门为准；当前 P0/P1 阶段禁止新训练。

## 这次实际恢复了什么

### 1. SSH 管理面

- Mac 公钥写入 `C:\ProgramData\ssh\administrators_authorized_keys`。
- 该文件关闭继承，只保留 `SYSTEM:F` 与 `BUILTIN\Administrators:F`。
- `HKLM:\SOFTWARE\OpenSSH\DefaultShell` 指向 Windows PowerShell。
- 防火墙只允许本地子网访问 22，不向公网放开。
- Mac 已在 ToDesk 画面中核对新主机 ED25519 指纹：
  `SHA256:fQTPIk3nUVNYmPuRzt7DhZfZaRjKukRP9fHJn19karY`。
- 旧 `known_hosts` 已备份到
  `~/.ssh/known_hosts.before-win3060-reinstall-20260820` 后再替换。

Windows 内置 `Add-WindowsCapability` 在这台 build 26200 上长时间卡住，能力状态仍为
`NotPresent`。现场改用微软签名的 Win32-OpenSSH Server-only MSI：

```text
file    OpenSSH-Win64-v9.8.3.0.msi
bytes   6103040
sha256  c8a8c7e21136a099665c2fad9accb41152d129466b719ea71678bab665e03389
signer  Microsoft Corporation (Authenticode Valid)
```

这是 Preview 构建，当前仅用于受 LAN 防火墙约束的训练箱。Windows servicing 恢复后，
应评估迁回系统自带 OpenSSH；不要把这个回退解释成生产服务器基线。

MSI 的 `sshd_config` 使用相对的 `sftp-server.exe`，因此还把
`C:\Program Files\OpenSSH` 加入了 Machine PATH 并重启 `sshd`。否则命令行 SSH 会成功，
但默认使用 SFTP 的现代 `scp` 会直接断开。

### 2. Python / CUDA 契约

旧脚本使用 `cu124` index，但该 index 没有 Windows/Python 3.9 的 `torch==2.8.0`
wheel。PowerShell 5.1 又不会因为原生命令返回非零而自动服从
`$ErrorActionPreference = "Stop"`，于是后续依赖安装可能从 PyPI 补进 CPU torch。

本次实际使用 `cu126`，并把 torch 与 torchvision 一起锁定：

```powershell
$py = "C:\fable\.venv\Scripts\python.exe"

& $py -m pip install --dry-run --report C:\fable\logs\pip-torch-dry-run.json `
  torch==2.8.0 torchvision==0.23.0 `
  --index-url https://download.pytorch.org/whl/cu126

& $py -m pip install torch==2.8.0 torchvision==0.23.0 `
  --index-url https://download.pytorch.org/whl/cu126

& $py -m pip install --dry-run --report C:\fable\logs\pip-app-dry-run.json `
  ultralytics==8.4.89 numpy==2.0.2
& $py -m pip install ultralytics==8.4.89 numpy==2.0.2
& $py -m pip check
```

离线 torch wheel 已保留在 Mac 与 Windows 的恢复目录，便于以后免去 2.9 GB 下载：

```text
file    torch-2.8.0+cu126-cp39-cp39-win_amd64.whl
bytes   2915338284
sha256  44d44adc578013fe60280abf84bef359c8f9e490d1d734e4d624a5c976a8f275
```

### 3. 训练入口不再只存在于一台机器

从 Mac 备份恢复出的旧文件哈希是：

```text
795c5c8b3f8874afc383aaecaf24faec9f190f83d64164d0a457ba6f5f526180
```

审计时发现旧副本仍有 `hsv_s=0.05` / `hsv_v=0.05`，与当前“HSV 全关”的铁律冲突。
因此没有把它继续当不可见的远端特例，而是建立了仓库受控入口：

```text
scripts/windows/train_dense.py
sha256  92b58d629e78cae261b7ab5d5ad50db35f9c10535577f95642bc7c703e2204c6
```

受控版把 `hsv_h/s/v` 全设为 `0.0`、显式记录 seed，并继续强制 CUDA；本轮没有用它训练
任何模型。`scripts/train_on_3060.sh` 在 `--check` 时比较两端哈希，真正运行时也会先同步并
复核该文件，避免 Windows 再次成为不可追溯的代码源。

## 下一次重装的最短恢复路径

以管理员身份打开 Windows PowerShell，把仓库中的
`scripts/windows/setup_3060.ps1` 和官方 Python 3.9 安装包放到 Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_3060.ps1
```

如果 Windows capability 安装再次卡住，先取得微软签名的 Win32-OpenSSH MSI，再运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_3060.ps1 `
  -OpenSshServerMsi C:\fable\OpenSSH-Win64-v9.8.3.0.msi
```

脚本现在会：

1. 已存在 `sshd` 时跳过 capability 安装，避免在当前 MSI 状态下再次卡住；
2. 将 22 端口限制在 `LocalSubnet`；
3. 用与系统语言无关的 SID 收紧管理员公钥 ACL；
4. 对两轮 `pip install` 先执行 `--dry-run --report`；
5. 检查每个原生命令的 exit code；
6. 锁定 `torch 2.8.0 + torchvision 0.23.0` 的 cu126 wheel；
7. 检查版本、CUDA、torchvision CUDA NMS、GPU 与 `pip check`，任一失败以 exit 1 结束。

## SCP/SFTP 的 Windows 路径规则

PowerShell 远程命令使用：

```text
C:/fable/train_dense.py
```

现代 `scp` 的 SFTP 传输路径必须使用：

```text
/C:/fable/train_dense.py
```

因此主脚本把命令路径与传输路径分开保存。`scp host:C:/...` 在 legacy SCP 模式可能碰巧
工作，但在现代默认 SFTP 下会把盘符解释错；不要用 `scp -O` 掩盖 SFTP 子系统问题。

## 仍需 owner/运维决定的事项

- 在路由器上给 `win-zzc` 做 DHCP 预留；当前 `.3` 不是永久承诺。
- Windows servicing 修复后，决定何时从 Preview MSI 迁回 inbox OpenSSH。
- 等 P0/P1 阶段门通过后，再为第一轮 3060 实验注册 experiment_id 与单变量配置。
- 每次训练结束只取回产物并评估；`owner_best`、ACTIVE、frozen 切换仍需 owner 明确批准，
  脚本不再自动 promote。
