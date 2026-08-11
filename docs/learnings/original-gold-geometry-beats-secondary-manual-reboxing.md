# 原始金标几何优先于二次人工重框

- **问题**：为了把旧框中已经启动的K线移出去，Codex对61张候选再次逐图目测并画橙框；Owner发现不少橙框仍然不准。
- **死胡同**：把“语义理解”误写成一轮新的主观标注。即使逐图处理、没有使用未来收益，二次目测仍会引入新的位置偏差，并受4–7根先验锚定；61框中56框恰为5根就是警报。
- **有效路径**：回到Owner最早在Label Studio手画的独立框，并与Owner后来亲自确认的`short`方向逐框精确联结。外层重裁只负责缩短输入窗口，内层核心从原手框正中心机械截取；未来48根只用于Owner审核，不参与几何生成。
- **通用规则**：只要存在可恢复的Owner原始坐标，第一步必须做标签血缘联结和原框重投影；不得先由Codex或模型重新猜边界。派生框只能作为预览，不能覆盖源金标。
- **牵连**：`data/benchmark_exemplars.json`、`analysis/output/owner_side_review/review_sheet.csv`、`scripts/build_w20_midbox_dataset.py`、`scripts/rebox_owner_eth_shortdelay_review200.py`、holdout禁读与3–5根最大后文约束。
