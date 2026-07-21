# 执行风控规格对照 — Freqtrade Protections → 本仓 executor

**日期**：2026-07-22  
**假设**：`H-EXEC-WUZAO-1`（发现级规格；**不** pip 引入 freqtrade，**不** copy GPL 源码）  
**状态**：规格草稿。默认等前向新鲜样本够再谈上线阈值；过早熔断会挡本来就稀的 tip 单。

参考文档（外链，只读思路）：[Freqtrade Protections](https://www.freqtrade.io/en/stable/plugins/)  
本仓执行器：`src/execution/executor.py` · `src/execution/config.py` · ledger `data/executor_ledger.jsonl`

---

## 1. 本仓已有能力（对照基线）

| 本仓机制 | 位置 | 行为 | 相当于 Protections 哪类 |
|----------|------|------|-------------------------|
| **kill switch** | `touch data/executor_KILL` | 停新开仓；持仓超时仍可关 | 人工总闸 / 全局 pause |
| **连续亏损熔断** | `max_consecutive_losses`（默认 5） | ledger 连续亏损 ≥N → pause 新开 | StoplossGuard / 连亏冷却雏形 |
| **并发上限** | `max_concurrent`（现 1） | 槽满拒开 | 隐式仓位保护 |
| **信号新鲜度** | `max_signal_age_min=30` | 过老信号拒开（三门同值） | 与 Protections 不同维：时效门 |
| **超时强平** | `timeout_hours=18`（72×15m） | 强制平仓对齐验证 horizon | 时间止损，非回撤熔断 |
| **tiered sizing** | sidecar `sizing_tiers` | 按分位放大名义（已上线） | 仓位缩放，非熔断 |

**缺什么（相对 Protections 清单）**：日损%/回撤%、冷却时长（时间窗）、单币低利润屏蔽、按「今天已亏够」自动停。

---

## 2. Protections 规格 → 本仓映射（只抄门槛语义）

| 外源概念（名字） | 规格语义（自述，不引代码） | 本仓建议落点 | 上线前置 | 风险 |
|------------------|----------------------------|--------------|----------|------|
| **MaxDrawdown** | 账户权益自峰值回撤 ≥X% → 暂停新开 Y 小时 | 新字段 `max_drawdown_pct` + 冷却到 `paused_until`；数据源=ledger 累计已实现 + 持仓浮盈亏 | 前向≥50–100；X/Y **owner 拍** | tip 稀时易长期空仓 |
| **StoplossGuard** | 近 N 笔止损过多 → 冷却 | 已有连亏计数；可扩展「仅计 SL 出场」vs「任何亏」 | 现默认 5 可先观察 | 与 tip 波动叠加 |
| **CooldownPeriod** | 任意平仓后全局歇 T | `cooldown_after_exit_min` | tip_fire>0 后再议 | 可能错过同脉冲第二币 |
| **LowProfitPairs** | 某币近 M 笔净利差 → 临时拉黑 | `symbol_cooldown` map in ledger | 样本够再做 | 山寨噪音大 |

**禁止**：`pip install freqtrade`；粘贴 GPL 插件源码进本仓。自写门槛即可。

---

## 3. 与「真金纪律」对齐

- 改默认阈值 / 启用新熔断 = **owner 逐次批准**（同 kill / 改仓）。  
- 规格可提前写；**默认值上线**另批。  
- 不自动 promote 模型；不因熔断实验清 `forward_log`。

---

## 4. 建议落地顺序（确认级仍靠前向）

1. 账本报表：日实现盈亏、峰值回撤、连亏 streak（只读脚本，可随时做）。  
2. Owner 定：日损% / 回撤% / 冷却分钟。  
3. 单变量加一个熔断（优先 MaxDrawdown **或** 强化 StoplossGuard，二选一）。  
4. 影子日志一周 → 再决定是否默认开启。

---

## 5. 待 Owner 批（本夜不做）

- [ ] 是否在 tip 仍≈0 时就启用更严熔断（默认：**否**）  
- [ ] 日损 / 回撤具体数字  
- [ ] VPS 上是否要独立「风控状态」看板条（≠ Grafana 全家桶）
