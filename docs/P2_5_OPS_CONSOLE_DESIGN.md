# P2.5 操作台化设计（Ops Console）

> **2026-07-20 状态**：Phase 0–3 **均已实现并合主线**（job runner 默认关；data/model hub 只读）。  
> 本文保留为设计底稿；**待办不在此文件**。实时纪律见 `CLAUDE.md` 实盘节 + `HANDOFF.md`。  
> **红线（本文不越）**：不评估 holdout、配置变更不走网页直改、VPS 禁止开 job executor。

---

## 0. 愿景与边界

### 0.1 Owner 愿景（中文）

把现有「只读证据看板」进化为**项目控制中枢**：  
在浏览器里看实验注册表、发起白名单任务、查看数据/模型状态、监控前向异常——而不是在 SSH / nohup / 本地脚本之间跳。

### 0.2 非目标（明确不做）

| 不做 | 原因 |
|------|------|
| 自由 shell / 任意 CLI 字符串 | 公网或误操作 = 实验室沦陷 |
| 网页表单直改阈值/TP/SL/成本 | 配置永远走 git 评审（AGENTS 升级规则） |
| VPS 上默认跑训练/sweep | 训练资源在 Mac；VPS MemoryMax ~900M |
| 模拟盘 / 实盘下单 | **P3**，不在 P2.5 |
| YOLO 重训、auto_label 改参 | 另属 P2-11；操作台不暴露这些入口 |
| holdout 评估按钮 | 铁律 1；任何 UI 不得提供 |

### 0.3 部署角色

```
MacBook（训练机 / 可执行）              VPS 103.214.174.58（公网只读 + 可选鉴权）
├─ ENABLE_JOB_EXECUTOR=1（默认关，需显式开）  ├─ ENABLE_JOB_EXECUTOR=0（强制默认关）
├─ 白名单 job runner + SSE 日志              ├─ 实验注册表 / 议程 / 数据热力 / 模型列表（只读）
├─ 端口开发用 8643（避 8642）                ├─ systemd fable-dashboard :8642
└─ 产物 rsync → VPS（现有 deploy_vps.sh）    └─ 鉴权前置；无 auth 则禁止任何 POST/执行面
```

---

## 1. 总架构

### 1.1 分层

```
┌──────────────────────── Frontend (static/, DESIGN.md tokens) ─────────────────┐
│ Tabs: 总览 | 回测 | 信号 | 前向 | 实验 | 议程 | 任务 | 数据 | 模型 | (配置只读) │
└────────────────────────────────────┬──────────────────────────────────────────┘
                                     │ REST (+ SSE 仅 job 日志)
┌────────────────────────────────────▼──────────────────────────────────────────┐
│ FastAPI (src/webapp/server.py 扩展，路由保持 thin)                              │
│  middleware: auth (Phase 0) → feature flags → no_cache_static                  │
├──────────────┬──────────────┬──────────────┬──────────────┬───────────────────┤
│ Phase1       │ Phase2       │ Phase3       │ Phase4       │ 既有              │
│ registry     │ job runner   │ data/model   │ forward      │ overview/backtest │
│ agenda       │ whitelist    │ hub + promote│ alerts       │ chart/forward     │
└──────────────┴──────┬───────┴──────────────┴──────────────┴───────────────────┘
                      │ subprocess argv from registry only
                      ▼
              sqlite jobs.db + logs/jobs/<id>.log
```

### 1.2 模块建议落点（实现时新建，不重构旧模块）

| 路径（建议） | 职责 |
|--------------|------|
| `src/webapp/auth.py` | Token / 可选 basic 校验；从 env 读密钥，永不硬编码 |
| `src/webapp/ops_flags.py` | `ENABLE_JOB_EXECUTOR`、`OPS_REQUIRE_AUTH` 等特性开关 |
| `src/webapp/experiment_registry.py` | 扫描 `analysis/output/*.json` + 关联 `analysis/*.md` |
| `src/webapp/agenda_payloads.py` | 读 `docs/RESEARCH_AGENDA.md` → 结构化或安全 HTML |
| `src/webapp/jobs/` | schema、whitelist、runner、store（sqlite） |
| `src/webapp/data_hub.py` | 覆盖热力、审计摘要（读 `data_audit*`） |
| `src/webapp/model_hub.py` | 列 `models/frozen_*`、指纹校验、active 指针读 |
| `src/webapp/alerts.py` | 前向异常规则（只计算+落盘；通道发送走 `src/notify.py`） |
| `src/webapp/static/` | 新 tab 视图；复用 `style.css` / `DESIGN.md` 组件 |

