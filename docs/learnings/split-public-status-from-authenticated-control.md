# 公网状态面与鉴权控制面必须分路由

- **问题**：VPS 看板可公网打开，但流水线复用了 `/api/ops/pipeline`，手机没有 token 时只能看到 401，导致“公开脱敏状态”与“鉴权控制面”两个目标互相冲突。
- **死胡同**：仅证明带 token 的 ops 接口可用，不能满足手机直接查看；反过来取消整个 `/api/ops/*` 鉴权又会扩大实验、模型和任务接口的暴露面。
- **有效路径**：新增只返回同一脱敏、只读 payload 的 `/api/pipeline`，保留 `/api/ops/pipeline` 鉴权，并继续让所有 POST 在 VPS executor 关闭时拒绝。
- **通用规则**：公网运维界面先把观察能力与控制能力拆成不同路由，再分别验证匿名 200、控制面 401、带鉴权写入 403 三个边界。
- **牵连**：`src/webapp/server.py`、`src/webapp/static/app.js`、`tests/test_ops_pipeline_status.py`、VPS `fable-dashboard.service`。
