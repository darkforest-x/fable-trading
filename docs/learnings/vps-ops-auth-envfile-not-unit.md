# VPS ops auth lives in root-only EnvironmentFile, not the unit text

- **问题**：公网看板 `/api/ops/*` 默认 `OPS_AUTH_MODE=off` 时匿名 200；上 VPS 后必须 fail-closed，但 token 不能进 git / unit 明文。
- **死胡同**：只靠 `deploy_vps.sh` 强制 `ENABLE_JOB_EXECUTOR=0` 不够——executor 关了仍可读 data/model/pipeline JSON；在 unit 里写 `Environment=OPS_API_TOKEN=…` 会把 secret 落进易被 `systemctl cat` / 备份扫到的 unit。
- **有效路径**：`/etc/fable-trading/ops.env`（mode 600）+ unit `EnvironmentFile=-/etc/fable-trading/ops.env`；deploy 脚本只 re-assert EnvironmentFile 行与 executor=0，永不创建/覆写 token；证据与 journal 只记 `ops_env:present mode=600`。
- **通用规则**：公网只读 ops 面 = token mode + 文件注入 secret + 部署脚本幂等接线；验证用 anon/wrong/auth 三码，不把 token 写入 evidence。
- **牵连**：`scripts/deploy_vps.sh`、`fable-dashboard.service`、`src/webapp/auth.py`、`.omo/evidence/task-6-vps-pipeline.md`
