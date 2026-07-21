# 执行折扣 / 滑点实测（2026-07-21）

**目的**：把「实盘理论收益 = 回测 × tip 群体折扣 × 执行折扣」里的执行折扣从拍脑袋变成可核对数字。  
**纪律**：只读现有 artifact；不评 holdout；不改成本/阈值/障碍。

---

## 结论（先行）

**无法从当前台账可靠估计「成交价相对账本价」的 bp 滑点。**  
绑定执行折扣的不是 taker 滑点，而是：

1. **检出延迟（lag）**：主线前向日志 lag 中位 **542 min**，最小 77.6 min，**新鲜可交易行 = 0**  
2. **保证金拒单（51008）**：历史 ledger 有 **5 次** 不足保证金失败（与 tiered 满仓部署冲突同源）  
3. **真实成交样本极稀**：compact 前 ledger 仅 **1 笔** `order_partial`，无成对 fill/mark 可算价差

诚实声明：本报告**不编造**「平均滑点 X bp」。可执行含义见「下一步」。

---

## 数据源

| 源 | 路径 / API | 用途 |
|---|---|---|
| 前向账本 | VPS `GET /api/forward`（2026-07-21 拉取，n≈28） | detected−signal 延迟 |
| 执行台账 | `/opt/fable-trading/data/executor_ledger*.jsonl` | 下单成败 |
| 产物 JSON | `analysis/output/p_execution_slippage.json` | 机器可读摘要 |

---

## 1. 延迟折扣（可量化）

| 指标 | 值 |
|---|---:|
| 前向行数（表内） | 28 |
| 新鲜行（lag≤30min） | **0** |
| 裁决笔数 | **0 / 100** |
| 事后排除 | 22（与 API 一致） |
| lag 最小 / 中位 / 均值 / 最大 (min) | 77.6 / **542.3** / 735.7 / 2307.1 |

解读：

- 执行器与 TG 的 30min 门会把这些行**全部挡在开仓外** → 对「可交易集合」延迟折扣 = **100% 拒单**（不是几 bp）  
- lag 衡量的是 **检出滞后**，不是 OKX 滑点；但在当前管道里它是主导执行损失  
- 根因见 HANDOFF / 会话诊断：`yolo_mode=live` 右缘可映射窗内任意 bar + 旧 signal 补认

## 2. 成交价滑点（不可估）

- ledger 事件构成：`paused` 328 · `order_failed` 5 · `order_partial` **1**  
- 唯一 partial：`DOGE` mark=0.0736，无后续 fill 回写价差字段  
- **n_clean_fill_with_price_diff = 0** → 不报告虚假 bp

若要真正测滑点，需要至少：`entry_price`（账本）vs `avgPx`（成交）成对写入 ledger，且 n≥30。

## 3. 保证金 / 拒单

| 拒单码 | 次数 | 含义 |
|---|---:|---|
| 51008 Insufficient USDT margin | 5 | 名义过大 / 权益不足 |
| 其他 | 0 | — |

与 tiered 1.5x/2x 部署冲突一致：max_concurrent=1 × leverage=3 时 1x 已近满保证金。

---

## 风险与诚实声明

- 本报告**不**用事后 TP 收益反推滑点  
- 前向样本全是 hindsight，**不能**当 live edge  
- holdout 消耗：0  
- 与 tip 子集回测正交：群体折扣（能不能在 tip 检出）≠ 执行折扣（检出后成交质量）

---

## 下一步（部分已在并行）

1. **写账闸门**：仅 lag≤30min 入主账本（止血，另项）  
2. **tiered 部署**：unit = 预算/2 头寸预留（本批实现）  
3. 补 ledger：`avg_fill_px` / `slip_bp` 字段，攒 ≥30 笔再出 bp 报告  

---

## 复现

```bash
curl -sS http://103.214.174.58:8642/api/forward -o /tmp/fwd.json
# VPS: data/executor_ledger.jsonl + executor_ledger_pre_compact_*.jsonl
# 摘要：analysis/output/p_execution_slippage.json
```
