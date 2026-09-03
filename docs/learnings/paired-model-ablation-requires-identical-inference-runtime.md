# 配对模型消融必须使用完全一致的推理运行时

- **问题**：`close → HL2` 六均线消融要与已归档 baseline 逐图配对，但 baseline 账本实际由 Mac MPS 生成，首次 treatment 推理却在 RTX 3060 CUDA 上完成。即使 Python 包的大版本一致，device、Python patch 版本和 Torch build 仍不同，浮点路径也不同。
- **死胡同**：把“同一权重、同一阈值、同一批量”误当成完整配对条件，直接比较 CUDA treatment 与 MPS baseline。该做法会把表示变化与运行时变化混在一起；本次有 1,257 个预测置信度发生变化，最大绝对差 `0.003553`，说明不能只凭聚合计数碰巧一致就放行。
- **有效路径**：让比较器在写结果前 fail closed；保留 CUDA 结果并明确标为 supplemental，然后在 baseline 的 `.venv`、Python 3.9.6、Torch 2.8.0、Ultralytics 8.4.89、NumPy 2.0.2、MPS、batch 8 下重跑 treatment。只有环境、样本顺序、权重、图像尺寸、置信度、NMS IoU 和真命中 IoU 全部匹配后，才生成正式配对结果。
- **通用规则**：图像检测 A/B 的配对契约必须同时冻结 `python/torch/ultralytics/numpy/device/batch/imgsz/conf/NMS IoU/真命中 IoU/样本顺序`。跨设备结果可以作为稳健性旁证，不能进入主裁决；聚合离散计数相同也不能替代运行时一致性。
- **牵连**：所有复用历史预测账本的消融实验都应先从账本收据读取真实运行环境，而不是从训练机或预注册措辞推断。比较器应在环境不一致时拒绝输出正式结论，并把纠偏过程单独留收据。
