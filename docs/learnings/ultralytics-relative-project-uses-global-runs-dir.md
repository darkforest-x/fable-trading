# Ultralytics 的相对 project 会继承全局 runs_dir

- **问题**：仓库训练器传入 `project="runs/detect"` 时，Ultralytics 没有按当前仓库解释，反而拼到用户级持久设置中的旧 checkout，训练在创建权重目录前就写错位置并失败。
- **死胡同**：仅保证 shell 的工作目录在仓库内并不足够；Ultralytics 会把相对 `project` 交给其全局 `runs_dir` 解析，因此同一命令在不同机器或用户设置下可能落到不同根目录。
- **有效路径**：从训练器文件位置解析仓库根目录，并把 `project` 固定为仓库内 `runs/detect` 的绝对路径；解析器必须同时支持源码位置 `src/detection/train.py` 和上传到 GPU 根目录的 `train_safe.py`，用测试锁住两种部署形态。
- **通用规则**：凡第三方框架带持久化全局输出设置，训练入口都必须传仓库内绝对输出路径，不能依赖 cwd 或用户级默认值。
- **牵连**：`src/detection/train.py`、`tests/test_detection_train_speed_knobs.py`、Ultralytics `settings.json`、训练产物可追溯性与沙箱写权限。
