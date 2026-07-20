# 系统架构（2026-07-20 刷新）

> **实时状态只看 [`HANDOFF.md`](../HANDOFF.md) 顶部。**  
> 下文描述**当前运行架构**；文末保留 07-09 / 07-16 历史图供对照。

一句话：**YOLO 检测候选 → LightGBM 回归排序 → 冻结阈值进前向 → VPS 执行器下单**；  
确认级只认 **100 笔新鲜前向**（事后/迟到检出剔除），不认 val / accept 再扫。

## 总览图（现行）

```
┌─────────────────────────── 数据层 ───────────────────────────┐
│ OKX 公共 API                                                 │
│  fetch_okx.py（全量/断点）  update_okx.py（脉冲内增量）         │
│  **VPS 是 K 线唯一写者**（本机不写 kline_fetched 上生产）        │
│  loader.py 合并去重 + BLOCKED / stockish 过滤                  │
└───────────────────────────┬─────────────────────────────────┘
                            │ 15m USDT-SWAP OHLCV（~344+ 币）
              ┌─────────────┴──────────────┐
              ▼                            ▼
┌── 2a 检测层（主线候选源）────┐  ┌── 规则扫描（回滚旁路）────┐
│ render 200-bar 窗            │  │ candidates.py EMA 8-55   │
│ SMA/EMA 20/60/120 画图       │  │ strict/expanded 预设     │
│ YOLO11 owner_best.pt         │  │ CANDIDATE_SOURCE=rules   │
│ live: tip + 近端 6 窗        │  │ 仅回滚/对照用             │
│ tip 盘口 bar 当场入账        │  └──────────────────────────┘
└──────────────┬───────────────┘
               ▼
┌── 2b 判断层 ───────────────────────────────────────────────┐
│ features 无前视 → LightGBM **回归** predicted_realized_ret   │
│ 冻结: frozen_tp5_sl2_swap_yolo_v11_reg_20260718              │
│ 阈值 val-q90（当前 ≈0.02022）；池 judgment_yolo_swap_v11     │
│ frozen.py::default_config() = 唯一咽喉                       │
└──────────────┬─────────────────────────────────────────────┘
               ▼
┌── 出场 / 前向 ─────────────────────────────────────────────┐
│ 主线出场 TP5/SL2 · horizon 72 · 72-bar 超时                 │
│ forward_track 脉冲（15m 收盘后对齐）→ data/forward_log.csv  │
│ 新鲜度三门 30min 同值（执行器 / TG / 看板 FRESH_DETECT_MIN） │
│ 裁决：100 笔 maker-filled closed · 事后检出不计入            │
└──────────────┬─────────────────────────────────────────────┘
               ▼
┌── 执行层（VPS）────────────┐    ┌── 观测 ──────────────────┐
│ src/execution/             │    │ webapp :8642 看板         │
│ fable-executor（市价+OCO）  │    │ TG 信号（仅 open+新鲜）   │
│ fable-forward.timer 15min  │    │ live_health 30min 告警    │
│ ENABLE_JOB_EXECUTOR=0      │    │ analysis/p*_report.md     │
└────────────────────────────┘    └──────────────────────────┘

旁路（不接主 executor）:
  · H1 scaled shadow 日志
  · H-TIP v12 训练中（owner_v12_htip）— 不自动 promote
  · src/short_tf/ 1m/5m 规则 tip
```

## 模块地图

| 路径 | 职责 | 关键约束 |
|---|---|---|
| `src/data/fetch_okx.py` | 全量历史 | 浏览器 UA；≤8 req/s；断点续传 |
| `src/data/update_okx.py` | 脉冲/日增量 | 幂等；VPS 主写 |
| `src/data/loader.py` | 合并去重 | BLOCKED；断链软链跳过 |
| `src/detection/*` | 渲染 / YOLO / owner 评估 | 增强全关；FINETUNE_OPT lr=1e-4；尺子 MANIFEST |
| `src/judgment/candidates.py` | 规则候选（旁路） | 阈值预设 owner 资产 |
| `src/judgment/labeling.py` | 障碍标签 | entry=次根开盘；无前视 |
| `src/judgment/features.py` | 特征 | 信号 bar 及之前 |
| `src/judgment/train.py` | 训练 | purge；holdout 仅 `--eval-holdout` |
| `src/judgment/frozen.py` | 冻结默认配置 | **唯一主线咽喉** |
| `src/judgment/forward.py` | 前向扫描/合并 | tip 实时路径；幂等键；shadow 隔离 |
| `src/execution/*` | 实盘下单 | 新鲜度门；ledger 防重；secrets 不入 git |
| `src/backtest/*` | accept/组合回测 | holdout 窗口消耗记账 |
| `src/costs.py` | 成本路由表 | owner 管控唯一来源 |
| `src/webapp/*` | 看板 | 只读产物；ops executor 默认关 |
| `src/short_tf/*` | 短周期支线 | 独立日志，不接主 executor |
| `scripts/deploy_vps.sh` | 部署 | 不推 data/kline；executor 强制 0 |
| `scripts/build_htip_dataset.py` | H-TIP tip 重渲克隆 | train-only；不自动 promote |
| `scripts/promote_owner_best.py` | 检测权重晋升 | 泄漏门 + 标杆门 |

