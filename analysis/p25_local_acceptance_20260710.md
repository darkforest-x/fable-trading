# P2.5 本地验收（2026-07-10）

## 结论

P2.5 本地只读控制台通过验收。token 鉴权有效，任务执行器保持关闭；实验、议程、任务、数据、模型、流水线六个视图在桌面与 390px 手机视口均可用。未开启实盘、VPS executor 或任何 holdout 读取。

## 运行配置

```bash
OPS_AUTH_MODE=token OPS_API_TOKEN='<ephemeral>' ENABLE_JOB_EXECUTOR=0 \
  python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8651
```

token 仅用于本轮本机浏览器会话，未写入仓库或报告。

## API 验收

| 请求 | 预期 | 结果 |
|---|---:|---:|
| `GET /api/ops/status`（无 token） | 200 | 200 |
| `GET /api/ops/experiments`（无 token） | 401 | 401 |
| `GET /api/ops/experiments`（错误 token） | 401 | 401 |
| `GET /api/ops/experiments`（正确 token） | 200 | 200 |
| `GET` agenda / job-types / jobs / data-hub / model-hub / pipeline | 200 | 全部 200 |
| `POST /api/ops/jobs`（白名单任务） | executor 关闭，403 | 403 |
| `POST /api/ops/jobs`（自由 `cmd` 字段） | schema 拒绝，422 | 422 |

`/api/ops/status` 同时确认 `ops_auth_required=true`、`executor_enabled=false`。

## 浏览器验收

- 视口：`1440x1000`、`390x844`。
- 六个视图均成功加载，无“无法加载”状态；桌面表格均在容器内完整布局。
- 手机端根页面横向溢出均为 0；宽表格只在各自 `.table-wrap` 内横向滚动。
- 最终浏览器控制台：0 error、0 warning。
- 修复了结构化 `config` 显示成 `[object Object]` 的问题；优先显示配置名，无名称时显示稳定的两项摘要。
- 修复了实验表被长配置撑宽、关键指标不可见的问题，并补全 token 输入框的表单语义。

关键截图：

- `.omo/evidence/task-7-p25-local-current/desktop-experiments-final.png`
- `.omo/evidence/task-7-p25-local-current/mobile-experiments-final.png`
- 同目录包含 agenda / jobs / data / models / pipeline 的桌面与手机截图。

## 自动化验证

```bash
python3 -m pytest \
  tests/test_ops_phase01.py \
  tests/test_ops_jobs_phase2.py \
  tests/test_ops_phase3_hubs.py \
  tests/test_ops_data_model_hub.py \
  tests/test_ops_pipeline_status.py -q
```

结果：专项 `66 passed`；复用已安装 torch 的只读 site-packages 后，全仓 `168 passed`。新增回归测试覆盖对象配置的稳定展示契约。

## 风险与诚实声明

- 这是本地安全模式验收，不代表 VPS 公网部署已验收；公网红线与脱敏由下一任务单独验证。
- 手机端实验、数据、模型宽表格需要容器内横向滚动，这是明确的响应式行为，不是根页面溢出。
- 议程保持 Markdown 原文只读展示，没有引入 Markdown 执行器或新依赖。
