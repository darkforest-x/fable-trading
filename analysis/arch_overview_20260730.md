# fable-trading 架构与现状总览（2026-07-30）

> 依据：HANDOFF.md、STATE_20260730.md、data/forward_log.csv、data/judgment_v10_wide.csv、analysis/output/live_signals_v10/last_scan.json、p2b_* 报告

## 一、两层架构

```
K线(15m) → 检测层(2a: YOLO v10) → 候选池 → 判断层(2b: LightGBM) → 信号(纸面/实盘)
```

- **2a 检测层**：YOLO11 检测"均线密集启动"形态。输入 200×200 渲染图，输出 tip/tip-1/tip-2 窗的框。生产阈值 conf=0.30，--tip-only 只认信号 bar。
- **2b 判断层**：LightGBM 排序/回归，在候选上挑"值得做"的 top decile。特征 28 生产 + 19 扩展（无前视，仅信号 bar 及之前）。

## 二、状态：认知被颠倒

| 层 | 结论 | 关键数字（v10 候选池 18,379 笔 / 232 币） |
|---|---|---|
| **2a 检测** | ❌ 负价值 | 池均值 **-2.41bp**（maker净，扣成本）；因果贡献 **~-6bp**（匹配随机做空对照 -0.39bp）；开火密度 ~115 条/币·月（owner 标注密度 0.18~0.36）；九种出场全部无法覆盖 10bp 往返成本 |
| **2b 判断** | ✅ 唯一有效 | 顶十分位稳定提升 **+17.76bp**（v10）/**+17.82bp**（老池 tip_v1b 25,602 笔）；**14~15/15 折为正**；ATR 匹配对照 -19.24bp，顶档超对照 **+42.73bp**；两池 Jaccard 重合仅 8.6% |

**单变量纪律下的结论**：检测器开火本身是负的；判断层挑单在两个不重合的池上都站住了。

## 三、是否"串起来"（端到端）

- **纸面模拟实盘（paper/sim）**：✅ 已串
  - `scripts/live_signal_tg.py --tip-only --send`（v10 权重、USE_STOP=True、30min 新鲜度）
  - 信号写 `analysis/output/live_signals_v10/last_scan.json`
  - 推送：TG + Bark（`src/notify.py`）
  - 前端：`src/webapp/{live_paper.py,server.py}` 暴露 `/api/live-paper`；`index.html` + `app.js` + `status_strip.py` 强制 4 芯片（owner/judgment/forward/v10纸面）
  - 纪律：不写 forward_log、不改 ACTIVE、不真下单、不碰 holdout
- **真实执行/前向**：❌ 未串
  - `data/forward_log.csv` 仅 34 行，27 笔 maker-filled closed（2026-07-18~07-21）
  - 中位检测延迟 ~490min（远超 30min 新鲜度门）；这些是陈旧统计，当前脉冲 15min + 扫描 ~2.8min
  - 现行纸面不落 forward_log，真实下单仍需 owner 逐次授权

## 四、关键数字速查

**v10 候选池（data/judgment_v10_wide.csv）**
- 行数 18,379 / 币种 232
- 池均值（net_barrier_maker）：-2.41bp
- 顶十分位均值（示例口径）：~+53bp（不同出场/成本口径有差异，报告口径为 +11~17bp 净区间）

**判断层 walkforward（p2b_yolo_short_30_6m_reg）**
- 5 折；顶十分位净（小数）：[+0.0065, -0.0051, +0.0112, -0.0011, +0.0053]
- 跨池稳健结论来自 STATE/HANDOFF 的 +17.7bp 量级

**前向日志**
- 34 行总计，27 笔 maker closed，29 币，时间 07-18~07-21
- p50 延迟 ~490min（已过时）

**纸面最近扫描（last_scan.json v10）**
- 2026-07-29T05:32Z，n_fired=1，n_fresh=0（30min 门），tip_edge=2

**成本与新鲜度**
- 往返 10bp（0.2%）；新鲜度三门同值 30min（脚本/TG/看板/执行器一致）

## 五、已证伪/不采纳（不要再做）

- 九种出场规则在 v10 上全部无效（排名与老池反转）
- 分类改回归在 v10 上只值 -0.53bp
- +245bp 顶档提升孤证，未复现
- Kronos 特征配对 +2.42bp，置换 p=0.0333 未过 0.01，且"仅 Kronos"0/15 折为正

## 六、下一步（优先级，需 owner 决策的已标注）

1. 剖开顶十分位：为什么它赚钱？（特征差异 + 匹配对照）—— 可能比手画金标更值得当目标
2. 标 `datasets/label_live_tip_1000/` 1000 张右缘=盘口、无后文的图（owner 20 分钟可回答"盘口可认否"）
3. 滑点实测（ledger 补 avg_fill_px）

**禁止**：promote、改 ACTIVE、清 forward_log、真下单、改新鲜度三门、未经批准读 holdout。

---
生成：analysis/arch_overview_20260730.md