**原则**：`server.py` 继续 thin；payload 与 runner 逻辑不塞进 route 函数。加法式扩展，不改既有只读 API 语义。

### 1.3 环境变量契约（owner 设置；agent 不写明文进 git）

| 变量 | 默认 | 含义 |
|------|------|------|
| `OPS_AUTH_MODE` | `off` | `off` \| `token` \| `nginx`（应用层信任反向代理已鉴权时可 `nginx` 仅作文档标记） |
| `OPS_API_TOKEN` | 空 | FastAPI Bearer token；**仅 env / VPS secret**，禁止入仓 |
| `OPS_REQUIRE_AUTH` | `0` | `1` 时所有 `/api/ops/*` 与写操作强制鉴权；建议 VPS 永远 `1` |
| `ENABLE_JOB_EXECUTOR` | `0` | `1` 才允许创建/取消 job；**VPS 默认保持 0** |
| `OPS_JOBS_DB` | `data/ops_jobs.sqlite` | 任务库路径（`data/` 不入 git） |
| `OPS_JOB_LOG_DIR` | `logs/jobs` | 任务日志目录 |
| `OPS_MAX_CONCURRENT_JOBS` | `1` | Mac 默认串行，防抢 CPU/磁盘 |
| `OPS_ACTIVE_MODEL_POINTER` | `models/ACTIVE` | 文本指针：当前生效 frozen 工件相对路径（git 可审） |

---

## 2. 第 0 期：鉴权（硬前置）

> **规则**：公网实例上出现任何「执行」按钮之前，必须先有鉴权。  
> 没有 Phase 0，**后面全部不许上 VPS**（含任务页 POST）。

### 2.1 两种方案（Owner 二选一）

#### 方案 A — nginx basic-auth（运维侧）

- VPS 上 nginx 反代 `127.0.0.1:8642`，`auth_basic` + `htpasswd`。
- 应用进程仍可只绑 loopback；公网只见 nginx。
- **优点**：应用零改动即可保护静态页；与现有 systemd 并存。  
- **缺点**：浏览器每次弹窗；SPA 的 fetch 需携带凭证（`credentials: 'include'`）；密码轮换在 VPS 上。
- **Agent 职责**：文档化 nginx 片段 + `deploy` 检查清单；**不**生成/提交密码文件。

#### 方案 B — FastAPI Bearer token（应用侧，推荐默认设计）

- Env：`OPS_AUTH_MODE=token`，`OPS_API_TOKEN=<owner 生成的高熵 secret>`。
- Middleware / dependency：对 `/api/ops/**` 及所有 `POST/DELETE` 校验  
  `Authorization: Bearer <token>`（或 `X-Ops-Token` 头，二选一写死一种）。
- 静态页可先公开只读旧 API；**一旦打开执行器或 ops 写接口，整站建议强制 token**（见 2.3）。
- **优点**：Mac/VPS 行为一致；便于脚本调用。  
- **缺点**：前端需安全持有 token（见 2.4，禁止写进 git 的 `app.js`）。

#### 方案 C — 组合（生产推荐形态）

- nginx basic-auth **或** TLS + IP 限制 包外层；应用内再对 `/api/ops/*` 做 token。  
- 深度防御；实现可分期：先 B 上 Mac，再 A 上 VPS。

### 2.2 鉴权范围矩阵

| 资源 | Auth 前（仅开发） | Auth 后（VPS 最低线） |
|------|-------------------|----------------------|
| 既有 GET `/api/overview` 等 | 可暂保持公开（owner 曾拍板 P2-10「暂不加」） | **Owner 决策 #1**：是否连只读一并保护 |
| GET `/api/ops/experiments` 等只读 ops | 建议同样受 token 保护（实验 JSON 含策略细节） | 必须 |
| POST `/api/ops/jobs` 等写操作 | 禁止上 VPS | 必须 auth + `ENABLE_JOB_EXECUTOR=1` |
| 静态 HTML/JS/CSS | 建议与 API 同策略 | 建议 basic-auth 或整站 token 门禁页 |

### 2.3 门禁 UX（应用侧 token 时）

- 首次打开 ops 相关 tab：显示「粘贴 token」面板（sessionStorage，**不** localStorage 默认，避免共享机器残留；owner 可再定）。
- 未鉴权调用写接口 → `401` + 中文提示「需要 OPS_API_TOKEN」。
- 执行器关闭 → `403` +「本实例已禁用任务执行器（VPS 默认）」。

