# Owner 打标作战手册（round3+）

## 训练是不是接着之前的权重？

**是（v2 起）。**

| 版本 | 底座 | 数据 |
|------|------|------|
| v1 | E2.1 `dense_15m_full_s_e21`（规则/旧标签先验） | ~268 图 / ~90 训练框 |
| **v2** | **`dense_owner_v1/best.pt`（在你金标上微调过）** | golden_pool + chunk1（768 图 / 423 框） |
| 以后 | 默认从上一版 owner best 接着训 | 每批标完合并再训 |

不是从零开始；也不是只靠规则模型。  
「接着训」= 权重热启动 + **全部金标数据重扫**（不是只喂新 505）。

## 分批项目（6 个）

| 批次 | 项目 | 任务 |
|------|------|------|
| chunk1 | round3_chunk1 (id=7) | 505 ✅ 已导出 |
| chunk2 | round3_chunk2 (id=8) | 505 |
| … | … | … |
| chunk6 | round3_chunk6 (id=12) | 448 |

## 教科书级样本怎么单独拿出来

标注界面增加了 **整图质量** 选项（chunk2+ 配置已可更新）：

| 快捷键 | 含义 |
|--------|------|
| **2** | `textbook` — 特别特别标准的图（整图） |
| **3** | `normal` — 普通（可省略） |
| **1** | 画 `dense_cluster` 框 |

导出后脚本会把 `quality=textbook` 的 stem 写入 `data/owner_exemplars.json`，  
可单独做：硬负样本对照、校准可视化、蒸馏教师子集。

**chunk1 已标完的**：若当时没勾，可把 stem 手写进 `data/owner_exemplars.json` 的 `stems` 列表。

## 合约数据（SWAP）约定

- 当前 round3 ~3000 张：现货/历史 dense_15m_full 池，**明天先打完**。
- **下一包起全部 SWAP**：从 `data/kline_deep` / `kline_fetched` 的 `*_USDT_SWAP_15m` 渲染，  
  渲染风格对齐 `tradingview_charting.py`（TV 蜡烛 + EMA 簇）。
- 生成脚本路径（后续）：`scripts/prepare_swap_owner_pack.py`（待离线生成）。

## 上班远程打标（VPS 反代本机 8081）

本机 Label Studio 必须开着，再开反向隧道：

```bash
# Mac 上
export VPS_HOST=root@你的VPS_IP
export VPS_PORT=18081
bash scripts/ls_reverse_tunnel.sh
```

浏览器访问：`http://VPS_IP:18081`  
账号：`fable-review@example.com` / `fable-review-local`

VPS 需允许 `GatewayPorts`（见脚本注释）。  
**安全**：弱口令，建议防火墙只放行公司出口 IP。

## TG 通知

需要 `data/tg_config.json`（gitignore）：

```json
{"bot_token": "从 BotFather", "chat_id": "频道或群 ID"}
```

或环境变量 `TG_BOT_TOKEN` + `TG_CHAT_ID`。  
训练结束会 `python -m src.notify "owner v2 F1=..."`。

## 明天任务清单

1. 标完 chunk2–6（~2468）
2. 喊我：导出 + 合并 + 训 v2.x
3. 我离线生成 **SWAP** 新任务包
4. 教科书级用 **2** 标记
