# 任务14：H16 放量突破入场（发现级 val-only）

## 铁律摘要
- train/val only；禁 holdout  
- 单变量：**只改入场时机**，标签障碍仍用主线 TP5/SL2  
- 不改 features 主表也可：入场逻辑可放在 labeling/candidates 层  
- 不碰 forward_log / frozen ACTIVE  

## 假设（RESEARCH_AGENDA H16）
现行：密集确认 bar 即信号，次根开盘入场。  
挑战：信号后 ≤6 根内寻找「收盘在密集带上方且 volume_z > 阈值」的突破根，以其 **次根开盘** 入场；更晚入场换确认。

## 做什么

1. 实现 `label_candidate_vol_breakout_entry`（名可微调，docstring 写清用到的列与窗口、无前视）  
   - volume_z 阈值：先固定一个预注册值（如 1.0），**禁止**在 val 上网格搜阈值后只报最好的；若要扫，全部写入表且主结论用预注册值  
2. 脚本 `scripts/h16_vol_breakout_entry.py`：  
   - 同池 expanded SWAP 候选  
   - 对照：现行入场 + TP5/SL2 vs H16 入场 + TP5/SL2  
   - 输出 val AUC、perm p、top-decile 毛/净@maker0.06%、胜率、笔数、单特征 baseline  
3. 报告 `analysis/p15_h16_vol_breakout_entry.md`（质量清单按 AGENTS.md）  
4. **判定（预注册）**：净@maker **与** 胜率 **同时** ≥ 基线才算发现级通过；只改善一个 → 未通过  

## 不做
- 不把 H16 接入前向  
- 不与 H1/H3 出场同时改（禁止双变量）  

## 完成定义
脚本 + 报告 + 必要测试 + commit push + RESULTS_v2  
