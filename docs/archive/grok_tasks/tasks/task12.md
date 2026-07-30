# 任务12：前向 crypto-only 闸门（样本污染修复）

## 铁律摘要
- 单变量：**只修「谁能进前向日志」**，不改分数模型/阈值/出场  
- 禁 holdout；禁改 frozen 权重文件  
- 主 `forward_log.csv` 仍不 truncate；过滤逻辑作用于 **扫描写入路径**

## 背景
早期主线 7 笔 closed 中 NFLX/QQQ/ORCL/EWJ 为 stockish；`BLOCKED_BASES` 未覆盖全部 stockish。  
确认级 0/100 若混入股票类 SWAP，裁决无效。

## 做什么

1. 定位前向扫描入池点（`src/judgment/forward.py` / `forward_scan` / loader 候选）：
   - 统一：`base in BLOCKED_BASES` **或** `is_stockish(base)` → **不写入任何前向 log**  
   - 在 summary JSON 中增加计数：`skipped_stockish` / `skipped_blocked`  
2. 单元测试：构造假 symbol 列表，断言 stockish 不落盘、crypto 可落盘  
3. 文档一行写入 `analysis/p_fwd_health_20260720.md` 追加节「§闸门」或独立短文  
4. **不**回写历史已污染行（历史 CSV 只读）；新跑的 replay/shadow 自动干净  

## 判定
- pytest 相关用例绿  
- 手工：对旁路 replay（若存在）过滤后 stockish 计数 = 0  

## 不做
- 不顺手扩大 BLOCKED 名单到「看起来薄」的山寨（那是另一变量）  
- 不改 val 训练池定义（除非已有 loader 级 BLOCKED 本就该一致——若训练池已过滤而前向未过滤，只对齐前向）  

## 完成定义
代码 + 测试 + 报告节 + commit push + RESULTS_v2 一段  
