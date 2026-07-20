# 任务13：H3 MA-exit 前向 shadow（第二账本）

## 铁律摘要
- **不替换** 主线 TP5/SL2；不改 ACTIVE  
- 影子日志路径固定：`data/forward_log_h3_ma_exit.csv`  
- 禁 holdout；禁改主 `forward_log.csv`  
- 单变量：出场结构 = MA-exit（收盘跌破 EMA21），其余对齐主线

## 背景
发现级报告 `analysis/p15_h3_ma_exit.md`：top 净@maker +0.512% vs TP5 +0.151%，均持仓 11.7 vs 20.1。  
无前向确认不得升级。

## 做什么

1. 将 `label_candidate_ma_exit` 的出场语义移植为前向 resolver  
   （参照 `docs/H1_SCALED_FORWARD_SHADOW_PLAN.md` 与现有 `forward_track_h1_shadow.py` 模式）：
   - 入场：次根开盘；maker_filled 规则与主线相同  
   - 出场：持仓期内 **已收盘** bar 的 close < ema21 → 平仓；否则至 horizon timeout  
   - 无固定 TP/SL（与标签一致）；报告中诚实写出左尾风险  
2. CLI：`scripts/forward_track_h3_shadow.py`（或 `forward_track.py --config h3_ma_exit --out ...`）  
   - **只写** `data/forward_log_h3_ma_exit.csv`  
   - 信号源：与主线相同候选 + **同一 ACTIVE 模型与 q90 阈值**（先共享分数，只换出场——单变量）  
3. 测试：障碍路径 2–4 条（立刻 ma_exit / timeout / 边界）  
4. 跑一遍 shadow（数据允许的前提下），写 `analysis/p15_h3_forward_shadow.md`：
   - 复现命令、样本数、outcome 分布、毛/净（标明成本）、与主线旁路 replay **同信号集合**对照（若主线 replay 存在）  
   - 明确：**发现级前向草稿，不计入 0/100**  

## 判定（本任务工程完成即可，不要求经济性通过）
- shadow 可跑通 + 测试绿 + 报告含风险声明  

## 不做
- 不把 H3 写进 daily digest 主 PF  
- 不重训 LightGBM  

## 完成定义
代码 + 测试 + 报告 + commit push + RESULTS_v2  
