# 重装后把 3060 接回来

> 2026-08-20。Owner 重装了 3060 的系统。本文是从零恢复到「Mac 能一条命令开训」的步骤。
> **必须在那台 Windows 上操作**——目前 Mac 完全连不上它（所有端口关闭）。

## 现状（实测，2026-08-20）

| 项 | 结果 |
|---|---|
| Mac 自己 | `192.168.1.2`（`en0`） |
| 在线主机 | `.1` 路由 · `.2` Mac · **`.3` Intel 网卡** · `.4` 随机 MAC |
| `192.168.1.5`（脚本默认） | **不在线** |
| `192.168.1.3` 开放端口 | **22 / 445 / 3389 / 5985 / 135 全关** |

`.3` 的 MAC OUI 是 **Intel Corporate**，几乎可以断定就是那台 Windows。
端口全关与「刚重装 + 防火墙默认拦入站 + OpenSSH Server 未安装」完全吻合。

> ⚠️ **别用 ping 判断找没找对机器。** 排查时我 ping `192.168.1.2` 通了、22 拒绝，
> 差点当成 3060 有问题——那是 Mac 在 ping 自己。
> 见 `docs/learnings/`「3060 局域网 IP 会从 .5 漂走」，那条笔记专门警告过这个坑。

## 好消息：权重一个都没丢

3060 上曾有的 20 个 run（`v7_chain` / `v8_chain` / `v8_coco` / `v8_star3` /
`v9` 四版 / `v10_chain` / `v10_coco` / `v14_pad200` / `v15_tipval` /
`v16_tipuni_cold` / `short_star v6–v10` / `side_short_tip v2·v3`）
**在本机全部有权重**，2026-08-05 那次取回是完整的。
逐项核对见 `analysis/p_model_inventory_20260820.md`。

**重装没有造成模型损失。** 需要重建的只是训练环境。

---

## 最省事的做法：跑一个脚本

`scripts/windows/setup_3060.ps1` 把下面所有步骤一次做完，**幂等**（跑坏了再跑一遍就行），
每步自检，失败会明说哪一步、为什么。

把它拷到 Windows（U 盘 / 微信 / OneDrive 都行），然后**以管理员身份**开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_3060.ps1
```

跑完它会打印这台机器的 hostname / 用户名 / IPv4 / GPU 型号，把那几行发回来即可。

下面是它逐步在做什么——手动执行或排查时看。

---

## 第一步：Windows 上装 OpenSSH Server

以**管理员** PowerShell 运行：

```powershell
# 1. 安装（Win10 1809+ / Win11 自带这个可选功能）
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 2. 启动并设为开机自启
Start-Service sshd
Set-Service  -Name sshd -StartupType Automatic

# 3. 防火墙放行 22
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22

# 4. 默认 shell 设成 PowerShell —— 仓库所有脚本按 PowerShell 写的，cmd 会报 UNC 错
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
  -PropertyType String -Force
```

## 第二步：装公钥（**管理员账户有个坑**）

Mac 的公钥：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAjtCeQsUhmpprxG8xmQ+dzCMsD95G9r+7yllPn39owl antigravity
```

如果 `zzc` 是**管理员账户**（多半是），Windows OpenSSH **不读** `~/.ssh/authorized_keys`，
只读 `C:\ProgramData\ssh\administrators_authorized_keys`，而且**ACL 不对就静默拒绝**：

```powershell
$k = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAjtCeQsUhmpprxG8xmQ+dzCMsD95G9r+7yllPn39owl antigravity"
$f = "C:\ProgramData\ssh\administrators_authorized_keys"
Add-Content -Path $f -Value $k -Encoding utf8

# ACL：只留 Administrators 和 SYSTEM，否则 sshd 拒绝该文件且不报原因
icacls $f /inheritance:r
icacls $f /grant "Administrators:F" /grant "SYSTEM:F"
Restart-Service sshd
```

这一步失败的表现是**一直要密码**，不会告诉你是 ACL 的问题。

## 第三步：从 Mac 验证

```bash
ssh -o ConnectTimeout=5 zzc@192.168.1.3 'hostname; nvidia-smi --query-gpu=name --format=csv,noheader'
```

看到 `NVIDIA GeForce RTX 3060` 才算找对机器（MAC 地址只能说明是 Intel 网卡）。

然后把 IP 固定下来，省得再漂：

```bash
echo 'export FABLE_3060_HOST=zzc@192.168.1.3' >> ~/.zshrc
```

**更好的做法是在路由器上按 MAC 做 DHCP 预留**（`e0:d4:e8:c7:fb:41` → 固定 IP），
这样脚本默认值和现实不会再分叉。

## 第四步：重建训练环境

远端布局不是完整仓库，是个精简 GPU 箱：

```
C:\fable\
├── .venv\Scripts\python.exe
├── train_dense.py          ← 训练入口（不是 src.detection.train）
└── datasets\               ← Mac 用 scp 推过来
```

```powershell
mkdir C:\fable; cd C:\fable
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\python.exe -m pip install ultralytics==8.4.89 numpy==2.0.2
```

### ⚠️ 版本必须逐位对上

`scripts/train_on_3060.sh` 会比对两端版本，不一致直接拒绝开训，理由是
**「结果无法与历史曲线对照」**。Mac 侧当前：

| 包 | 版本 |
|---|---|
| python | 3.9.6 |
| torch | **2.8.0** |
| ultralytics | **8.4.89** |
| numpy | **2.0.2** |

装完自检：

```powershell
.\.venv\Scripts\python.exe -c "import torch,ultralytics,numpy; print(torch.__version__, ultralytics.__version__, numpy.__version__, torch.cuda.is_available())"
```

`torch.cuda.is_available()` 必须是 `True`，否则装成了 CPU 版，训练会慢到没意义。

`train_dense.py` 需要从旧机备份或重新生成——**本机没有它的副本**
（它只在 3060 上，从来没进过仓库）。这是这次重装**唯一可能真丢的东西**，
优先确认还有没有备份。

## 第五步：确认整条链通

```bash
scripts/train_on_3060.sh --check-only
```

它会验 SSH、`C:/fable` 存在、版本一致。全绿之后才谈开训。

---

## 这次暴露出来的两个问题

1. **`train_dense.py` 只存在于 3060，从未入库。** 一台机器重装就可能永久丢失训练入口。
   恢复后应当把它提进仓库（它是代码，不是产物）。
2. **`FABLE_3060_HOST` 默认值 `.5` 已经和现实分叉过两次。**
   DHCP 预留一次配好，比每次排查省事。
