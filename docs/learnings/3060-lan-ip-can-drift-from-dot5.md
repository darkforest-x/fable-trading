# 3060 局域网 IP 会从 .5 漂走；ping 不通先扫同网段

- **问题**：默认 `zzc@192.168.1.5` SSH/ping 全死（ARP incomplete），但 Mac 仍在 `192.168.1.0/24`。
- **死胡同**：只报「开局域网 / OpenSSH」而不查是否换 IP——盒子其实已开机且 SSH 正常，只是 DHCP 给了别的地址。
- **有效路径**：同网段 ping sweep → 对存活主机试 `zzc@` + `Test-Path C:/fable` + GPU 名；本次落到 **`192.168.1.3`**（RTX 3060）。再 `FABLE_3060_HOST` 或改脚本默认。
- **通用规则**：`sync_* --check` 失败时：① ping `.5` ② sweep ③ 对 UP 主机探 SSH+fable+3060，再谈「机器没开」。
- **2026-07-27 更新（本条曾把人带偏，务必读）**：IP **又漂回 `.5`**，而 **Mac 自己占了 `.3`**。
  于是 `ping 192.168.1.3` 会「通」——**那是 Mac 在 ping 自己**——但 SSH 必然 refused。
  这正是 `analysis/output/it16_pipeline.log` 里连续 `SSH_FAIL` 的原因。
  **ping 通 ≠ 找对机器**：先 `ipconfig getifaddr en0` 确认本机 IP，再扫 22 端口
  （`nc -z 192.168.1.$i 22`），最后用 `nvidia-smi` 确认对面真是 3060。
  `scripts/train_on_3060.sh` 的默认值 `zzc@192.168.1.5` 一直是对的；被写歪的是这份笔记。
- **牵连**：`FABLE_3060_HOST`、`scripts/sync_v14_to_windows.sh`、路由器 DHCP 预留（建议绑 MAC→固定 IP）