## 均线定义（现行裁决）

| 层 | 均线 | 说明 |
|---|---|---|
| **检测渲染 / 视觉** | SMA/EMA **20/60/120** | 与 owner 打标图一致；live YOLO 主线候选源 |
| **规则扫描旁路** | EMA **8/13/21/34/55** +144/200 | 规则时代主线；现仅回滚 |
| **判断特征** | 特征表含 spread/order 等（由候选 bar 导出） | 不在候选源上再套一套「密级闸门」 |

历史「两层均线不一致」在 **YOLO 已是候选主源** 后变为：检测看 20/60/120 图，规则 8-55 只作旁路。  
P0-3 曾在合约上对比过 8-55 vs 20/60/120 的**判断经济性**；切 YOLO 主线后以 **owner 视觉一致性** 优先。

## 数据资产与产物

| 路径 | 入 git? | 内容 |
|---|---|---|
| `data/kline_fetched/` | 否 | 15m 序列；VPS 写 |
| `data/forward_log.csv` | 否 | 主线前向裁决账本 |
| `data/forward_log_*.csv` | 否 | shadow / 归档（禁混入 0/100） |
| `data/judgment_yolo_swap_v11.csv` 等 | 否 | 判断池 |
| `models/ACTIVE` + `frozen_*` + `owner_best.pt` | 部分 | 冻结与晋升指针 |
| `datasets/owner_eval_frozen/` | 部分 | 检测冻结尺子 MANIFEST |
| `analysis/p*_report.md` + `output/` | 是 | 实验结论 |
| `docs/learnings/` | 是 | 事故/反直觉 |
| `runs/` `datasets/dense_*` | 否 | YOLO 训练 |

## 部署拓扑（2026-07-20）

```
MacBook                              VPS (Debian)
├─ 开发 / 部分 YOLO 训练(v12)         ├─ /opt/fable-trading
├─ Label Studio / 打标                ├─ fable-dashboard :8642
├─ golden_pool / promote / 评测       ├─ fable-forward.timer（15m 脉冲）
└─ git push → GitHub                  ├─ fable-executor（live keys）
                                      ├─ K 线唯一写者 + forward_log 写者
                                      └─ ENABLE_JOB_EXECUTOR=0
可选: 局域网 3060 训 YOLO（见 memory/training-on-3060）
```

## 全局不变量

1. 时间切分 + purge；特征无前视  
2. holdout / accept 消耗次数记账（见 HANDOFF）  
3. 成功指标 = 扣成本净收益 + 显著性；确认级 = 前向 100 笔新鲜  
4. 实验加法优先；跑过的实验脚本冻结不改  
5. 结论进 report / learnings；没写 = 没做  
6. 实盘：新鲜度三门同值、脉冲 <15min、不自动 promote（见 `CLAUDE.md` 实盘纪律）

## 图解

- LightGBM 流水线：![](diagrams/lightgbm_pipeline.svg)  
- triple-barrier：![](diagrams/triple_barrier.svg)

---

## 历史附录 A — 2026-07-09 架构图（规则主线时代）

当时一句话是「规则扫描 → ML 排序 → 回测 → 前向」，YOLO 为旁路。  
总览 ASCII 与「检测 20/60/120 vs 判断 8-55」讨论以 git 历史为准；**已不代表现行主线**。

## 历史附录 B — 2026-07-16 现状补记（v8/v9 池时代）

```
K线 → YOLO owner_best → LGBM 回归 v8 池 → TP5/SL2 → forward / 看板
```

治理设施（仍有效）：

| 设施 | 位置 |
|---|---|
| 冻结尺子清单 | `datasets/owner_eval_frozen/MANIFEST.json` |
| eval 唯一实现 | `src/detection/owner_eval.py` |
| 续训 lr | `FINETUNE_OPT`（禁 optimizer=auto） |
| 标杆门 | `scripts/benchmark_check.py` |
| 成本表 | `src/costs.py` |
| promote 泄漏门 | `scripts/promote_owner_best.py` |

v11 切流（07-18）与实时 tip 路径（07-20）见 HANDOFF 顶部。
