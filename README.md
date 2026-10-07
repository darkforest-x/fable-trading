# fable-trading

个人交易与量化研究的唯一仓库。当前主要研究假设：

> **K 线多均线"密集后启动"形态，在启动初期可被识别，且其中一小部分在扣除成本后可交易。**

模型路线是 YOLO 检测"长得像的"（L1）→ LightGBM 排序"值得进的"（L2）→ 回测（L3）→ 执行（L4）。
模型换过很多版，一直没换的是那套防自欺的实验纪律——**纪律才是这个项目真正的资产**。

本 README 只讲不随进度变化的东西：是什么、怎么组织、守什么规矩、怎么跑。

| 想知道 | 看哪里 |
|---|---|
| 现在进行到哪、在等什么、下一条允许的动作 | [`HANDOFF.md`](HANDOFF.md) 顶部（**唯一实时状态**） |
| 铁律与实盘纪律 | [`CLAUDE.md`](CLAUDE.md)（与 [`AGENTS.md`](AGENTS.md) 同步） |
| 阶段与门（P0–P5） | [`ROADMAP.md`](ROADMAP.md) |
| 东西放哪、一轮实验怎么走 | [`docs/PROJECT_CHARTER.md`](docs/PROJECT_CHARTER.md) |
| 全部文档索引 | [`docs/DOC_MAP.md`](docs/DOC_MAP.md) |
| 交易知识库（假设、事实、复盘） | [Notion · Spike 交易系统与研究中枢](https://app.notion.com/p/3d98856479af80d9ad43f622dd7c3efd) |

## 当前所处位置

- **阶段：P0（形态定义与重复标注稳定性）→ P1（Gold Dataset）**。两阶段通过前，新训练与 promote 都要 owner 明确授权。
- **生产上跑着 0 个模型**：`models/active_bundle.json` 不存在，`require_active_bundle()` fail-closed，
  实盘检测器诚实空转（`detector=none`）。代码存在不等于阶段开始。
- **YOLO 现在是 Pattern Teacher，不是生产触发器**：它产出的是 proposal，不是 signal，
  进不了下单路径（`yoyo/contracts/candidates.py`）。
- 所有模型与产物的 `training_eligible` / `production_eligible` 默认 false，改动需 owner。

## 体系结构

业务分三条并行线：**个人交易系统、策略研究、自动策略验证**。个人交易不依赖自动策略先通过，
人工判断同样要前向记录与复盘。

### 研究平台六层

| 层 | 职责 | 代码 |
|---|---|---|
| 数据 | 行情、图像、标签、事件谱系 | `yoyo/data/`、`yoyo/datasets/` |
| 特征与标签 | 因子定义、方向语义、时间边界 | L2 feature / label 模块 |
| 模型 | YOLO 形态候选、VLM 结构化观察、LightGBM 数值判断 | `yoyo/layers/l1_detection/`、`l2_judgment/`、`yoyo/vision_research/` |
| 策略 | 能力选择、入场退出、运行适配 | `yoyo/research_workspace/` |
| 评估与回测 | 时间切分、原成本、匹配对照、经济门 | `yoyo/evaluation/`、`yoyo/layers/l3_backtest/` |
| 前向运行 | 观察时点、模拟成交、信号观察 | `yoyo/monitor/`、`yoyo/layers/l4_execution/` |

### 四层 + 契约（硬约束）

```
                yoyo/contracts/   ← 所有层都可以 import
                yoyo/data/        ← 所有研究层都可以 import
                      ↑
L1 detection → L2 judgment → L3 backtest → L4 execution
       （层与层之间禁止互相 import，只能经 contracts / data）
```

由 `tests/boundaries/test_layer_imports.py` 用 AST 强制。起因是 2026-08-03 一个故障横跨
扫描、冻结判断和执行器三个文件：L2 的事实被 L1 的事实决定了，而代码里没有任何东西反对。

关键契约：

| 模块 | 语义 |
|---|---|
| `costs.py` | 成本路由表（owner 决策值，默认 0.2% 往返） |
| `outcomes.py` | 障碍/出场解算的唯一实现 |
| `pattern.py` | PatternEvent：`visible_end_at <= decision_at`；proposal 不是 gold |
| `candidates.py` | CandidateProposal：proposal 不是 signal |
| `protocol.py` | ACTIVE bundle 加载与 fail-closed 闸门 |

### 运行拓扑

```
Mac（开发 / 打标 / 评估 / 决策）       VPS（K 线与 forward_log 唯一写者）
├─ 统一工作台 127.0.0.1:8766          ├─ 看板 :8642
│  （yoyo.monitor，LaunchAgent）       ├─ fable-forward.timer（15m 脉冲）
├─ Discord 跟单 127.0.0.1:8080        │
│  （yoyo.copier，.venv-copier）       │
├─ Label Studio :8081                 └─ 执行器（真金，仅 owner 授权）
└─ git push → GitHub
局域网 RTX 3060：YOLO 训练（scripts/train_on_3060.sh）
```

## 纪律

完整条文在 [`CLAUDE.md`](CLAUDE.md)，违反即返工。最常被问到的几条：

1. **成功标准不是 AUC**：top-decile 扣 0.2% 往返成本后净收益 > 0，置换检验 p < 0.01，
   且**跑赢匹配随机对照**（同币 × 同时间块 × 同波动桶）。三项缺一即否，由
   `yoyo/evaluation/economic_gates.py` 执行。
2. **时间切分 + 无前视**：禁止随机切分；特征只用信号 bar 及之前的数据。
   决定能看见多少未来的是**窗口右端落在哪根**，不是窗口多长。
3. **单变量**：一次实验只改一个变量，成败都如实汇报。
4. **先提交 builder，再生成产物**：产物早于生成器入库 = 复现声明未经验证。
5. **YOLO 增强全关**：fliplr / flipud / mosaic / mixup / hsv 破坏时间方向和红绿语义。
6. **识别验收 ≠ 交易验收**：自家 val / mAP 不能替代真实 tip 金标与 tip-smoke；识别到形态不等于能赚钱。
7. **实盘门由 owner 把守**：新鲜度三门同值（30min）、脉冲 < 15min、不自动 promote、
   不清 forward_log、真金操作只由 owner 亲手做或逐次授权。
8. **单仓单分支**：只有 `main`，不开分支、不建 worktree、不开新仓；新研究进
   `experiments/active/<experiment_id>/` 并注册到 `experiments/registry.yaml`。
9. **非平凡问题解决后写 `docs/learnings/`**，用 `extract-approach` skill。

历史 holdout 纪律已于 2026-09-19 由 owner 取消，所有历史数据可直接用于研究；旧报告里的
"holdout 消耗 N 次"只是当时的记录。

## 快速上手

需要 Python 3.9（与 CI 一致）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -c constraints-ci.txt
git config core.hooksPath scripts/hooks   # CLAUDE.md 与 AGENTS.md 同步检查
```

`torch` / `ultralytics` / `numpy` / `pandas` 是跨机契约：Mac、3060、CI 必须同版本，锁在
`constraints-ci.txt`。cleanlab 一类评估工具装进独立 venv（`requirements-eval.txt`），
装新库前先 `pip install --dry-run --report -` 看会不会降级。

```bash
# 测试（CI 跑的就是这个；守门测试红了先读它在说什么）
.venv/bin/python -m pytest tests -q

# 拉 OKX 15m K 线（断点续传，自动跳过已完成币种；生产 K 线只在 VPS 写）
PYTHONPATH=. .venv/bin/python -m src.data.fetch_okx

# 统一工作台（http://127.0.0.1:8766）
.venv/bin/python -m yoyo.monitor.manage install   # 或 status / restart / stop

# 部署 VPS 看板（不推 data/kline_fetched，执行器强制关闭）
bash scripts/deploy_vps.sh
```

`src/` 下是旧 import 的转发壳，仍可用；**新代码一律写进 `yoyo/`**。

## 仓库地图

| 路径 | 内容 |
|---|---|
| `yoyo/contracts/` | 跨层语义：成本、障碍、形态、候选、ACTIVE 协议 |
| `yoyo/data/`、`yoyo/datasets/` | 行情读取与指标；标注、金标、数据集构建 |
| `yoyo/layers/l1_detection/` … `l4_execution/` | 检测 / 判断 / 回测 / 执行四层 |
| `yoyo/evaluation/` | 切分、匹配对照、置换检验、经济门 |
| `yoyo/monitor/` | 信号中心、通知、统一工作台后端与前端（`static/`） |
| `yoyo/research_workspace/` | 研究组合、模型目录、策略库、个人交易工作区 |
| `yoyo/copier/` | Discord 跟单服务（真金；独立 venv，说明见其 README） |
| `src/` | 转发壳（迁移期并存，不再添加） |
| `scripts/` | 流水线与一次性实验脚本；跑过的实验脚本冻结不改 |
| `experiments/` | `registry.yaml` 入口；`active/` 进行中；`historical/` 四个归档仓的结论 |
| `artifacts/registry.yaml` | 产物登记与血统 |
| `analysis/` | 历史实验报告（索引 `analysis/INDEX.md`；只增不改结论） |
| `docs/learnings/` | 事故与反直觉结论（只增） |
| `docs/protocol/` | 研究规格，如 Local Signal V2 |
| `tests/` | 含 `boundaries/`、`causality/`、`parity/` 守门测试 |
| `data/`、`datasets/`、`runs/` | 数据与训练产物，**不入 git** |

## 报告与交付

默认直接在对话中给结论、关键证据和限制；只有 owner 明确要求时才生成报告文件。
需要的实验代码、原始结果和复现信息照常保留。改历史报告里的路径是禁止的——
`analysis/` 和 `docs/learnings/` 记录的是当时发生了什么，新旧路径对照查 `docs/RESTRUCTURE_MAP.md`。
