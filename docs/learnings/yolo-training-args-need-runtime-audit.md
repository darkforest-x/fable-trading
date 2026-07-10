# YOLO 增强铁律必须核对运行参数

- **问题**：训练模块的注释和报告都声称 HSV 已关闭，但 E2.1 日志与 `args.yaml` 显示 `hsv_s=0.05`、`hsv_v=0.05`，违反项目增强铁律。
- **死胡同**：只相信 `SAFE_AUG` 的名字、模块 docstring 和报告里的“全部关闭”，没有逐项核对 Ultralytics 启动时打印并落盘的最终参数。
- **有效路径**：把禁止项做成回归测试，再用一图真实 CLI 训练检查最终 `args.yaml`；源码、日志、落盘参数三处一致后才算合规。
- **通用规则**：每轮 YOLO 训练开始后先审计 `args.yaml`，任何 `hsv_*`、flip、mosaic、mixup 非零都立即把该轮降级为诊断实验。
- **牵连**：`src/detection/train.py`、`tests/test_detection_train_config.py`、所有修复前生成的 YOLO 权重与指标。
