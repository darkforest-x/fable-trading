# 事件语义对照 — LEAN / vnpy vs 本仓 forward / executor

**日期**：2026-07-22  
**upstream（只读规格，不安装全家桶）**：
- [QuantConnect/Lean](https://github.com/QuantConnect/lean)
- [vnpy/vnpy](https://github.com/vnpy/vnpy)
- 同族已评：Basana / `docs/EXEC_PROTECTIONS_SPEC.md`

**本仓锚点**：`src/judgment/forward_scan.py` · `src/judgment/forward_records.py` · `src/execution/executor.py`

---

## 一句话

成熟框架强调：**回测事件边界必须能映射到实盘同一语义**。本仓已经为 tip 做了「盘口 bar 当场入账 + 下脉冲回填真实入场」；本表只做审计对照，**不**换执行器。

---

## 事件边界对照表

| 概念 | LEAN / 事件驱动回测（语义） | vnpy（语义） | 本仓现状 | 差距 / 风险 |
|------|-----------------------------|--------------|----------|-------------|
| **信号时刻** | OnData / 策略在 bar 收盘或 tick 触发 | 策略 on_bar / on_tick | `signal_time` = 信号 K 线时间 | 一致：以收盘 bar 为信号 |
| **可交易时刻** | 通常下一 bar 开盘才成交（防前视） | 同左（回测模式） | 离线建库：要等入场 bar；**实时 tip**：当场写 open，`entry_price` 先用信号 bar 收盘代理 | 实时路径有意用代理价，靠 merge 回填 |
| **入场价回填** | 回测撮合引擎一次定死 | 回测引擎成交价 | `maker_filled` 空 = 待回填哨兵；下一脉冲填真实下根开盘 | 勿把代理价当最终成交审计 |
| **检出延迟** | 回测常假设即时可知信号 | 实盘有网关延迟 | `detected_at` 保留首见；新鲜度三门 30min | 延迟预算见 learnings freshness-gates |
| **新鲜度门** | 框架少「信号过期丢弃」默认 | 用户自写 | executor / TG / 看板 **同值 30min** | 改门必须三处同改 |
| **贴边过滤** | 无对应 | 无对应 | A′：`TIP_EDGE_BARS`，只收扫描窗最后 N=2 | 本仓特有；框架对照帮不上 |
| **持仓/出场** | OnOrderEvent / 止损止盈算法 | 止损单 / 算法交易 | TP5/SL2 ATR；`timeout_hours` | 口径已固定；勿默默改障碍参数 |
| **回测↔实盘同账** | Lean 强调同一算法代码路径 | 常「回测一套、实盘一套」 | forward_log = 发现账本；executor ledger = 真金 | **两本账**；确认级认前向新鲜 100，不认 val PF |
| **风控熔断** | 风险模型 / 算法限制 | 风控引擎 | kill + 连亏 + max_concurrent；Protections 规格未上线数字 | 见 EXEC_PROTECTIONS_SPEC |

---

## 对本仓审计的含义（发现级）

1. **代理入场 vs 真实入场**：复盘 tip 单时先看 `maker_filled`；空则延迟统计用 `detected_at`，盈亏等回填后再算「真成交」。
2. **离线数据集 ≠ 实时路径**：`build_dataset` 仍要入场 bar；不要用实时代理价逻辑去「补」历史标签。
3. **不装 LEAN/vnpy**：对照表已够；装全家桶会抢依赖且无 tip 收益。
4. **待 tip_fire>0 后再拧**：Protections 数字、更严日损——过早熔断会挡稀信号。

---

## 复现（只读，无安装）

```bash
# 读本对照 + 本仓实时入账注释
sed -n '180,230p' src/judgment/forward_scan.py
sed -n '45,60p' src/judgment/forward_records.py
```
