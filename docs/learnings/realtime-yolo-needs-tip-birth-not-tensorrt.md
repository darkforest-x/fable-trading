# 「实时 YOLO」优先治 tip 出生率，不是换 TensorRT/DeepStream

- **问题**：Owner 要把 YOLO 从「事后给信号」压成「bar 内实时」；候选仓库（ChartScanAI、
  TensorRT、Savant、DeepStream-Yolo、VLM 标注平台等）看起来都像「实时检测」。
- **死胡同**：
  1. 把脉冲墙钟/推理引擎当主瓶颈——三门 30min 预算下 tip 路径已能落账，缺的是框。
  2. tip-only / 降 conf / 右缘偏置——强制 tip 扫描仍 0 开火，调度救不了出生率。
  3. 上视频流栈（Savant/DeepStream）——输入是 CSV 渲染图不是 RTSP；VPS 未必有 DS。
  4. 未证明 tip_fire>0 就做 forming-bar「真 bar 内」——分布与判断层/入场假设同时漂移。
- **有效路径**：先分清三层事后（收盘等待 / 多窗映射 / 几何语义）；主路径仍是 tip 分布
  训练（v13）+ 贴边门；部署加速与 forming-bar 排在出生率之后。成功标准是 tip_fresh /
  前向，不是 star。
- **通用规则**：谈「实时」先写延迟预算表与「现在零框还是慢框」；零框 → 训练/标注；
  慢框且过不了门 → 再削脉冲或换推理后端。
- **牵连**：`analysis/p_realtime_yolo_within_bar.md`；
  [tip-only 不抬出生率](tip-only-scan-does-not-raise-tip-birth-rate.md)；
  [新鲜度从管道算术推导](freshness-gates-must-be-derived-from-pipeline-arithmetic.md)；
  `TIP_EDGE_BARS`；三门 30min。
