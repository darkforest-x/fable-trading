# P2-11 偏 B · 坏图清单（Round 1 → E2）

**日期**：2026-07-10  
**用途**：单变量 segment 修正的验收集；不是全量统计。

## 坏图（优先修）

| image | split | 现象 | 根因类 | 目标实验 |
|---|---|---|---|---|
| `PAXG_USDT_015960` | val | 左框超长（dense run **74** bars） | 长段 slab | **E2 core-trim** |
| `PI_USDT_017060` | val | 左框过宽（run **59** bars） | 长段 slab | E2 |
| `ALLO_USDT_014860` | val | 两框本可并（9+12 bars） | split/merge | 以后 E4 merge（本轮不动） |
| `ICP_USDT_000760` | train | 左缘残框 | edge | 以后 E3 |
| `BNB_USDT_011660` | train | 左缘残框 | edge | 以后 E3 |

## 好图（回归，不许变坏）

`LTC_USDT_016460`, `XRP_USDT_016760`, `BNB_USDT_005560`, `SUI_USDT_012660`（右框）, 背景 `SPACE/NEAR/CHZ/WLFI/BTC_005760`

## 本轮单变量（E2）

- **只改**：`MAX_DENSE_BARS = 24`（新增；超长 run 收成最紧 24 根）
- **不改**：x_pad / y_pad / min_bars / merge_gap / 增强 / conf
- **为何不是 merge**：merge 会让框更宽；主诉是超长框，应对 **截核** 不是合并

对照：`/label_audit_e2_compare.html`（生成后）
