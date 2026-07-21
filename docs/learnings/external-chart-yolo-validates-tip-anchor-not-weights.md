# 外源 chart-YOLO 印证的是右缘锚定协议，不是公开权重

- **问题**：检测层假设若只在仓内复盘（pad200 / tip-only / A′），会漏掉「外面是否已有更好训法」；Owner 要求外网调研。
- **死胡同**：把 ChartScanAI / foduucom / Roboflow 形态权重当 tip 解药——任务是事后 Buy·Sell 或经典形态，增强常开 flip/mosaic，社区自己报「只事后认」。StreamYOLO / DeepStream 看起来「实时」，解决的是视频延迟，不是「无后文 K 线图不画贴边框」。
- **有效路径**：从论文/规范抽**可测协议**：Chen & Tsai（arXiv:2201.08669）实时窗对象钉在最新时刻；StreamYOLO 用流式口径而非离线 mAP；Ultralytics/Roboflow 要求贴边截断只标可见段；ENIAC 2025 保留均线且禁翻转，但训练图带 crossover **后文**——后者是坑。再用 CPU 审计本仓标签：v13_pad200 train 右缘≥0.95 占 ~96%、框宽 p50≈12 bar，与文献 5–16 对齐；val 未 pad，故不能拿 val mAP 冒充 tip。
- **通用规则**：金融 YOLO 外源默认当**负面教材或协议印证**；先写带出处的单变量假设（发现/确认门槛），再决定是否动 GPU。便宜审计（框几何、渲染对照）可与大训并行；公开权重默认不进主线。
- **牵连**：`docs/RESEARCH_AGENDA_DETECT.md` H-DET-EXT-\*；`analysis/p_yolo_external_sources.md`；`analysis/output/tip_box_geometry_vs_lit.json`；铁律 5；勿杀 `owner_v13_pad200`。
