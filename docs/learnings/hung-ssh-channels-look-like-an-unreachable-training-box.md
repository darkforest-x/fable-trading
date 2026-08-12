# SSH 通道不关闭，症状长得像"训练机连不上"——而且会越探越坏

- **问题**：2026-08-12 要在 3060 上开 R3 训练，`ssh zzc@192.168.1.4 "..."` 全部挂死：
  不报错、不超时、进程一直活着。我据此在报告里写"3060 SSH 无响应，训练臂被阻塞"。
  Owner 一句"为什么今天 codex 都能连上"直接证伪了这个结论。

- **死胡同（每一条都真花了时间）**：
  1. **以为猜错了 IP**。仓库脚本有 `.3`/`.4`/`.5` 三种默认值，还写着
     "the 3060 DHCP address must never be guessed"。但 `arp -a` 显示 `.4` 就是 `zzc-2.local`，
     地址是对的。
  2. **以为机器睡了/sshd 死了**。但 `ping` 通、`nc -z 22` 通、密钥认证通过，
     `nvidia-smi` 还回过完整结果（RTX 3060 / 12288 MiB / 0% 占用），`scp` 传文件 EXIT=0。
     **机器一直是好的。**
  3. **以为是 `-o ConnectTimeout` 没设对**。它只管 TCP + 握手，命令跑完通道不关它管不着。
  4. **以为是远端默认 shell 变成了 cmd.exe**。`ssh -tt` 的输出里有 `conhost.exe` 标题，
     但那对 PowerShell 也会出现——这条证据不成立，别当结论用。
  5. **`-EncodedCommand`（UTF-16LE base64）**：本地 round-trip 验证没问题，远端却只回一个
     CLIXML 错误头、stdout 全空。
  6. **`-tt` 强制 pty**：会话确实会关闭，但 Windows 控制台清屏把命令输出吃掉了，
     调用方拿不到可解析的东西。
  7. **把 ssh 放进后台跑**（`... &` 或 `( ... ) &`）：同一条命令在前台能出结果，
     放后台就一个字都不出。

- **有效路径（在会话干净时）**：把脚本从 **stdin** 喂给 `powershell -Command -`：

  ```bash
  printf '%s\n' "$cmd" | ssh -o BatchMode=yes host "powershell -NoProfile -NonInteractive -Command -"
  ```

  这是唯一一次拿到干净输出的形式（`ps-ok cwd=C:\Users\zzc`，无 CLIXML 噪声，无引号地狱）。
  但**它只在会话干净时有效**。

- **决定性的一刀：分别测 sftp 和 exec**。
  `sftp` 2 秒连上、`ls` 正常、干净退出；`scp` 每次都成功。而 `ssh host "cmd"` 连上后
  什么都不跑、也不关闭。**sftp-server 是独立子系统，不走 `DefaultShell`；exec 走。**
  两者一好一坏 ⇒ 病在 **sshd 的 shell 配置**，不在网络、不在认证、不在机器负载。
  （顺手用 sftp 查了候选 shell：`powershell.exe` 5.1 和 `cmd.exe` 都在，
  **`C:\Program Files\PowerShell\7\pwsh.exe` 不存在** —— 若 `DefaultShell` 指向它，
  症状会与观察到的完全一致。）
  修复只能在机器上做（读/改 `HKLM:\SOFTWARE\OpenSSH\DefaultShell` 后重启 sshd）。

- **另一个真实病根**：这台机器上的 ssh **exec 通道跑完命令也不关闭**。每探测一次就留下一个
  卡住的会话；会话越堆越多，新连接越来越慢，最后完全不回。所以：
  - codex 早些时候能连上（那时干净），我后来连不上（我自己把它探坏了）；
  - `$(ssh ...)` 命令替换会一直等 EOF ⇒ 整套自动化**静默变成永久挂起，而不是失败**。

  客户端侧的缓解是给每次远程调用加**硬 deadline**（看门狗杀本地 client），
  但服务端堆积的会话只能靠 **重启 Windows 的 OpenSSH 服务** 清掉——那是改系统服务，
  属于要先问 Owner 的操作，不能自作主张。

- **通用规则**：
  1. 远程命令"挂住"而不是报错时，先分三层定位：**网络**（ping/nc）、**认证**（`ssh -vvv`
     跑到 authenticated）、**通道**（有没有 `Exit status`）。第三层挂死几乎都不是机器坏了。
  2. **任何远程探测都要带硬超时**，只加 `ConnectTimeout` 等于没加。
  3. **诊断本身会改变被诊断的系统**：每次重试都在加剧故障。发现"越试越糟"就停手，
     先清状态或交给能清状态的人。
  4. 把"连不上"写进报告前先跑一次 `-vvv`，并把**已经成功过的证据**（nvidia-smi/scp）
     和失败一起写出来——只写失败会让 Owner 拿到一个错误的世界模型。

- **牵连**：`scripts/ssh_ps.sh`（新，stdin + deadline）、
  `scripts/train_w20_midbox_on_3060.sh`（改用 wrapper，探测改为 WMI 分离进程 + 读文件）；
  其余 3060 脚本（`short_tip_v2_train_start.sh`、`sync_v16_to_windows.sh`、
  `train_eth3m_short_pilot*_on_3060.sh`）仍是裸 ssh + PowerShell，**同样会以挂起收场**。
