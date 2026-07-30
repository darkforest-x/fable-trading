# 分类模型预处理必须保留完整时间轴

- **问题**：因果图虽然只到 signal bar，但 Ultralytics 分类训练对非方形输入使用
  `RandomResizedCrop`，验证使用 `CenterCrop`；`1280×742` 图会被裁掉左右边缘，最右侧
  signal bar 可能完全不进模型。
- **死胡同**：只把 `scale=0` 就当作“关闭裁剪”。源图宽高比超出默认 ratio 范围时，
  scale 为 0 仍会走中心裁剪回退。
- **有效路径**：方向数据单独渲染为 `640×640` 方形画布，训练仍固定 imgsz=320；禁止
  flip/HSV/scale/erasing/auto augment，并在训练入口读取 summary 拒绝非方形数据集。
- **通用规则**：时序图的因果性要覆盖像素生成和模型预处理全链路；输入文件无未来不够，
  resize/crop 也必须保证信号端点仍可见。
- **牵连**：`src/detection/build_direction_dataset.py`、
  `src/detection/train_direction_classifier.py`、Ultralytics classification transforms。
