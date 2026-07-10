# P2.5 VPS 公网验收（2026-07-10）

## 结论

当前 MA206 项目流水线已部署到 `http://103.214.174.58:8642/` 并通过公网验收。匿名用户可查看脱敏只读七阶段状态；实验、模型、任务等 `/api/ops/*` 控制面仍要求 token；VPS 任务执行器保持关闭。

## 实机结果

| 检查 | 结果 |
|---|---|
| `GET /api/pipeline` 无 token | 200 |
| `GET /api/ops/pipeline` 无 token | 401 |
| `GET /api/ops/pipeline` 有效 token | 200 |
| `POST /api/ops/jobs` 有效 token | 403，executor 关闭 |
| systemd | active + enabled |
| `ENABLE_JOB_EXECUTOR` | 0 |
| ACTIVE | `frozen_tp5_sl2_swap_ma206_20260710` |
| fingerprint | ok |
| 浏览器 | 1440x1000 与 390x844 均通过，0 error / 0 warning |
| 脱敏扫描 | 无绝对本机路径、无 secret value |

公开页面同时显示数据规模、YOLO 诊断证据、MA206 judgment ACTIVE、历史回测边界、前向样本、任务执行器与部署角色。回测明确标为非最终收益证明，forward 明确为 `0/100`。

## 验证命令

```bash
bash scripts/deploy_vps.sh
curl -sS http://103.214.174.58:8642/api/pipeline
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://103.214.174.58:8642/api/ops/pipeline
python3 -m pytest tests/test_ops_pipeline_status.py tests/test_ops_phase01.py -q
```

相关专项测试 `20 passed`。完整证据与截图见 `.omo/evidence/task-6-vps-current-ma206-acceptance.md`。

## 风险与诚实声明

- 公开的是粗粒度只读元数据，不是交易执行器。
- forward 当前 0/100，尚无前向收益证据；页面上的唯一 warning 正是空 forward 样本。
- YOLO 当前阶段只显示已有诊断报告；E2.1b 正式结论仍必须等待训练自然结束后评估。
- 本轮未读取 holdout，未改阈值、成本、TP/SL、ACTIVE，未启动实盘。
