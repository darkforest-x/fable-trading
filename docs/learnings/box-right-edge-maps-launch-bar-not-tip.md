# 框右缘映射的是启动 bar，不是 tip——几何对、语义错

- **问题**：实盘脉冲有候选、有过阈，但 `tip_fresh≤30m=0`。KORU 切流后最短 lag
  仍≈62min；EDEN 等可达数小时。嫌疑是 YOLO 框经 `right_edge_to_bar` 落在窗内旧 bar。
- **死胡同**：
  1. 只报 conf≥0.30「当时可检」却不看框位置——会把窗中段昨日形态当成 tip 开火
     （已有 [detector-lag-is-model-side…](detector-lag-is-model-side-check-box-position-not-just-conf.md)）。
  2. 以为换成 tip-only 调度就能新鲜——tip 窗照样映射 tip−k；影子 KORU lag 72m。
  3. 用「右缘最后 N%」当新鲜度门——KORU 复现 right_norm=0.975 仍 offset=3 bar，
     过得了 5% 像素门、过不了 30min 龄门。
  4. 怀疑 off-by-one / 非正方形图——往返 `x_at`↔`right_edge_to_bar` 200/200 精确。
- **有效路径**：在**检测当下 tip** 截断序列，打印每个框的
  `(right_norm, bar_in_win, offset_from_tip, conf, score)`，并与账本
  `signal_time`/`score` 对齐。KORU：信号 tip/+1/+2 零框，tip+3 才出现右缘→信号 bar
  （score 与 VPS 逐位一致）；EDEN：信号 tip 只框昨日簇，+35 bar 才框到当日启动。
  根因是模型要后文才画框，不是像素公式算错。
- **通用规则**：
  1. 新鲜度门必须按 **bar 偏移**（`window-1-bar_in_win`）与管道预算对齐，不能只看
     归一化右缘百分比。
  2. 离线 `tip_hit`（金标重渲）≠ 实盘 tip 出生率；裁决只认墙钟 lag / tip_fresh。
  3. 改入账几何（只收 tip 附近 N 根）是产品决策，需 owner；默认强制 `signal_i=tip`
     会制造假信号。
- **牵连**：`src/judgment/yolo_candidates.py`（`right_edge_to_bar`、live/tip 调度、
  tip 模式 `signal_i+1>=len` 过滤）、三门 30min、报告 `analysis/p_box_to_bar_lag.md`、
  复现打印 `analysis/output/box_to_bar_repro.txt`。
