## 问题

live 扫描会使用 tip、tip-1、tip-2 三个起始窗口。局部窗口中的 bar 198 满足最后两根 edge gate，但当窗口本身从 tip-2 开始时，它映射回整条序列的 tip-3，仍可进入打分路径。

## 失败尝试

只在每个 200-bar 图内检查 `bar_in_win >= 198`，并把“靠近窗口右缘”当成“靠近市场最新 closed bar”。窗口坐标正确，但窗口原点不同，局部 age 不能代表全局 age。

## 有效做法

保留局部 edge gate 作为框位置质量门；所有窗口完成映射、合并和去重后，再用唯一的 `latest_closed_i` 断言 `latest_closed_i - signal_i <= protocol.max_tip_age_bars`。局部 edge reject 与全局 age reject 使用独立计数器。

## 可推广原则

任何滑窗系统的最终时效性都必须在全局坐标系校验。局部坐标只能证明候选在窗口中的位置，不能证明它相对全局最新事件的新鲜度。

## 本次涉及

- `src/judgment/yolo_candidates.py`
- `src/judgment/forward_scan.py`
- `src/judgment/protocol.py`
- `tests/test_global_tip_age_gate.py`

