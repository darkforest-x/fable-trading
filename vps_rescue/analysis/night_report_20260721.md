# 晨报 / 批次状态（2026-07-21）

**触发**：owner 要求补齐 Cursor 夜间未完成五项。  
**纪律**：本批 holdout#6 仅在 v12 池 cutover accept 时消耗一次；未 promote ACTIVE。

## 总表

| # | 任务 | 状态 | 产物 / 备注 |
|---|---|---|---|
| 1 | tip 子集全量回测 | 🔄 跑中 | `logs/tip_subset_rerender.log`；完成后 → backtest + `p_tip_subset_val.md` |
| 2 | 滑点报告 | ✅ | `analysis/p_execution_slippage.md` + JSON |
| 3 | 晨报 | ✅（本文，随长任务更新） | 本文件 |
| 4 | v12 全池 + holdout#6 | 🔄 跑中 | `logs/yolo_v12_pool_build.log` → cutover 脚本 |
| 5 | tiered VPS 部署 | ✅ | headroom ①；executor restart；status-strip 新鲜度对齐 |

## 关键发现（并行诊断）

- VPS 前向：**0 新鲜 / 22 事后**；lag 中位 ~9h  
- tip 烟雾（30 行）与全量进度早期：live 全序列 MA 下 tip_hit_strict 很低（~5%）——与离线 0.925 冲突，**渲染/训练分布问题仍开放**  
- 执行折扣：无 fill 价差样本；51008 ×5 历史；延迟门是主损失

## tiered 部署口径（已生效）

- `unit_notional = equity×leverage / max_concurrent / 2`  
- 实际名义 = unit × size_mult（1 / 1.5 / 2）  
- 详见 `docs/learnings/tier-multiplier-needs-margin-headroom-in-base-notional.md`

## 待长任务结束后补写

- [ ] tip 折扣系数（tip 净 / 全量净）  
- [ ] holdout 第 6 次记账句 + accept 表  
- [ ] ACTIVE 仍为 v11 判断（等待 owner promote v12 池冻结）

## 风险

- v12 全池扫描可能数小时；Mac 16GB 注意 OOM  
- tip 子集若 tip_hit≈0，折扣系数无定义（分母有、分子空）→ 报告如实写 fail  
- 未改主线写账闸门（事后仍入 forward_log）——另项止血
