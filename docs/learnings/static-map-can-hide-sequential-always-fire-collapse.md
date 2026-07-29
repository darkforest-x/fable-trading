# 静态 mAP 可以掩盖连续盘口“几乎恒真”的检测崩塌

- **问题**：ETH 3m 做空 pilot 在 48 张静态 val 图上得到 mAP50 0.735，
  但对训练结束后的连续严格 OOS 逐 bar 回放时，在 774 根 eligible bars 中开火 772 根，
  原始开火率 99.74%。18 根间隔去重后仍有 26.67 笔/有效日，已经不是事件检测器。
- **死胡同**：只看静态 val 的框级 P/R/mAP，或在看到连续回放后继续调 conf。
  稀疏抽取的正图和少量 owner hard negatives 没有覆盖同一事件前后的连续相邻窗口，
  因而无法检验模型是否只是学会“在右边缘放框”。回放后调阈值既会污染开发集，
  也只能压低分数，不能补出时间选择力。
- **有效路径**：在进入判断层和 holdout 前，先做因果逐 bar 密集回放：窗口只到决策 bar，
  与所有 train/val 图片保持零像素重叠，先报告去重前 raw fire density，再报告固定间隔后的事件密度；
  经济性必须对照同 run、同波动桶的随机入场。本轮严格 OOS 模型扣 20bp 后均值 -31.97bp，
  匹配随机 -31.17bp，超额 -0.80bp；间隙回放超额 -4.43bp，确认去重没有创造选择力。
- **通用规则**：检测器晋升必须先通过一个**训练前锁定的连续盘口回放门**，且 raw fire density
  是第一指标，不能被 NMS、冷却间隔或判断层遮住。数据集应按事件成组：一个可交易正时点，
  配套加入同一事件之前的“未形成”和之后的“已经太晚”连续硬负例；模糊带忽略，不强行标负。
  新版本训练前先封存新的连续 OOS，并由 owner 预先确定允许的原始/去重密度上限。
- **牵连**：`scripts/backtest_eth3m_short_pilot_v1.py`、
  `scripts/validate_eth3m_short_pilot_backtest.py`、
  `analysis/p_eth_3m_short_pilot_v1_backtest.md`、
  `datasets/eth_3m_short_pilot_v1/`、`TIP_EDGE_BARS`、`MIN_GAP_BARS`、检测置信度与 holdout 晋升门。