### 2.4 安全纪律

1. **Agent 永不**在仓库中写入真实密码/token，不在 commit message / 设计文档示例里放伪真实 secret（示例用 `<OWNER_SET>`）。
2. `data/tg_config.json` 既有模式可类比：gitignore + env 回退；token 同理。
3. 日志不得打印完整 Authorization 头。
4. CSRF：Bearer token 不走 cookie 时风险较低；若将来改 cookie session，必须补 SameSite + CSRF。本期用 Bearer。

### 2.5 Phase 0 验收

- [ ] VPS 上未配置 secret 时，任何 job 创建返回失败且无副作用。
- [ ] 错误 token 无法创建 job。
- [ ] `ENABLE_JOB_EXECUTOR=0` 时即使 token 正确也拒绝执行。
- [ ] 仓库 `git grep` 无真实凭证。

---

## 3. 第 1 期：实验注册表 + 研究议程（只读，无风险先行）

### 3.1 实验注册表（Experiment Registry）

#### 数据源

- 主：`analysis/output/*.json`
- 辅：同 stem 或约定映射的 `analysis/*.md` 报告
- **不**扫描 `data/` 大体量 CSV 进索引（避免卡死 VPS）

#### 索引条目字段（归一化后）

| 字段 | 来源策略 |
|------|----------|
| `id` | 文件名 stem，如 `swap_replication` |
| `path` | 相对仓库根路径 |
| `mtime` / `size` | 文件系统 |
| `kind` | 启发式：文件名前缀 `p0_`/`p2b_`/`p3_`/`h9_`/`mtf_`/`exit_`/`swap_`… |
| `tag` / `config` | JSON 内 `config` 字段；若为数组则展平为多行「实验行」 |
| `metrics` | 抽取公共键（见下） |
| `report_path` | 启发式关联 `analysis/p*_*.md` 或 manifest（见 3.3） |
| `schema_note` | 未识别字段保留在 `raw_keys` |

#### 关键指标抽取（best-effort）

JSON 形态不统一（有的是 list-of-configs，有的是 dict summary）。抽取器按优先级尝试：

1. 若 root 为 `list[dict]`：每元素一行，取 `config`、`val_auc`、`perm_p`、`top_gross`、`top_net_*`、`top_win_rate`、`maker_fill_rate`、`n_val`/`n_candidates` 等存在的键。
2. 若 root 为 `dict`：单行 summary；嵌套 `results`/`runs` 则展平。
3. 缺键 → `null`，前端显示 `—`，**禁止编造数字**。

#### API（只读）

```
GET /api/ops/experiments
    ?kind=&q=&sort=mtime|val_auc|perm_p&order=desc
    → { items: ExperimentRow[], generated_at }

GET /api/ops/experiments/{id}
    → { meta, rows[], raw: <json>, report_path?, report_html? }
```

- `report_html`：服务端将关联 markdown 转为**消毒后** HTML（允许 h1–h3、table、code、list；禁 raw script）。可用标准库 + 极简转换，或依赖已有轻量 md 库（**若引入依赖需 owner 批准**；优先零依赖简易 renderer 或前端 `marked` 仅本地打包——默认推荐**后端返回 raw markdown，前端用现有纯文本/pre 先展示，Phase1.1 再升级渲染**）。

#### 前端「实验」页

- 可排序对比表：日期 / kind / config / val_auc / perm_p / top_net_maker / n_val。
- 行点击 → 详情抽屉：指标 tiles + JSON tree（`<pre>`）+ 报告链接。
- 空态：无 `analysis/output` 时中文说明「先 rsync 产物或本地跑实验」。
- 样式：复用 `.panel` / `.table-wrap` / `.tile`（`DESIGN.md`）。

### 3.2 研究议程页

- 源：`docs/RESEARCH_AGENDA.md`（唯一真相）。
- `GET /api/ops/agenda` → `{ markdown, mtime, path }`。
- 前端 tab「议程」：渲染 markdown；状态 emoji（🟢🔴🟡🔵⚪）保持原样。
- **只读**：UI 不提供「改状态」按钮；状态更新仍靠 PR 改 md（与现纪律一致）。

### 3.3 可选 manifest（非必须，Phase1 后可加）

若启发式关联报告噪声大，可后续加：

`analysis/output/registry_manifest.json`（手写或脚本生成，入 git）  
映射 `stem → report / hypothesis_id / notes`。  
**不作为 Phase1 阻塞项。**

