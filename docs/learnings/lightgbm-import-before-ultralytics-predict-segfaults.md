# macOS venv 里先 import lightgbm 再跑 ultralytics predict 会段错误（exit 139）

- **问题**：路 C val 窗重扫脚本冒烟即崩，exit 139（SIGSEGV），无任何 Python traceback。
  同样的 `scan_series_with_yolo` 管道在 `scripts/yolo_candidate_source.py` 里一直正常。
- **死胡同**：先怀疑 `OMP_NUM_THREADS=1` 或 matplotlib 渲染与 torch 的交互——
  单独测 render、单独测 predict、调换 render/load 顺序，都各自正常，无法定位。
  纯 `import lightgbm; import torch; matmul` 最小复现也不崩——问题不在 import 本身。
- **有效路径**：二分导入组合后锁定：**同进程内 lightgbm 已 import 时，第一次
  `ultralytics model.predict()` 必崩**；反之先 predict 再 import lightgbm 则正常。
  根因是两份 OpenMP 运行时（lightgbm 自带 libomp vs torch 的 libomp）在 YOLO
  首次前向初始化线程池时冲突。崩溃点在 C 层，所以 Python 无 traceback。
  修复：扫描脚本不 import 任何会拉 lightgbm 的模块（`src.judgment.train` 是
  隐蔽入口——为了 `HOLDOUT_START` 一个常量就会把 lightgbm 拉进来），打分放到
  独立进程（`scripts/compare_v12_valwin_scores.py`）。
- **通用规则**：exit 139 + 无 traceback + torch/YOLO 在场 → 第一假设是双 libomp。
  检查项：进程里是否同时有 lightgbm 和 torch；谁先初始化。YOLO 扫描进程保持
  "只有 torch"，LightGBM 打分永远另开进程。为一个常量 import 重模块前先看它的
  依赖链。
- **牵连**：`scripts/scan_v12_valwin.py`（本地 mirror 了 `HOLDOUT_START`，注释写明
  原因）、`scripts/compare_v12_valwin_scores.py`、`src/judgment/train.py`（顶层
  import lightgbm）。`scripts/yolo_candidate_source.py` 之前没踩坑纯属侥幸：它不
  import train。VPS 前向管道若未来把打分并入扫描进程，同样会踩。
