# darkforest-trading

验证一个交易假设:**K 线多均线"密集后启动"形态,在启动初期可被视觉模型识别,且其中
一小部分在扣除成本后可交易**。两层架构——YOLO 检测"长得像的",LightGBM 回归排序
"值得进的"——外加一套防自欺的实验纪律。

> **实时状态只看一处:[`HANDOFF.md`](HANDOFF.md) 顶部。**  
> 文档索引:[`docs/DOC_MAP.md`](docs/DOC_MAP.md)。  
> 本 README 讲不随进度变化的东西:动机、架构、纪律、怎么跑。

## 架构

```
OKX 合约 15m K线(400+ 币种,VPS 每 15 分钟增量,是 K 线唯一写者)
   │  src/data/fetch_okx.py(断点续传) / update_okx.py(脉冲内增量)
   ▼
渲染 200-bar 窗口(K线 + SMA/EMA 20/60/120)          src/detection/render.py
   ▼
[2a 检测层] YOLO11 —— 在项目所有者手工标注(~9500张)上训练
   │         权重: models/owner_best.pt(晋升制,泄漏审计,标杆体检门)
   │         live 扫描:tip+近端 6 窗,盘口 bar 当场入账(实时 tip 路径)
   ▼
[2b 判断层] LightGBM 回归 predicted_realized_ret      src/judgment/
   │         冻结工件 + val-q90 阈值,事前锁定          (frozen.py 是唯一咽喉)
   ▼
TP5/SL2 三重障碍出场 → 前向验证(100 笔新鲜裁决,事后检出剔除)
   ▼
[执行层] src/execution/ —— OKX 实盘(VPS systemd):市价入场 + OCO 括号
         + 72-bar 超时平仓;新鲜度三门 30min 一致(执行器/TG/裁决)
   ▼
看板 :8642 / TG 信号
```

- **看板**: http://103.214.174.58:8642(部署 `bash scripts/deploy_vps.sh`)
- **打标**: Label Studio :8081,轮次制;round8 起生成器保证窗口零重叠、排除冻结评估币种

## 纪律(为什么这个项目还没自欺)

细则在 [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md),不可协商的几条:

1. **holdout(≥2026-05-04)每次动用需项目所有者批准并记账**(消耗账本见 HANDOFF 顶部,当前 5 次)
2. **成功标准是 top-decile 扣成本净收益 + 置换检验 p<0.01**,AUC 只是参考量
3. **冻结评估尺子是清单不是规则**:`datasets/owner_eval_frozen/MANIFEST.json`
   (47 币种从未参训);训过尺子币种的模型会被晋升门自动拒绝
4. **每轮实验交付 `analysis/pXX_report.md`**,必含复现命令与"风险与诚实声明"
5. **每个非平凡问题解决后写 `docs/learnings/` 笔记**(40+ 篇,含
   "optimizer=auto 炸掉所有续训"、"新鲜度门必须从管道时序推导"、"tip 分布错位"等)

## 快速上手

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 数据(断点续传,~1h)
PYTHONPATH=. .venv/bin/python -m src.data.fetch_okx

# 判断层训练/评估(不带 --eval-holdout 即安全;当前主线池 v11)
PYTHONPATH=. .venv/bin/python -m src.judgment.train --data data/judgment_yolo_swap_v11.csv

# 阶段3回测(accept 窗口是 holdout,跑之前先读 CLAUDE.md 铁律 1)
PYTHONPATH=. .venv/bin/python -m src.backtest.run --frozen-config default \
    --data data/judgment_yolo_swap_v11.csv

# 检测层评估(冻结尺子) + 标杆体检
PYTHONPATH=. .venv/bin/python scripts/promote_owner_best.py
PYTHONPATH=. .venv/bin/python scripts/benchmark_check.py

# 本地看板
.venv/bin/uvicorn src.webapp.server:app --port 8642
```

## 仓库地图

| 路径 | 内容 |
|---|---|
| `src/detection/` | 渲染、YOLO 训练配方(含续训 lr 修复)、评估尺子(唯一实现) |
| `src/judgment/` | 候选→特征→三重障碍标签→LightGBM→冻结工件→前向 |
| `src/backtest/` | 阶段3 事件驱动模拟(成本扫描、并发上限、`--frozen-config`) |
| `src/execution/` | OKX 实盘执行器(市价+OCO+超时平仓;secrets 在 data/ 不入 git) |
| `src/short_tf/` | 1m/5m 规则 tip 支线(独立日志,不接主线 executor) |
| `src/costs.py` | 成本路由表(owner 管控,唯一来源) |
| `src/webapp/` | FastAPI 看板(总览/回测/前向/探索/ops) |
| `scripts/` | 流水线与实验脚本;**跑过的实验脚本冻结不改**(保复现) |
| `analysis/` | 每轮实验报告(p0 → p3),结论以此为准 |
| `docs/learnings/` | 事故与反直觉结论笔记 |
| `docs/archive/` | 已被取代的历史文档(只增不删) |
| `models/` | 冻结工件、ACTIVE 指针、owner_best 检测权重、yolo11* 冷启动基座 |