### 3.4 Phase 1 验收

- [ ] 本地与 VPS 均能列出当前 `analysis/output` 中全部 JSON。
- [ ] `swap_replication` / `p2b_v3_sweep*` 等多样 schema 不崩溃。
- [ ] 议程页展示与仓库文件一致。
- [ ] 无 POST 路由、无 subprocess。

---

## 4. 第 2 期：任务运行器（核心）

### 4.1 铁律

1. **硬编码命令白名单 only**——请求体只许 `job_type` + 受约束参数对象；**永不**接受 `cmd` / `shell` / `argv` 自由字符串。
2. 参数经 schema 校验后由服务端组装 `list[str]` argv。
3. 工作目录固定为仓库根；`env` 只追加白名单键（如 `PYTHONPATH=.`）。
4. `ENABLE_JOB_EXECUTOR!=1` → 创建接口硬失败。
5. 并发默认 1；队列 FIFO。
6. 超时：每类 job 有 `timeout_sec` 上限（见下表）；超时 kill 进程组。
7. 日志：append-only 文件 + 可选 SSE  tail；不回传密钥环境变量。

### 4.2 白名单 Job 类型

| `job_type` | 组装的命令（示意） | 允许参数（枚举/范围） | 默认超时 | 产物提示 |
|------------|--------------------|----------------------|----------|----------|
| `build_dataset` | `python3 -m src.judgment.build_dataset` | `mode∈{strict,expanded}`；`bar∈BAR_CHOICES`；`horizon_bars∈[12,576]`；`out` 仅允许相对 `data/` 下预置模板名 | 2h | `data/judgment_dataset_*.csv` |
| `barrier_sweep` | `python3 -m src.judgment.barrier_sweep`（或项目现行入口） | 仅暴露已有 CLI 的安全子集：如预设 pack 名枚举；**禁止**任意 py 表达式 | 4h | `analysis/output/*sweep*.json` |
| `swap_replication` | `python3 scripts/swap_replication.py` | 无参或固定 flag 子集（若脚本后续加 flag，先改白名单再暴露） | 2h | `analysis/output/swap_replication.json` |
| `update_okx` | `python3 -m src.data.update_okx` | `bar∈BAR_CHOICES` | 1h | `data/kline_fetched/` |
| `forward_track` | `python3 scripts/forward_track.py` | `start` 可选 ISO 日期，但 **UI 默认不提供改正式窗口**；高级参数需 owner 批准后再开 | 30m | `data/forward_log.csv` |
| `deploy_self` | `bash scripts/deploy_vps.sh` | **无参**；仅 Mac 侧有意义；二次确认文案 | 15m | VPS 同步 |

**明确永不入白名单**：`train` 带 `--eval-holdout`、任意 `rm`、`git push --force`、YOLO train、`relabel_yolo_dataset`、`auto_label` 相关、自由 `bash -c`。

### 4.3 参数校验伪接口

```text
POST /api/ops/jobs
Authorization: Bearer <token>
Body: {
  "job_type": "update_okx",
  "params": { "bar": "15m" }
}
→ 201 { "id": "...", "status": "queued" }

# 非法示例（必须 400）
{ "job_type": "update_okx", "params": { "bar": "15m; rm -rf /" } }
{ "cmd": "anything" }
{ "job_type": "not_in_whitelist" }
```

服务端：`params` → 严格 pydantic/dataclass → `WHITELIST[job_type].build_argv(params)` → 仅返回 `list[str]`。

### 4.4 任务状态机

```
queued → running → succeeded
                 → failed
                 → cancelled
                 → timeout
```

- sqlite 表 `jobs(id, job_type, params_json, status, created_at, started_at, finished_at, exit_code, log_path, error_summary)`
- 进程：`subprocess.Popen` + 新 session（便于 kill 进程组）；stdout/stderr 合并写入 log 文件。
- 重启策略：看板进程重启后，将遗留 `running` 标为 `failed`（reason=`orphaned_after_restart`），不自动重跑（防重复副作用）；`deploy_self` / `update_okx` 尤其如此。

### 4.5 API

```
GET  /api/ops/jobs                 # 历史，分页
GET  /api/ops/jobs/{id}            # 详情 + 末尾 N 行日志
GET  /api/ops/jobs/{id}/log        # text/plain 或 SSE text/event-stream
POST /api/ops/jobs                 # 创建（需 executor+auth）
POST /api/ops/jobs/{id}/cancel     # SIGTERM → 宽限 → SIGKILL
GET  /api/ops/job-types            # 白名单元数据：参数 schema 给前端表单
```

