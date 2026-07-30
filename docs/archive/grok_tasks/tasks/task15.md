# 任务15：H1 scaled shadow 健康修复（crypto-only 续记）

## 铁律摘要
- H1 仍是 **shadow only**；不替换 TP5/SL2 主线  
- 禁 holdout；禁改主 `forward_log.csv`  
- 日志：`data/forward_log_h1_scaled.csv`（append 幂等）  
- 单变量：出场=scaled；分数模型=主线 ACTIVE + 主线 q90  

## 背景
- `forward_log_h1_scaled.csv` 停在 2026-07-09，仅 ~8 行，且含 stockish  
- 计划文档：`docs/H1_SCALED_FORWARD_SHADOW_PLAN.md`  
- 代码可能已有 `scripts/forward_track_h1_shadow.py`——先读再改，忌重写  

## 做什么

1. 核实 H1 shadow 入口是否：
   - 仍指向正确 ACTIVE / 阈值  
   - 在 task12 闸门之后享受 crypto-only  
   - 幂等键 `(source, symbol, signal_time)` 稳定  
2. 修复「停更」原因（常见：未进日链、异常吞掉、路径写错、FORWARD_START 之后无增量）  
   - 文档化：如何手动跑、如何挂到 Codex 日链（**只写说明，不擅自改 Claude/Codex 云端 cron 密钥**）  
3. 从 `FORWARD_START` 幂等续跑 shadow（数据允许时），**不要**删除旧污染行；在报告中：
   - 全样本 vs crypto-only 子集两套统计  
   - 声明旧行污染，新行应 stockish=0  
4. 报告 `analysis/p15_h1_shadow_health.md`：
   - 复现命令、n、outcome、毛净、与主线旁路 **同信号** 对照（出场不同）  
   - 明确不计入 0/100  

## 判定
- 工程：shadow 可续跑 + 报告诚实  
- 经济：样本 <30 不做「确认级通过」表述  

## 不做
- 不把 H1 晋升 ACTIVE  
- 不把 H1 PF 写进主 digest 裁决字段  

## 完成定义
修复 + 报告 + 测试（若改 resolver）+ commit push + RESULTS_v2  
