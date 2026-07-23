# 链路失败主因是 regime + 入场错位，不是出场或打分

- **问题**：holdout#7 把 train 过线的 spread-short+趋势出塌到 PF≈1.0 后，owner 不放弃
  密集链路，要求分清入场 / 出场 / 特征打分 / regime 哪一层是主因。
- **死胡同**：①把塌因归咎于出场参数（再调 trail）——holdout 上 no_tp 与 trail4 **同步**
  塌，排除出场主责；②指望多因子/分数门救当前触发——rich AND 几乎不抬 PF，WF 门
  仍躲不过 2026-04；③把测量 bug 当第一假设——同机随机对照已否；④同一 A 再申请
  holdout#8。
- **有效路径**：train-only 做入场×出场抬升分解 + atr/BTC 切片 + 手标 vs 规则重叠。
  结论：**主因 = regime 不迁移（强）+ 因果入场与 owner short Jaccard≈0.05 / 召回≈25%
  （强）**；出场是 train 放大器；打分救不了当前触发器。Holdout#7 否的是预注册 A
  可交易，不是「密集永远无边」。
- **通用规则**：配置 holdout 证伪后，先做**层归因**（入场集合重叠、regime 切片、
  出场同塌检验），再开单变量新假设；禁止在已证伪配置上旋钮续命或堆 OHLCV。
- **牵连**：`analysis/p_chain_failure_attribution.md`；`scripts/chain_failure_attribution.py`；
  `p_short_trend_holdout7`；`holdout-pf-collapse-is-not-automatically-a-measurement-bug`；
  `train-monthly-pf-does-not-imply-holdout-edge`。