### 4.6 前端「任务」页

- 白名单卡片/下拉：只渲染 `GET /api/ops/job-types` 返回的类型。
- 参数表单：enum → segmented control；数字 → number input + min/max。
- 「运行」按钮：二次确认（中文：说明将执行的人类可读命令摘要，**不是**可编辑 shell）。
- 运行中：日志面板轮询 1s 或 SSE；状态色用 `--up/--down/--warn`。
- 历史表：状态、耗时、产物路径提示（字符串，不自动乱链外网）。
- VPS：整页横幅「执行器已禁用；请在 Mac 看板运行任务」。

### 4.7 Phase 2 验收

- [ ] 模糊测试：任意额外字段/注入字符串无法改变 argv 前缀。
- [ ] VPS 默认配置下 POST jobs → 403。
- [ ] Mac 显式开启后，`update_okx --bar 15m` 与 CLI 等价可复现。
- [ ] 取消与超时不留僵尸进程。
- [ ] 单测：whitelist builder + auth dependency + executor flag（纯 python，CI 可跑）。

---

## 5. 第 3 期：数据与模型中枢

### 5.1 数据页（主只读 + 更新走 runner）

**只读展示**

- 宇宙 × timeframe 覆盖热力：基于 `list_series` / `data/kline_fetched` 文件 mtime 与行数后缀（与 `data_audit` 一致口径）。
- 嵌入或链接 `analysis/output/data_audit_summary.json` + 报告 `analysis/p2_data_audit_report.md` 摘要（stale 数、黑名单已生效说明）。
- 前向日志健康：`data/forward_log.csv` 行数、最近 `detected_at`、距「100 笔裁决」进度（复用 forward payload 逻辑）。

**写操作**

- 「增量更新」按钮 → 仅创建 `job_type=update_okx`（Phase 2 runner）。
- **无**直接写 CSV 的 API。

### 5.2 模型页

**只读**

- 扫描 `models/frozen_*.json` + 对应 `.txt`。
- 展示：`config`、`created_at`、`threshold_val_q90`、`dataset_path`、`dataset_sha256`、特征数、文件是否双存在。
- 指纹校验：现场重算 dataset sha（若本地有该 CSV）vs sidecar；缺失数据则标 `unverifiable`（VPS 常如此）。
- 当前生效：读 `models/ACTIVE`（或 env 指针）→ 高亮。

**晋升 / 回滚（禁止网页直改配置数值）**

```
POST /api/ops/models/promote
Body: { "artifact_id": "frozen_tp5_sl2_swap_20260709" }
```

语义（二选一，**Owner 决策 #3**）：

| 模式 | 行为 |
|------|------|
| **A. git-pointer（推荐）** | API 只在 Mac 生成/更新工作区文件 `models/ACTIVE` 内容为相对路径，并返回「请 commit + PR」提示；**不**自动 git commit。回滚 = 改回上一路径再 PR。看板加载信号时读 ACTIVE。 |
| **B. local-pointer-only** | 仅写本地 `models/ACTIVE`（gitignore 或入 git 由 owner 定）；VPS 靠 `deploy_vps.sh` 同步。仍无网页改 threshold。 |

**禁止**：POST 修改 `threshold_val_q90`、改 feature 列表、上传任意 `.txt` 模型文件（防后门）。

新冻结工件仍通过现有 `scripts/freeze_model.py` 离线产生 → git 入库 → 再 promote。

### 5.3 配置页（只读 + 修改申请流）

- 展示当前硬编码/冻结侧车中的：阈值、成本假设（maker 0.06% / taker 等）、universe 默认、horizon、MAX_CONCURRENT 等**只读**快照。
- 「申请修改」：前端填写期望值 → 后端生成 **unified diff 草案** 或 patch 片段（针对允许的配置源文件列表，白名单路径如 `src/judgment/frozen.py` 默认名、某 yaml——若尚无 yaml 则仅生成 markdown 变更单）。
- 响应：`proposed_diff` 文本 + 「复制到 PR 描述」；**不** `open().write` 目标文件（或仅写到 `output/ops_change_requests/<ts>.md` 供人工处理）。
- 文案固定：「配置变更永远走代码评审，不走网页表单直改。」

### 5.4 Phase 3 验收

