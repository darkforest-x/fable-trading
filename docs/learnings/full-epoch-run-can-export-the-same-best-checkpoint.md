# 跑满轮数不等于得到新模型——先比较 `best.pt` 张量再重跑下游

- **问题**：同一 YOLO 数据集与 seed 先以 `patience=10` 训练，随后关闭早停重新跑满
  40 轮。两个运行目录和 `best.pt` 文件 SHA 不同，容易被当成两个模型并重复执行昂贵的
  行情扫描。

- **死胡同**：只比较运行名、训练轮数、文件时间或整个 checkpoint 的 SHA。Ultralytics 的
  `best.pt` 同时保存运行名、日期、训练参数与曲线；即使实际模型参数完全相同，这些元数据也会
  让文件字节不同。反过来，只看第 40 轮指标又会把 `last.pt` 和自动选择的 `best.pt` 混为一谈。

- **有效路径**：把比较拆成三层。先逐列确认两次 `results.csv` 的重叠轮次是否一致；再从
  checkpoint 取 `ema`（无则取 `model`）的 `state_dict`，按键、dtype、shape 和张量值逐项比较；
  最后把 `best.pt` 与 `last.pt` 分开评价。本例前 16 轮除耗时外逐值一致，两份 `best.pt` 的
  499 个张量全部相同，都是第 6 轮；40 轮模型只是继续训练到 `last.pt`，且 mAP50-95 从
  0.77768 降至 0.69426。

- **通用规则**：同数据、同 seed 的“关闭早停重跑”完成后，任何下游推理之前先做 checkpoint
  张量身份审计。若新旧 `best.pt` 张量完全相同，下游输出在相同软件、输入和阈值下没有新的模型
  变量，不应为不同文件 SHA 重复消耗 holdout；若要研究最后一轮，必须明确使用并命名 `last.pt`，
  不能称它为最佳模型。

- **牵连**：
  `analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960/`、
  `analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960_full40/`、
  `scripts/train_15m_ma_launch_owner_grade_a8000_neg24000_full40_960_on_3060.sh`；比较时保持
  数据集、`imgsz=960`、batch、seed 和依赖契约不变。
