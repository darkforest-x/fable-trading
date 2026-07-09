# CI gates should stay on lightweight contracts

- **问题**：P2-9 要在 push 时跑冒烟测试，但仓库的 `requirements.txt` 同时包含 YOLO 训练栈（torch/ultralytics/opencv）和判断层依赖；如果 CI 直接安装全量依赖，普通工程 gate 会被检测层重依赖拖慢、拖脆。
- **死胡同**：把 `pip install -r requirements.txt` 放进 GitHub Actions 最省事，但会让每次文档、判断层或看板小改都承担 YOLO 环境成本；这不是 P2-9 要保护的行为边界。
- **有效路径**：把冒烟测试写成纯判断层/数据/看板契约：barrier 四路径、组合模拟不变量、loader 合并去重、update_okx 幂等，再让 CI 只安装这些测试会实际 import 的轻量依赖，YOLO 训练继续留给本地 `.venv` 和专项离线管道。
- **通用规则**：CI 的第一道 gate 应保护高频改动的核心契约，而不是重跑所有低频重任务；重依赖链路需要专项 job 或离线工单，不能混入普通 push gate。
- **牵连**：`.github/workflows/tests.yml`、`tests/test_labeling_paths.py`、`tests/test_portfolio_simulation.py`、`tests/test_loader_update_smoke.py`。