- [ ] 数据热力与磁盘一致；无写路径除 job runner。
- [ ] promote 不能改 threshold；只能切换已存在 artifact。
- [ ] 配置页无法静默改仓库文件。

---

## 6. 第 4 期：监控与告警

### 6.1 前向页增强（异常标注）

在既有前向 tab 上叠加只读告警条（规则可配置常量，改规则走 git）：

| 规则 id | 条件（初稿） | 级别 |
|---------|--------------|------|
| `data_stale` | 主宇宙 kline mtime 超过 N 小时未更新（默认 36h） | warn |
| `forward_stall` | 正式窗口长时间 0 新信号且数据非 stale（信息性） | info |
| `loss_streak` | maker-filled closed 连续 SL ≥ K（默认 5） | warn |
| `fill_rate_drop` | 近 M 笔 fill rate 较基线下降超过阈值 | warn |
| `log_corrupt` | forward_log 缺列/解析失败 | error |

API：`GET /api/ops/alerts` → 当前触发列表 + 上次计算时间。  
计算可在请求时轻量完成，或附在 `forward_track` job 末尾写 `data/ops_alerts.json`。

### 6.2 告警通道（Owner 选定后接入）

| 通道 | 现状 | 接入方式 |
|------|------|----------|
| Telegram | 已有 `src/notify.py` + `data/tg_config.json` / env | 复用 `send()`；agent 不读 token |
| Webhook | 无 | owner 提供 URL env `OPS_ALERT_WEBHOOK`；POST JSON |
| 邮件 | 无 | 本期不实现，除非 owner 明确要求 |
| 仅看板 | 默认 | 无 secret 时降级为页内横幅 |

**发送纪律**：缺配置 → warn + no-op（与 notify.py 一致）；管道不因告警失败而崩溃。

### 6.3 Phase 4 验收

- [ ] 人为构造 stale / loss_streak 夹具时 API 返回对应 alert。
- [ ] 无 TG 配置时不抛异常。
- [ ] 不引入实盘下单或 demo API。

---

## 7. 实例策略：VPS vs Mac

| 项 | Mac（开发/训练） | VPS（公网） |
|----|------------------|-------------|
| 看板端口 | 8643（避占 8642） | 8642 systemd |
| `ENABLE_JOB_EXECUTOR` | owner 显式 `1` 才开 | **默认 0，文档禁止改 1**（除非 owner 书面例外） |
| Auth | 建议 token，防局域网误触 | **强制**（Phase 0） |
| 数据完整性 | 全量 data/models | rsync 子集（deploy 现有列表可逐步扩 `analysis/output`） |
| deploy_self job | 允许 | 禁用（在 VPS 上跑 rsync 到自己无意义且危险） |
| 内存 | 可跑 sweep | MemoryMax 900M；禁止 barrier_sweep |

systemd 建议（文档片段，非自动改）：

```ini
# /etc/systemd/system/fable-dashboard.service.d/ops.conf
[Service]
Environment=ENABLE_JOB_EXECUTOR=0
Environment=OPS_REQUIRE_AUTH=1
Environment=OPS_AUTH_MODE=token
# Environment=OPS_API_TOKEN=  → 由 owner 用 systemd 环境文件注入，chmod 600
```

---

## 8. UI / 信息架构（对齐 DESIGN.md）

### 8.1 新增 Tabs（顺序建议）

`总览 · 回测 · 信号 · 前向 · 实验 · 议程 · 任务 · 数据 · 模型`

- 「配置」可作为模型页子面板，避免 tab 过多。
- 移动端：tab 横向滚动；任务日志 mono 12px；表单全宽（既有 390px 纪律）。

### 8.2 视觉

- 继续 dark-only；状态色语义化（成功/失败/警告）。
- 执行按钮用 `--down` 边框或明确「危险操作」样式 + 确认模态；只读页保持安静。
- 无装饰动画；loading 仅 dim view。

### 8.3 文案语言

- Owner 可见 UI 文案：**中文**。
- `job_type`、JSON 键、路径、API：**英文标识符**。

---

## 9. 需要 Owner 拍板的事项（集中清单）

