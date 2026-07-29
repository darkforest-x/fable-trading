# HSV 全关必须逐项断言，不能只看注释或 hue

- **问题**：训练入口声明 K 线颜色增强已全部关闭，但 `hsv_s` 和 `hsv_v` 仍为 `0.05`；红绿蜡烛及线条的视觉语义会在训练时被悄悄扰动。
- **死胡同**：只检查注释和 `hsv_h=0` 会产生“HSV 已关闭”的错觉。Ultralytics 将 hue、saturation、value 作为三个独立参数，关闭其中一个不代表关闭整组增强。
- **有效路径**：训练前审计完整的增强参数字典，把 `hsv_h`、`hsv_s`、`hsv_v` 全部设为零，并在单元测试中分别断言三项，令配置文字与实际调用参数一致。
- **通用规则**：图表模型每次新增或复用训练入口时，第一步检查传给框架的最终增强字典；对 flip、mosaic、mixup 和 HSV 三分量逐项做零值测试。
- **牵连**：`src/detection/train.py`、`tests/test_detection_train_speed_knobs.py`、AGENTS.md 的 YOLO 增强禁用纪律。
