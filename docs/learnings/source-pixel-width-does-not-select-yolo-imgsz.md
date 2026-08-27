# 源图宽度不能替代训练/推理分辨率网格

- **问题**：训练 PNG 全部是 1280×742 时，很容易把“直接用原图”理解成 YOLO 的
  `imgsz=1280` 应天然优于 `imgsz=960`。本轮固定同一批 36,812 张 PNG、标签、split、seed 和
  完整训练配方，只把 `imgsz` 改成 1280，原生 mAP50-95 反而从 0.3313 降到 0.3110。
- **死胡同**：只比较训练日志的最终 val，或只把旧权重的推理 `imgsz` 调到 1280，都不能回答
  问题。前者混合了训练分辨率与评估分辨率，后者在本轮把旧 960 权重的 mAP50-95 降到
  0.2672，会把“推理尺寸失配”误说成“更多像素无效”。源 PNG 未离线缩放也不等于每个训练
  张量逐像素不变，因为 letterbox、补边和冻结配方里的 `translate/scale` 仍在内存执行。
- **有效路径**：先锁住源文件 manifest 和全部训练参数，再做权重（train 960 / train 1280）×
  推理尺寸（eval 960 / eval 1280）的 2×2 网格，并在两套原生单元之外检查两个交叉单元；再用
  同一 confidence 的 easy/hard 背景检查是否以误报换召回。本轮两个交叉单元分别只有 0.2672
  和 0.2187，证明权重与训练尺寸存在匹配效应；但 1280 原生仍全面弱于 960 原生，且 easy
  误报从 3.401 升到 4.082 框/千图，所以可以拒绝替换而不把原因归错。
- **通用规则**：涉及视觉模型分辨率时，第一步锁源图 bytes、标签、split 和完整训练配方；第二步
  跑 train-resolution × eval-resolution 全因子网格。只有同设备、同 val 的原生对原生单元用于
  替换裁决，交叉单元用于诊断分辨率失配；同时固定阈值复测背景误报。不要用源文件像素宽度、
  单个训练日志指标或一次推理尺寸切换替代这个网格。
- **牵连**：`src/detection/train.py` 的 `imgsz/rect/translate/scale`；
  `scripts/evaluate_15m_ma_launch_t3_resolution_grid.py`；
  `experiments/active/exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1/results/`；禁止读取 holdout、
  调阈值或用静态 mAP 作生产/收益裁决。
