# 入场 close vs next_open 几乎同 PF

- **问题**：默认「信号下一根开盘入场」是否卡死了边？改成信号 bar 收盘价入场，
  同一分边因果规则 + TP5/SL2 下 PF 能否过 1.3？
- **死胡同**：小样（20 币）spread-short 两档都 >1.3，易误判「改入场就过线」；
  全量后两档一起回落到 ~1.24。把「拿掉下一开限制」幻想成救命变量会浪费一轮。
- **有效路径**：单变量只改 fill（`entry=next_open|signal_close`），路径仍从 i+1 起算；
  复用 direction_select 底座一次扫描双档。全量 ΔPF ∈ [−0.004, +0.010]，最好边
  1.245 vs 1.244——**入场约定不是瓶颈**。
- **通用规则**：质疑成交时机时，先在同规则同障碍上对照 fill 约定，再谈拿掉限制；
  规则若用当根 close 判定，close 入场要写清「同打印成交」实盘可行性。TP/SL 是
  另一变量，禁止与 entry 混扫。
- **牵连**：`analysis/p_entry_timing_close_vs_next.md`、
  `scripts/entry_timing_close_vs_next.py`、
  `scripts/direction_select_base_rate.py --entry`、
  `src/judgment/labeling.py`（`entry=`）
