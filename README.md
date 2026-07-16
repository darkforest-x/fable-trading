# fable-trading

验证一个交易假设:**K 线多均线"密集后启动"形态,在启动初期可被视觉模型识别,且其中
一小部分在扣除成本后可交易**。两层架构——YOLO 检测"长得像的",LightGBM 回归排序
"值得进的"——外加一套防自欺的实验纪律。

> **实时状态只看一处:[`HANDOFF.md`](HANDOFF.md) 顶部"当前真相"区。**
> 本 README 讲不随进度变化的东西:动机、架构、纪律、怎么跑。

## 为什么会有这个项目(旧方案尸检)

前身(`yolo-yolo-okx-20-k`,只读参考)迭代 180+ 版本后确认失败,根因:

1. 所有正样本标注框固定在图像右缘——任务实为"右缘是否启动初期"的二分类,不是检测;
2. v176–v181 误开 fliplr/mosaic/hsv 增强,破坏时间方向与红绿 K 线语义;
3. 正样本仅 79–271 个,验证集指标全是噪声;2911 组回测参数搜索是过拟合发生器;
4. ETH 近一年回测 671 笔,-26.3%,胜率 34.9%,PF 0.47。

本项目从 P0(信号定义是否含 alpha)重做,每一步有验收标准,失败照样入库。

## 架构(2026-07 形态)

```
OKX 合约 15m K线(267 币种)
   │  src/data/fetch_okx.py(断点续传)
   ▼
渲染 200-bar 窗口(K线 + SMA/EMA 20/60/120)          src/detection/render.py
   ▼
[2a 检测层] YOLO11 —— 在项目所有者手工标注(~9500张)上训练
   │         权重: models/owner_best.pt(晋升制,泄漏审计,标杆体检门)
   ▼
[2b 判断层] LightGBM 回归 predicted_realized_ret      src/judgment/
   │         冻结工件 + val-q90 阈值,事前锁定          (frozen.py 是唯一咽喉)
   ▼
TP5/SL2 三重障碍出场 → 前向验证(100 笔硬闸) → 看板 / TG 信号
```

- **看板**: http://103.214.174.58:8642(部署 `bash scripts/deploy_vps.sh`)
- **训练**: 一律在局域网 RTX 3060(比 Mac MPS 快 ~7 倍),Mac 只做数据/评估/决策;
  见 `scripts/train_on_3060.sh`
- **打标**: Label Studio :8081,轮次制;round8 起生成器保证窗口零重叠、排除冻结评估币种

## 纪律(为什么这个项目还没自欺)

细则在 [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md),不可协商的几条:

1. **holdout(≥2026-05-04)每次动用需项目所有者批准并记账**(已消耗 3 次,均有记录)
2. **成功标准是 top-decile 扣成本净收益 + 置换检验 p<0.01**,AUC 只是参考量
3. **冻结评估尺子是清单不是规则**:`datasets/owner_eval_frozen/MANIFEST.json`
   (47 币种从未参训);训过尺子币种的模型会被晋升门自动拒绝
4. **每轮实验交付 `analysis/pXX_report.md`**,必含复现命令与"风险与诚实声明"
5. **每个非平凡问题解决后写 `docs/learnings/` 笔记**(现有 15+ 篇,含
   "optimizer=auto 炸掉所有续训"、"nice 不隔离 GPU"等真实事故)

## 快速上手

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 数据(断点续传,~1h)
PYTHONPATH=. .venv/bin/python -m src.data.fetch_okx

# 判断层训练/评估(不带 --eval-holdout 即安全)
PYTHONPATH=. .venv/bin/python -m src.judgment.train --data data/judgment_yolo_swap_v8.csv

# 阶段3回测(accept 窗口是 holdout,跑之前先读 CLAUDE.md 铁律 1)
PYTHONPATH=. .venv/bin/python -m src.backtest.run --frozen-config default \
    --data data/judgment_yolo_swap_v8.csv

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
| `src/costs.py` | 成本路由表(owner 管控,唯一来源) |
| `src/webapp/` | FastAPI 看板(总览/回测/前向/探索/ops) |
| `scripts/` | 流水线与实验脚本;**跑过的实验脚本冻结不改**(保复现) |
| `analysis/` | 每轮实验报告(p0 → p3),结论以此为准 |
| `docs/learnings/` | 事故与反直觉结论笔记 |
| `docs/archive/` | 已被取代的历史文档(只增不删) |
| `models/` | 冻结工件、ACTIVE 指针、owner_best 检测权重、yolo11* 冷启动基座 |

## 历史(一句话版)

规则族表达不了所有者的"可交易密集"概念(网格上限 F1≈0.45 vs 自洽 0.88)→ 转向
手工标注训练 YOLO → 2026-07-16 审计发现 `optimizer='auto'` 曾使所有续训失效,修复后
检测层 F1 0.65+,判断层切换到干净候选池(accept 窗口 PF 7.50/428 笔,**该数字仍待
前向 100 笔裁决,不构成收益宣称**)。完整履历:`git log` + `analysis/`。
