# 窗口长度是检测器行为变量，不只是裁图尺寸

- **问题**：P1 的 24 根固定窗与 30 根固定窗只差 6 根 K，训练配置、事件、时间切分、
  seed 和负样本比例均相同，但冻结事件尺上的行为完全不同。B1 在 conf=0.10 时召回 99.2%、
  FP/1000=904.9；升到 0.20 后 FP/1000 降到 222.4，召回却塌到 36.3%。B2 在 conf=0.35
  同时达到 Precision 81.9%、Recall 73.5%、FP/1000=81.1。

- **死胡同**：把窗口长度当作近似等价的图像超参，或拿训练 mAP 直接选模型，会漏掉置信度
  分布、重复框和背景误报的结构变化。B1 的训练 Recall 高达 0.992，看起来“学会了”，但完整
  阈值曲线上没有任何工作点能同时通过 precision、recall 和 FP 三门。

- **有效路径**：固定同一批 decision endpoints、同一 event matching 与去重规则，一次预测后
  扫完整冻结阈值网格；先看是否存在满足三门的工作点，再比较该点的 FP、Precision、Recall
  和 duplicates。这样才能区分“检测到了”与“可以在可控误报下检测”。

- **通用规则**：局部检测器的窗口长度必须作为模型行为变量单独实验。比较窗口时保持其余
  数据和训练条件不变，并用事件级完整阈值曲线裁决；单点 mAP、最大召回或主观视觉紧凑度
  都不能替代工作点可行性。单次历史结果只支持当前数据上的选择，不外推为普遍最优长度。

- **牵连**：
  - `configs/local_signal_v2_p1.yaml`
  - `analysis/output/p1_local_signal_v2/{B1,B2,C3}_event_eval.json`
  - `analysis/output/p1_local_signal_v2/comparison.json`
  - `scripts/eval_local_signal_v2_p1.py`
  - `scripts/summarize_local_signal_v2_p1.py`
