# BGR模型输入与浏览器RGB预览要分开留证

- **问题**：逐笔YOLO图册视觉检查时，原本红绿K线显示为紫色与橄榄色；数值推理与原监控路径一致。
- **死胡同**：把OpenCV的BGR数组直接交给PIL保存，再因PNG像素哈希一致就当显示也正确。字节守恒只能证明通道值没动，不能证明颜色解释一致。
- **有效路径**：确认绘图器和Ultralytics的numpy入口均按BGR解释。保留原输入数组文件及哈希，另生成RGB浏览器预览并记录来源与转换哈希；不重跑模型、不覆盖原预测证据。
- **通用规则**：图像跨OpenCV、PIL和浏览器边界时显式记颜色空间；模型数组和展示文件分开，视觉检查必须包含已知红绿语义。
- **牵连**：`yoyo/evaluation/spike_eth_yolo_delivery.py`、`yoyo/layers/l1_detection/render.py`、本轮`display_manifest.json`。
