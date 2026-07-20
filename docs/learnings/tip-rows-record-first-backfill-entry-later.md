# 盘口信号先入账后回填,而不是等字段齐了才入账

- **问题**:前向账本的行 schema 要求入场价/maker 判定,而这些字段来自信号 bar
  的**下一根** bar——盘口信号天然缺字段,旧代码的选择是直接丢弃(`entry_i >=
  len` → None),每笔实盘信号白白损失 15~22 分钟先机。
- **死胡同**:第一反应是给账本加"pending"新状态列或新表——schema 变更会波及
  normalize/merge/看板/执行器全链路。第二反应是留空字段——但 TG 通知和执行器
  把 entry_price 当 mark 兜底,空值会让下游显示/计算翻车。
- **有效路径**:三个字段三种处理:entry_time 其实**已知**(= 信号 bar 收盘时
  刻),直接写真值;entry_price 写**有明确语义的代理**(信号 bar 收盘价≈下根
  开盘,TG/执行器立即可用);maker_filled 留空——它本来就是"未知",顺便当
  "待回填"哨兵。merge 端:凡 previous 的 maker_filled 为空且新记录不为空,
  就整组覆盖三个入场字段;detected_at 永远保留首见值(延迟统计的根基)。
  关键判断:**挑一个语义上真正未知的字段做哨兵**,而不是加状态位——CSV 往返
  NaN 天然保真,零 schema 变更。
- **通用规则**:流式系统里"字段还没发生"的行,先用已知值+带语义的代理+一个
  天然未知字段作哨兵落盘,由幂等 merge 负责收敛到真值;不要为"暂缺"发明新
  状态机。回填规则必须写明哪些字段永不覆盖(如 detected_at)。
- **牵连**:`src/judgment/forward_scan.py`(resolver tip 分支、record 构造)、
  `src/judgment/forward_records.py`(`_entry_pending` + merge)、
  `src/judgment/yolo_candidates.py`(live 模式放行 tip bar,full 模式不放——
  离线建数据集必须有入场 bar)。端到端保护:`tests/test_tip_realtime_path.py`。
  相关:[新鲜度阈值从管道时序推导](freshness-gates-must-be-derived-from-pipeline-arithmetic.md)
