# 新 VPS Reality 节点（Debian 12）

> **安全**：本文件**不得**写入 root 密码、API key、xray UUID/私钥。
> 凭证只放本机密码管理器或 `~/.ssh/`，通过 SSH key 登录。

## 主机

| 项 | 值 |
|----|-----|
| IP | `206.237.14.112`（以当前 VPS 为准，变更时改 hub / settings） |
| 用户 | `root`（或专用 sudo 用户） |
| 登录 | **SSH 公钥**；禁止把明文密码提交进 git |

```bash
# 本机：生成并安装公钥（若尚未）
ssh-keygen -t ed25519 -f ~/.ssh/fable_vps -N ""
ssh-copy-id -i ~/.ssh/fable_vps.pub root@206.237.14.112

ssh -i ~/.ssh/fable_vps root@206.237.14.112
```

## 一键装 Xray Reality

仓库脚本（服务端 root 执行；**运行时生成** UUID / x25519，打印到终端，自行抄到客户端）：

```bash
# 在 VPS 上
bash scripts/setup-xray-reality.sh
```

本地从本机推脚本再跑：

```bash
scp -i ~/.ssh/fable_vps scripts/setup-xray-reality.sh root@206.237.14.112:/tmp/
ssh -i ~/.ssh/fable_vps root@206.237.14.112 'bash /tmp/setup-xray-reality.sh'
```

脚本会输出：`IP / Port / UUID / PublicKey / ShortId / SNI`。  
**把输出贴到私密笔记，不要回写本文件。**

## 与仓库其它入口对齐

换 IP 后需同步（无密钥）：

- `output/label_studio/hub.json` 的 Label Studio URL
- `.claude/settings.json` 的 rsync/ssh allow 前缀（仅开发机）

## 不要做的事

- 不要 `git add` 含密码的临时草稿
- 不要把 xray `privateKey` / client UUID 写进 docs 或 commit
- 旧文档若曾泄露过 root 密码：**立即在面板改密 + 关密码登录**