| # | 事项 | 默认建议 | 阻塞阶段 |
|---|------|----------|----------|
| D1 | 鉴权方案：nginx basic-auth / FastAPI token / 组合 | 组合：VPS nginx 或 token 至少一个；应用 token 统一 Mac/VPS | **Phase 0** |
| D2 | 既有只读 API（overview 等）是否一并强制鉴权 | VPS 整站保护更稳妥；与 07-09「暂不加」决策冲突时以 **P2.5 执行面** 为准：至少 ops+POST 必鉴权 | Phase 0 |
| D3 | 模型 promote 模式：git 指针 PR vs 仅本地 ACTIVE 文件 | git 指针 PR | Phase 3 |
| D4 | 告警通道：TG / webhook / 仅看板 | 先「仅看板」+ 复用 TG（若已有 `tg_config`） | Phase 4 |
| D5 | Mac 是否默认开启 executor | 默认关，owner 开 shell 别名或 env 文件开启 | Phase 2 |
| D6 | `forward_track` 是否允许 UI 改 `--start` | 默认不允许（防污染正式窗口） | Phase 2 |
| D7 | markdown 渲染是否允许新依赖 | 零新依赖优先 | Phase 1 |
| D8 | 模拟盘 / 实盘 API | **不在 P2.5**；属 P3 | — |
| D9 | VPS 是否永远禁止 executor | 是 | Phase 2 |
| D10 | sessionStorage 存 token 是否接受 | 可接受；否则每次粘贴 | Phase 0 UI |

**已澄清的非决策**：demo trading key → P3；YOLO/auto_label → 不进操作台；holdout → 无 UI。

---

## 10. PR 级实现计划（小步顺序）

> 每 PR 可独立 review / 回滚；提交信息英文；汇报中文。  
> 工作目录纪律：按 `NEXT_STEPS` 在 codex worktree 开发则从其规定；本设计文件可在 main 仓 `docs/` 落地。

| PR | 标题（建议） | 内容 | 依赖 |
|----|--------------|------|------|
| **PR-0a** | `docs: P2.5 ops console design` | 本文档入库 | 无 |
| **PR-0b** | `feat(webapp): ops auth middleware + flags` | `auth.py`、`ops_flags.py`；env 契约；401/403；单测 mock token；**尚无业务 POST** | Owner 可后补真实 secret |
| **PR-0c** | `docs: VPS auth runbook (no secrets)` | nginx/htpasswd 或 systemd env 文件步骤；检查清单 | D1 |
| **PR-1a** | `feat(webapp): experiment registry API` | 扫描 JSON + 列表/详情 API + 单测夹具 | PR-0b 可选（可先挂 `/api/ops` 并强制 auth） |
| **PR-1b** | `feat(webapp): experiments tab UI` | 表 + 详情；DESIGN 组件 | PR-1a |
| **PR-1c** | `feat(webapp): research agenda tab` | 读 RESEARCH_AGENDA.md | PR-1a |
| **PR-2a** | `feat(webapp): job whitelist + sqlite store` | 无真实 subprocess 或 dry-run 模式；单测 argv 不变式 | PR-0b |
| **PR-2b** | `feat(webapp): job runner subprocess + logs` | Popen、超时、cancel、SSE/轮询 | PR-2a；`ENABLE_JOB_EXECUTOR` |
| **PR-2c** | `feat(webapp): jobs tab UI` | 表单、确认、日志、VPS 禁用横幅 | PR-2b |
| **PR-2d** | `test: job runner integration smoke` | 对 `job-types` 元数据与假命令夹具（不击网） | PR-2b |
| **PR-3a** | `feat(webapp): data hub read-only` | 覆盖/审计/forward 健康 | PR-1a |
| **PR-3b** | `feat(webapp): model hub + promote pointer` | 列表、校验、ACTIVE；无 threshold 写 | D3 |
| **PR-3c** | `feat(webapp): config read-only + change request artifact` | diff/变更单落 `output/ops_change_requests/` | PR-3b |
| **PR-4a** | `feat(webapp): forward alert rules` | 规则 + API + 前向页横幅 | 既有 forward |
| **PR-4b** | `feat(webapp): alert channel hook` | 接 notify/webhook；缺省 no-op | D4 |

**合并门禁**：每 PR 跑现有 pytest + 新增 ops 单测；不把 torch 拉进 CI。

---

## 11. 测试策略

| 层级 | 覆盖 |
|------|------|
| 单元 | token 比对（用 env fixture）、flag 解析、JSON 指标抽取、whitelist `build_argv`、状态机迁移 |
| 契约 | 未知 job_type → 400；executor off → 403；bad token → 401 |
| 夹具 | 最小 `analysis/output/*.json` 多样 schema；假 log 文件 |
| 手工 | Mac 开 executor 跑 `update_okx` dry 路径；VPS 确认无执行按钮或按钮 disabled |
| 禁止 | 测试中不写真实 TG token；不打公网 OKX 作为 CI 门（可用 mock） |

