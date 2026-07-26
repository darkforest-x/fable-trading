# full 模式的因果性靠检测器"学会贴右边",不是靠代码门 —— 换个检测器就会静默漏未来

**日期**:2026-07-26
**类型**:审计/反直觉结论(验 short 检测器 v1b 时发现)

## 一句话

`scripts/yolo_candidate_source.py` 调 `scan_series_with_yolo` **没传 `mode=`**,走默认
`mode="full"`,而 full 模式里 `apply_tip_edge = mode in ("live","tip")` = **False**,
框可落窗内任意位置 → 理论上可看未来。**实测却是因果的**(lookahead p50=0),因为
`owner_side_short_tip_v1b` 是 tip 对齐训的,**它自己只画右边缘框**。
**保证来自模型行为,不是代码约束——换个不 tip 对齐的权重,同一条路会静默把未来漏回来。**

## 怎么发现的(先怀疑,再实测,不靠假设)

1. **怀疑**:6.61 回测就是被 `mode="full"` 吹起来的(候选池胜率 37% vs 因果 29%);
   `yolo_candidate_source.py:174` 恰好没传 mode。
2. **反证一(汇总)**:`signal_i mod 50`(STRIDE=50)分散在 38/50 桶 → 看起来框没贴右边。
3. **反证被推翻(分币)**:每个币**单独**看,`mod 50` 集中度中位数 **100%**(100/100 币 >50%)。
   汇总分散只是因为各币 `signal_time_lo` 导致 `first_start` 偏移不同,池化后被抹开。
4. **直接测量**(`scripts/diag_full_mode_lookahead.py`,3 币 150 窗):
   `bar_in_win` p10=p50=p90=**199**(WINDOW=200 的最后一根),
   `lookahead=(window-1)-bar_in_win` p50=**0**、mean=0.1、**99.3% ≤2 根**。

## 教训

- **"代码路径没门"≠"数据被污染"**;**"实测没污染"≠"这条路安全"**。两件事要分开断。
  本次结论:**这批 short 候选池干净可用**,但**这条代码路径不安全**。
- **汇总统计会骗人**:池化 100 个起点不同的币,把"每币 100% 集中"抹成"38/50 桶分散"。
  怀疑泄漏时,**先按最小独立单位(单币/单序列)看,再池化**。
- **建议(需 owner/并行会话协调后改)**:离线建池时显式断言框贴盘口——
  例如 `yolo_candidate_source.py` 传 `mode="tip"`,或在 full 模式下加
  `assert bar_in_win >= window - TIP_EDGE_BARS`,把"靠模型自觉"变成"代码保证"。
  在改之前,**任何换检测器权重的建池都必须重跑本诊断**。

## 相关

- 诊断脚本:`scripts/diag_full_mode_lookahead.py`(只读,不写数据集)
- 产物:`analysis/output/diag_full_mode_lookahead.json`
- 涉及:`src/judgment/yolo_candidates.py:275`(`apply_tip_edge`)、
  `scripts/yolo_candidate_source.py:174`(未传 mode)
- [[dense-cluster-has-no-causally-tradeable-direction-edge]]
- 铁律 12(检测只认盘口)
