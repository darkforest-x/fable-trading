# 前向 / 主线诚实状态摘要（2026-07-20）

**目的**：Claude 额度见底时，给 owner 一页「要不要动主线」的决策材料。  
**纪律**：本文件只读现有 artifact；**不**评估 holdout；**不**改阈值/障碍/成本假设。

---

## 一句话

**不要动主线配置。**  
真正的问题不是「edge 看起来不够好」，而是：**主线前向裁决账本已断、数据停更、早期样本被股票类 SWAP 污染**——先修管道，再谈 PF。

---

## 主线配置（仍有效，无需改）

| 项 | 值 |
|---|---|
| ACTIVE | `models/frozen_tp5_sl2_swap_20260709.txt` |
| 宇宙 | OKX USDT-SWAP · expanded 候选 |
| 均线 | EMA 8-55（P0-3 已否决 20/60/120） |
| 出场 | TP5 / SL2 · horizon 72 |
| 阈值 | val q90 = **0.3747093215963419** |
| dataset_sha256 | `818304cffc…4856180` |
| YOLO | 非关键 |
| holdout | 已消耗；禁止再碰 |

---

## 前向账本盘点（本机 `data/`，不入 git）

| 文件 | 行数(含表头) | 模型 | 窗口 | maker closed 粗经济 |
|---|---:|---|---|---|
| **`forward_log.csv`（主线裁决）** | **1（仅表头）** | — | — | **n=0；0/100 进度归零** |
| `forward_log_rules_pre_yolo_20260715.csv` | 10 | frozen_tp5_sl2_swap_20260709 · q90 | 07-08→07-09 | n_maker_closed=7；gross≈+0.27%/笔；胜率≈43% |
| `forward_log_h1_scaled.csv` | 9 | 同上阈值 · H1 出场 | 07-08→07-09 | n=7 closed；gross≈+0.24%；胜率≈71%；**已停更** |
| `forward_log_ma206*.csv` | 73~87 | **ma206 实验模型** · 更低阈值 | 07-10→07-11 | 非主线；不可并入 0/100 |
| `forward_log_pre_v11_retest_20260719.csv` | 14 | **yolo_v8_reg** · 阈值≈0.022 | 07-15→07-16 | 非主线；阈值量级已换，禁止混判 |

### 主线早期 7 笔的致命污染

`forward_log_rules_pre_yolo_20260715.csv` 的 closed 标的：

`NFLX, QQQ, ORCL, NEO, ZIL, EWJ, AT`（另有 open）

- `is_stockish`：**NFLX / QQQ / ORCL / EWJ = True**（7 笔里约一半是股票/ETF 类）
- `BLOCKED_BASES` 只挡住了 **EWJ**，没挡住 NFLX/QQQ/ORCL
- 因此：**即使用这 7 笔算 PF，也不能当作 crypto 主线裁决样本**

H1 shadow 同期窗口同样混入 NFLX/QQQ/ORCL；胜率好看，**样本身份不干净**。

---

## 基础设施健康

| 检查项 | 状态 | 含义 |
|---|---|---|
| Claude 日链 `daily-okx-data-update` | **已停用**（SKILL 写明改由 Codex automation `fable`） | 本机 Claude 不再拉数/前向 |
| K 线新鲜度（BTC SWAP 15m 末 bar） | **2026-07-16 16:45 UTC** | 相对 07-20 落后约 **3–4 天** |
| SWAP 15m 文件数 | 401 | 宇宙规模 OK，缺的是增量更新 |
| funding 文件 | 54 | 覆盖偏旧/偏窄（历史已记录 val 覆盖 ~73–76%） |
| 主线 `forward_log.csv` | 空表 | 任何「正式窗口 PF / 0/100」展示都是假进度 |

git 历史上可见 v11 / YOLO 源 / live gate 等前向改造；**当前 worktree 主裁决文件被清空或未回填**，与 07-10 纪要中的「已有 closed 样本」不一致。

---

## 挑战者排序（发现级 only，不升级主线）

| 排名 | 假设 | val 证据（摘要） | 前向状态 | 建议 |
|---|---|---|---|---|
| 1 | **H1 scaled** | top 净@maker +0.326%；PF≈2.8 | shadow 日志停在 07-09，且污染 | **唯一值得持续 shadow 的出场**；须 crypto-only 重记 |
| 2 | **H3 MA-exit** | top 净@maker +0.512% vs TP5 +0.15%；持仓更短 | 尚无独立 shadow 前向 | 发现级强；先做 resolver + 影子账本，**不替换 TP5/SL2** |
| 3 | H5 vol-adaptive | 净仅 +0.02% 量级 | 无 | 归档备查，不占前向带宽 |
| 4 | H8 30m | 净高但 n 小；h60 非最优 | 无 | 保持低频线索，不切 15m 主时钟 |
| — | H4 / H13 / H14–15 / H11 | 负或持平 | — | **dead / 不优先** |
| — | H9 / H10 | 有线索但未过「超单特征」或样本小 | — | 过滤/空头保持研究，不切主线 |
| — | **H16 放量突破入场** | 议程 🟡 未做 | — | 量价族首做；纯 val 单变量 |

---

## 要不要动主线？

| 问题 | 答案 |
|---|---|
| 改 TP/SL / q90 / EMA 定义？ | **否** |
| 换 ACTIVE 到 ma206 / yolo_v8 / scaled？ | **否**（皆非确认级） |
| 降低阈值加速 N？ | **否**（见 `docs/FORWARD_ACCELERATION_OPTIONS.md`，默认 stay） |
| 重开 holdout？ | **否** |
| 必须立刻做的？ | **(1) 恢复 update_okx 日链 (2) 用 ACTIVE 回填主线前向到独立/主文件 (3) 前向扫描强制 crypto-only（stockish 剔除）** |

**判定标准未变**：主线确认仍要求 **正式窗 + 冻结 TP5/SL2 + maker-filled closed 积累到门禁（~100）** 后再谈 PF。  
在账本为空、数据停更的情况下，**任何「主线失效/有效」的口头结论都过早**。

---

## Owner 可执行清单（零 Claude）

1. **确认谁在跑日链**：Codex automation `fable` 是否仍活；若死，本机手工：
   ```bash
   python3 -m src.data.update_okx
   PYTHONPATH=. python3 scripts/forward_track.py
   PYTHONPATH=. python3 scripts/daily_digest.py --dry-run
   ```
2. **主线日志策略**（二选一，需你点头）：
   - A：允许 `forward_track` 从 `FORWARD_START` 幂等回填 `data/forward_log.csv`（空文件场景）
   - B：先写 `data/forward_log_mainline_replay.csv`，你验收后再 `cp` 晋升
3. **强制**：前向候选路径过滤 `is_stockish` / 扩大 BLOCKED，使 0/100 只计 crypto。
4. **Shadow 带宽**：只开 **H1**（已有代码路径）+ 可选 **H3**（见 grok task11+）；关掉 ma206/q80/yolo 实验日志对裁决 UI 的干扰。

详细可过夜实现规格：`grok_tasks/overnight_batch_v2.md`。
