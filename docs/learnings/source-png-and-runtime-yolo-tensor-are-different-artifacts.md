# 源 PNG、数据加载张量和标签叠加图必须分开验真

- **问题**：同一句“训练图没压缩”既可能指磁盘里的 1280×742 PNG 没被离线改写，也可能被理解为模型前向看到的像素逐字节等于源图。YOLO 的 letterbox、stride 补边、scale/translate 与归一化让后者并不成立。
- **死胡同**：只展示源 PNG 或带框预览图无法回答“模型实际看到了什么”；预览叠加框会污染输入像素，而 `plots=false` 的历史训练又没有留下 `train_batch*.jpg`，事后不能假装存在原始运行截图。
- **有效路径**：在原训练主机上，用冻结的数据、保存的 `args.yaml`、同版本 Ultralytics、相同 seed/workers 重放 train loader；分别落盘预归一化 RGB 张量与仅供查看的标签叠加副本，再独立重放一次比较输入 SHA 和变换后标签。模型输入随后若只做 `float()/255`，该 PNG 就是可无损查看的像素证据。
- **通用规则**：回答“模型实际用的图”时先拆成三层：源文件、数据加载后的模型输入、标签张量/叠加预览；报告每层尺寸和哈希，禁止把任意一层简称为“原图”。新训练应默认保存至少一个首轮 batch 的输入与目标哈希，避免只能事后重放。
- **牵连**：`scripts/audit_15m_ma_launch_t3_model_inputs.py`、训练 `args.yaml`、`imgsz/rect/scale/translate/multi_scale`、Ultralytics 版本、训练主机与 `plots` 开关；审计回执在 `experiments/active/exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1/results/model_input_audit_3060/`。
