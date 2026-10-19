# PNG 容器哈希与解码像素一致性是两个复现轴

- **问题**：从同一行情和冻结渲染代码重建的 620 张 baseline 图，逐像素完全一致，但 PNG 文件 SHA-256 全部不同；若只认文件哈希，合法的颜色审计会在模型推理前被误判为渲染漂移。
- **死胡同**：把“同一图像”简化成 PNG 字节必须相同。压缩器版本、编码参数或元数据可以改变容器字节而不改变模型读到的像素，容器哈希失败不能自动推出视觉输入失败。
- **有效路径**：继续用 manifest SHA 校验原始数据集文件的身份和未篡改性；对重建 parity 单独校验解码后的 shape、dtype 与全部像素，并把 `620/620` 容器哈希不一致如实记录。因为原预注册写的是容器 SHA，必须在任何模型预测发生前留一份带哈希的 amendment，再改用解码像素硬门，不能看完结果后补口径。
- **通用规则**：复现声明至少拆成“产物容器身份”和“消费者实际输入内容”两个轴；前者用于追踪同一文件，后者用于判断模型是否看到同一数据。改变预注册硬门时必须先停、查明机制、留前置修订凭证，再继续实验。
- **牵连**：OpenCV PNG encoder 版本、`datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl`、`analysis/output/p1_yolo_color_semantics_audit_20260902/preregistration_amendment_1.json`、`scripts/audit_yolo_color_semantics.py`。
