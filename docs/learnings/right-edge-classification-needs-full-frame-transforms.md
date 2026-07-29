# 右缘分类必须同时固定物理画布与训练变换

- **问题**：宽幅 K 线图的分类目标位于最右端盘口；通用 ImageNet 分类预处理会随机或居中裁成正方形，模型可能根本看不到需要判断的 T 点。
- **死胡同**：只把 `scale/flip/hsv` 设为零仍不充分。Ultralytics 的 train 路径仍构造 `RandomResizedCrop`，val 路径仍构造 `CenterCrop`；只在参数层写“关闭增强”无法证明时间右缘被保留。
- **有效路径**：先把完整宽图等比缩放到白底正方形并记录源/目标 SHA256、content box 与 padding，再用自定义 classification trainer 把 train/val transform 都替换为确定性的整图 `Resize + ToTensor`。独立验证同时拒绝额外 class 目录或未进 manifest 的文件。
- **通用规则**：视觉标签依赖边缘位置时，第一步检查框架真正实例化的 dataset transform；安全性必须由“物理输入形状 + 运行时 transform”双重保证，不能从 CLI 参数名推断。
- **牵连**：`src/detection/eth3m_v2_classification.py`、`scripts/prepare_eth3m_short_pilot_v2_cls.py`、`scripts/train_eth3m_short_pilot_v2_cls.py`；960×960 白底 letterbox，右缘 x=959，train/val 均无 crop。