---

## 12. Subagent 可离线实现 vs 必须等 Owner 密钥

### 12.1 Subagent / Codex 可离线完成（无 secret）

- 本文档与 runbook（无真实密码）。
- Auth middleware 代码路径 + 单测（fixture 内临时 token）。
- 实验注册表扫描、议程读取、前端只读 tab。
- Job whitelist 数据结构、`build_argv`、sqlite schema、状态机、假 subprocess 测试。
- 数据/模型只读 hub UI + promote 写 ACTIVE 的逻辑（不访问 VPS）。
- 告警规则纯函数 + 夹具。
- CI 可跑的全部测试。

### 12.2 必须 Owner 到场 / 提供 secret 的事项

| 事项 | 原因 |
|------|------|
| 生成并设置 `OPS_API_TOKEN` 或 htpasswd | 凭证所有权 |
| VPS systemd/nginx 注入环境变量与 reload | 生产权限与 SSH |
| 决定 D1–D10 | 产品/风险偏好 |
| 开启 Mac `ENABLE_JOB_EXECUTOR=1` | 本地资源与误操作责任 |
| Telegram/webhook 密钥与 chat_id | 已有 notify 纪律 |
| 确认 promote 是否 commit `models/ACTIVE` | git 工作流 |
| P3 demo API key | **不属于 P2.5** |
| 首次公网验收「攻击面」走查 | 真人浏览 + 未授权请求探测 |

### 12.3 协作节奏建议

1. Subagent 连续落地 PR-0b → PR-1c（只读面），owner 仅 review diff。  
2. Owner 完成 D1 + VPS secret → 合并后 deploy，验证 401。  
3. 再合 PR-2*；owner 在 Mac 开 executor 做第一次真实 `update_okx` 或 `forward_track`。  
4. Phase 3–4 可与研究实验并行，但不阻塞 P1.5 假设队列。

---

## 13. 风险与诚实声明

- **攻击面扩大**：任何执行面都比纯只读危险；白名单与默认关 executor 是必要但不充分条件——鉴权配错仍等于裸奔。
- **JSON schema 漂移**：注册表抽取是 best-effort；不会自动「修」实验数字，错误展示应宁可空白。
- **VPS 数据子集**：热力/指纹在 VPS 上可能 `unverifiable`；属部署现实，不是 bug 粉饰。
- **任务副作用**：`update_okx` / `deploy_self` 有真实外部效应；UI 必须二次确认且日志可审计。
- **与 P2-10 决策张力**：owner 曾「暂不加访问控制」；P2.5 执行按钮改变了风险模型——**执行面上线强制重新引入鉴权**，只读旧页是否保护由 D2 定。
- **本文不保证**：实现时点、告警阈值最优、或操作台替代 CLI 的 100% 覆盖。

---

## 14. 成功标准（P2.5 整体）

1. VPS 在无 token/无 basic-auth 时无法调用任何 job 创建接口。  
2. VPS `ENABLE_JOB_EXECUTOR=0` 下无进程被看板拉起。  
3. Mac 可在白名单内一键跑通至少：`update_okx`、`forward_track`，日志可见。  
4. 实验 tab 能对比近期 `analysis/output` 指标；议程 tab 与 md 同步。  
5. 模型 promote 不提供阈值编辑；配置变更有 diff/变更单无直写。  
6. 前向异常在看板上可见；通道按 owner 选择工作或安全 no-op。  
7. 无 holdout 评估入口；无 YOLO/auto_label 执行入口；无自由 shell。

---

## 15. 参考路径

| 资产 | 路径 |
|------|------|
| 本设计 | `docs/P2_5_OPS_CONSOLE_DESIGN.md` |
| 计划原文 | `docs/archive/NEXT_STEPS.md` §P2.5(已归档) |
| 设计系统 | `DESIGN.md` |
| 架构/部署 | `docs/ARCHITECTURE.md` |
| 议程 | `docs/RESEARCH_AGENDA.md` |
| 现看板入口 | `src/webapp/server.py` |
| 部署脚本 | `scripts/deploy_vps.sh` |
| TG 通知 | `src/notify.py` |
| 冻结模型 | `models/frozen_*.json`、`src/judgment/frozen.py` |
| 实验产物 | `analysis/output/*.json` |

---

*文档版本：2026-07-10 · 设计 only · 实现按 §10 PR 序列推进。*
