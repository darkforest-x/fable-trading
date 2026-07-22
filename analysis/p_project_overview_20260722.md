# 项目总览（给 Owner）— 2026-07-22 夜

> **本文件角色**：一份可读的「整仓地图 + 当前真相 + 排队决策」。  
> **实时状态仍以** [`HANDOFF.md`](../HANDOFF.md) **顶部为准**；本文件是 07-22 夜快照，不替代 HANDOFF 滚动更新。  
> **纪律原文**：[`CLAUDE.md`](../CLAUDE.md) / [`AGENTS.md`](../AGENTS.md)（两文件同步）。

---

## 0. 写在前面：今晚一眼

| 项 | 状态 |
|---|---|
| **检测主线权重** | `models/owner_best.pt` = **v12 H-TIP**（未 promote v13/v14/v15） |
| **判断 ACTIVE** | `models/ACTIVE` → **`frozen_tp5_sl2_swap_yolo_v11_reg_20260718`**（v11 池，阈值 val-q90≈0.02022） |
| **tip-smoke（贴边开火）** | **v12 / v13 / v14 均为 0/27** |
| **v14** | 终局失败；根因已写；**勿再同构 pad200** |
| **v15** | **3060 仍在训**（`owner_v15_tipval_oomfix`，约 ep14/40，best 已有）；单变量=「val 也 pad200」；**未 promote** |
| **前向 100** | tip≈0 → 有效新鲜裁决几乎攒不起来；本机 `forward_log` 空表头；真账本在 VPS |
| **当前目标** | **盘口 tip 着火 → 才谈前向 100 确认级** |

**本夜 v15 状态命令**（2026-07-22 ~22:46）：`bash scripts/v15_train_status.sh`  
→ `running`；`14/40` ~25%；`best.pt` 时间戳约 22:35（Windows `C:\fable\...owner_v15_tipval_oomfix\`）。

---

## 1. 一句话：项目是什么

验证假设：**K 线多均线「密集后启动」形态，可被视觉模型在启动初期识别，且其中一小部分在扣除成本后可交易。**

做法不是「一个端到端黑盒」，而是两层 + 执行：

1. **2a 检测（YOLO）**——找「长得像密集启动」的框；  
2. **2b 判断（LightGBM 回归）**——在候选上排序「值得进」；  
3. **执行 + 前向**——VPS 实盘括号单 + **新鲜 100 笔**做唯一确认级裁决。

旧项目失败教训（固定框假检测、增强破坏时间方向、把 AUC 当成功）写进 README / learnings；本仓用晋升制、冻结尺子、时间切分、holdout 记账、前向时钟来防自欺。

---

## 2. 架构（2a / 2b / 执行 / 看板 / VPS）+ 关键目录

### 2.1 流水线（人话）

```
OKX 合约 15m K 线（400+ 币；VPS 每 15min 增量 = 唯一写者）
        │  src/data/fetch_okx.py · update_okx.py
        ▼
渲染 200-bar 窗（K + SMA/EMA 20/60/120）     src/detection/render.py
        ▼
[2a] YOLO11 检测「密集区」                     models/owner_best.pt（现 = v12）
        │  live：tip + 近端窗；TIP_EDGE_BARS=2 贴边入账
        ▼
[2b] LightGBM 回归 predicted_realized_ret     src/judgment/
        │  咽喉：frozen.py + ACTIVE 指针
        ▼
TP5/SL2 三重障碍出场 → 前向账本（新鲜度三门 30min）
        ▼
[执行] OKX 市价 + OCO + 72-bar 超时            src/execution/（VPS systemd）
        ▼
看板 :8642 + TG 信号                           src/webapp/
```

### 2.2 关键模块一句话

| 路径 | 一句话 |
|---|---|
| `src/detection/` | 渲染、YOLO 训练/评估、owner 冻结尺子与标杆门；**不**做涨跌判断 |
| `src/judgment/` | 候选→特征→三重障碍标签→LGBM→冻结工件→前向扫描；`frozen.py` 是生产咽喉 |
| `src/execution/` | OKX 实盘：市价入场、OCO 括号、超时平仓、KILL、tiered 仓位；secrets 在 `data/` |
| `src/webapp/` | FastAPI 看板（总览/回测/前向/探索/ops）+ 状态条新鲜度门 |
| `src/data/` | OKX 拉取/增量、universe、funding、bar 工具；VPS 写 K 线 |
| `src/backtest/` | 阶段 3 事件驱动回测（成本、并发、`--frozen-config`） |
| `src/short_tf/` | 1m/5m 规则 tip 支线；**不接**主线 executor |
| `src/costs.py` | 成本路由表（owner 管控唯一来源） |
| `scripts/` | 流水线入口；**跑过的实验脚本冻结不改**（保复现） |
| `analysis/` | 每轮实验报告 `p*_*.md`；结论以此为准 |
| `docs/learnings/` | 事故与反直觉笔记（80+；learning law） |
| `models/` | `owner_best`、各版权重、`ACTIVE`、frozen sidecar |
| `datasets/` | 检测 YOLO 数据集（v11/v12 htip/v13–v15 pad200…）；评估 MANIFEST |

### 2.3 关键脚本入口（Owner 常摸）

| 脚本 | 用途 |
|---|---|
| `scripts/train_owner_v12_htip.sh` 等 | 历史检测训练流水（v9–v14）；**v14 同构勿再开** |
| `scripts/train_owner_v15_tipval.sh` / `v15_train_start.sh` | v15 tipval（本机 MPS 备 / **3060 WMI 主路径**） |
| `scripts/v15_train_status.sh` | SSH 一眼：v15 是否还在训、log 尾、best.pt |
| `scripts/sync_v15_to_windows.sh` | 打包 tipval 数据到 `zzc@192.168.1.3`（`FABLE_3060_HOST`） |
| `scripts/eval_v1{2,3,4}_vs_v12_tip.sh` / `eval_v15_vs_v12_tip.sh` | tip_hit + tip-smoke 对照；**不 promote** |
| `scripts/diag_forward_detect_lag.py --tip-smoke` | 强制 tip 窗冒烟（发现级主指标之一） |
| `scripts/tip_detectability.py` | true_tip tip_hit（注意与 smoke 协议鸿沟） |
| `scripts/deploy_vps.sh` | 部署代码到 VPS（**不推** `data/kline_fetched`） |
| `scripts/forward_pulse.sh` | 15min 脉冲：update → discover → phase2 → 可选 executor |
| `scripts/webapp_start.sh` / `webapp_status.sh` | 本机看板 |
| `scripts/live_health.py` | 实盘健康 + TG 告警（VPS timer） |

### 2.4 VPS / 实盘骨架（已上线，非实验）

- 主机看板：`http://103.214.174.58:8642`  
- `fable-forward.timer`：每 15min（对齐收盘后 `:01/:16/:31/:46`）  
- `fable-executor`：live；权益约 ~92U；`max_concurrent=1`；tiered 口径①已上（基础仓位减半）  
- 新鲜度三门 **30min**（执行器 / TG / 看板）同值  
- A′ 贴边入账：`TIP_EDGE_BARS=2`（拦事后框；**不制造 tip**）  
- **不自动 promote**；`forward_log` 不清空（清账 = owner）

---

## 3. 铁律（各一句）

| 律 | 一句 |
|---|---|
| **holdout** | ≥2026-05-04 只在最终验收评；每次动用须对话明确批准并记账（当前已消耗 **5** 次；下次池 cutover 即 **#6**） |
| **时间切分 / 无前视** | 禁止随机切分；特征只用信号 bar 及之前；只有标签可看未来 |
| **成功标准** | top-decile 扣成本净收益为正 + 置换 p&lt;0.01；**AUC 只是参考**（v1 教训：AUC 0.59 照样亏） |
| **YOLO 增强** | fliplr/flipud/mosaic/mixup/hsv **全关**（毁时间方向与红绿语义） |
| **新鲜度三门** | max_signal_age / TG / 看板 **必须同值**；现 30min，改动附延迟预算表且三处同改 |
| **脉冲预算** | 整脉冲 &lt;15min；禁止往 forward 脉冲塞扫描窗/实验任务 |
| **VPS 唯一写者** | K 线与 `forward_log` 只在 VPS 写；deploy 不推 kline |
| **不自动 promote** | `owner_best` / `ACTIVE` / frozen 默认切换须 owner 点头 |
| **真金** | 下单/撤单/KILL/改仓/改 API key 仅 owner 亲手或逐次授权 |
| **单变量** | 一次实验只改一个变量；多变量打包须批准并记 PROJECT_PLAN |
| **learning law** | 非平凡解决后写 `docs/learnings/`；无笔记 = 工作未完成 |

发现级 vs 确认级：**val 赢 10 次不如前向确认 1 次**；确认级只认 **前向新鲜 100 笔**。

---

## 4. 当前进度（版本账 + 数字）

### 4.1 生产配置（ACTIVE / best）

| 层 | 指针 | 内容 |
|---|---|---|
| 检测 | `models/owner_best.pt` | **v12** `owner_v12_htip`（07-20 owner 强制切检测主线；回滚 `owner_best_pre_v12.pt`） |
| 判断 | `models/ACTIVE` | **`frozen_tp5_sl2_swap_yolo_v11_reg_20260718`** |
| 池 | 数据 | `judgment_yolo_swap_v11.csv`（~26653 候选 / 344 币） |
| 阈值 | sidecar | val-q90 ≈ **0.02022** |
| 出场 | 实盘 | **TP5 / SL2**；H1 scaled 仅发现级强，未切主线 |
| 仓位 | 实盘 | tiered：q90–95×1 / q95–99×1.5 / q99+×2；unit=(equity×lev)/2 |

**重要错位**：检测已是 v12，判断仍冻在 **v11 池**——同池重建 + accept = **holdout #6**，且 tip 未通时 ROI 低（见 backlog B1）。

### 4.2 检测实验线（v12 → v15）

| 版本 | 假设 | 终局数字 | 状态 |
|---|---|---|---|
| **v12 H-TIP** | 右缘 tip 克隆重渲 | tip_hit **0.925** / frozen-F1 **0.650**；tip-smoke **0/27** | ✅ 主线；**H-DET-7**：离线≠盘口 |
| **v13 pad200** | 框后无后文；MAD **关**（后证实错窗） | tip_hit **0.008**；smoke **0/27** | 🔴；权重 `owner_v13_pad200.pt` 仅存档 |
| **v14 pad200** | 同构 + MAD **开** 复验 | tip_hit **0.033**；smoke **0/27**；训图可贴右 | 🔴 H-DET-1；根因 `p_v14_failure_rootcause.md`；**勿再同构** |
| **v15 tipval** | **单变量**：train=复用 v14；**val 也 pad200**（修 early-stop 中段尺子）；holdout 样本跳过 | 构建：val_pad200=803 / skip=706；训中 `*_oomfix` | 🔵 **3060 训中**；训完再 eval；**默认不 promote** |

v15 在测的是根因排序里的 **B（中段 val early-stop）**——即使 val 对齐，根因报告仍把 **C（pad200 语义 ≠ 盘口 tip）** 排第一；v15 过线标准仍是 tip-smoke ≫ 0，不是 val mAP。

### 4.3 tip-smoke / tip_hit 对照（发现级主表）

| | v12 | v13 | v14 |
|---|---:|---:|---:|
| true_tip tip_hit (n=120, conf=0.3) | **0.925** | 0.008 | 0.033 |
| tip-smoke 贴边开火 | **0/27** | 0/27 | 0/27 |

v12 的「高 tip_hit」测的是：**已知密集金标裁成 tip 几何后还认不认**；  
smoke 测的是：**账本币当前盘口 tip 有没有贴边开火**。二者不是同一件事（H-DET-7 🟢）。

### 4.4 判断层 / accept（历史，勿当实盘）

- v11 池 accept（holdout **第 5** 次，owner 批准）@0.3% 成本：  
  **703 笔 · 净资金 +245.8% · PF 6.61 · 胜率 77.1% · maxDD 0.76%**  
- **诚实折扣**：tip 可检子集相对全量净 ≈ **0.0465**（约 20× 高估风险）；通 tip 前别用 accept PF 当实盘预期。

### 4.5 前向

- 07-19 起时钟重启；确认级目标 **100 笔新鲜**（lag 在三门内）。  
- **阻塞**：检测 tip_fire≈0 → 新鲜 open≈0 → 裁决攒不动。  
- 本机仓库 `data/forward_log.csv` 仅表头；**真账本在 VPS**（勿本机覆盖）。

### 4.6 旁路（07-22，不抢 tip GPU）

已落地：LWC hardneg 批量、叠框画廊、LS 小包、Protections 规格、本机工具集（nvitop/netron/mitm/marimo…）、前端前向 Tabulator + 状态条。  
见 `p_overnight_20260722.md`、`p_wuzao_*`、`p_frontend_viz_opt.md`、`p_side_tools_landed.md`。

---

## 5. 当前目标

**主目标（串行瓶颈）**

1. **让盘口 tip 真正着火**（发现级：强制 tip 窗 + `TIP_EDGE_BARS=2` 后贴边开火率明显 &gt;0；相对 v12 的 0/27）。  
2. **再积累前向新鲜 100 笔**（确认级；不调门、不清账、不自动切池）。

**副线（可并行，不抢 3060 / 不改 LIVE）**

- v15 训完 → `eval_v15_vs_v12_tip.sh`（裁决 tip-smoke / tip_hit；**禁止**自动 promote）。  
- H-DET-4 渲染消融（GPU 空闲）。  
- 真实 tip 成败金标小样（**需 owner 点头**扩采/开训）。  
- 实盘运维：live_health、脉冲耗时、tiered 观察（样本极稀）。

**不是当前目标**

- 用 val mAP / accept PF「证明 tip 好了」。  
- 同构再训一轮 pad200。  
- 判断层大特征包 / holdout#6 池重建（等 tip 或明确批准）。  
- VPS 装 Kuma/Grafana（清单已写，**未批未装**）。

---

## 6. 已知 bug / 坑（带证据指针）

### 6.1 检测 / tip（主战场）

| 坑 | 现象 | 指针 |
|---|---|---|
| **stem / MAD 错窗（v13）** | 关 MAD 盲 `end_incl` → ~31% okx 错窗；v14 MAD-on 后错窗≈0，但 tip 仍挂 | `p_pad200_cut_audit.md`、`p_v14_pad200_rebuild.md` |
| **MAD≈0 ≠ tip 通** | 训推像素自洽（重渲 MAD=0），训图可贴右，smoke 仍 0 | `p_v14_failure_rootcause.md` §2 |
| **val 口径陷阱** | pad200 train 全贴右 + **中段 val** early-stop → catastrophic forgetting（0.925→0.033） | 同根因 §3；v15 单变量修 val |
| **tip 协议鸿沟（H-DET-7）** | tip_hit 高 ≠ tip-smoke / tip_fire | `p_v12_htip_eval.md`、`p_tip_only_smoke.md` |
| **MA 顺序差（H-DET-4）** | true_tip=切窗后 MA；live/pad200=全序列再切；v12 在 full-MA tip 小样 0/8 | 根因 §2 A/B 表 |
| **语义差 C（主因）** | 金标 crop-after-box ≠「当前 tip 正在启动」 | 根因 §5；下一步=真实 tip 金标 |
| **tip-only / 降 conf 当解药** | 已证伪，仍 0/27 | H-DET-5/6 |
| **A′ 贴边门** | 能挡事后账，**不能**从零创造 tip | H-DET-8 |

### 6.2 训练基建

| 坑 | 现象 | 指针 |
|---|---|---|
| **3060 OOM（v15）** | epoch-end val `ap_per_class` 把 16GB RAM 打爆；已用 `oomfix`：workers4→2、batch16→8、max_det=100 | `docs/learnings/yolo-val-ap-per-class-oom-on-16gb.md` |
| **SSH IP 漂移** | 默认 `.5` 可 ARP 死；现常落到 **`192.168.1.3`**；用 `FABLE_3060_HOST` | `docs/learnings/3060-lan-ip-can-drift-from-dot5.md` |
| **长训必须 WMI** | 纯 SSH 跑训会随断线杀进程 | `p_v14_windows_train.md`、`train_on_3060` 族 |
| **16GB Mac jetsam** | pad200 / tipval 构建需 `--resume` + watchdog | `pad200-mad-bulk-needs-resume-watchdog-on-16gb.md` |
| **optimizer=auto 续训** | 旧坑：lr=0.002 炸掉 chain；已修 FINETUNE_OPT lr=1e-4 | `p2a_lr_bug_audit.md` |

### 6.3 判断 / 执行 / 运维

| 坑 | 现象 | 指针 |
|---|---|---|
| **把 AUC / accept PF 当实盘** | tip 子集折扣 ~0.05；确认级只认前向 100 | `p_tip_subset_val.md`、CLAUDE 弱模型错 |
| **isotonic→仓位** | 分被压成台阶，阈值附近弃单；已证伪 | `p_weight_centric_val.md` |
| **改一道新鲜度忘另两道** | 曾用 20min 结构性挡死一切 | freshness-gates learning |
| **脉冲塞实验** | &gt;15min 节拍 = 挡 tip | 实盘纪律 8 |
| **检测换权重、判断未同池** | v12 检测 + v11 frozen；真同池需 holdout#6 | backlog B1 |
| **本机 forward_log 空** | 勿当「没交易」；看 VPS | HANDOFF / 纪律 9 |
| **q99+ tiered 样本少** | val ~41 笔；满档冲击大 | HANDOFF 07-21 |

---

## 7. 优化与下一步排队（标注要你批的）

### 7.1 检测主线（优先）

| # | 项 | 何时 | 要你批？ |
|---|---|---|---|
| 1 | **等 v15 训完 → tip-smoke / tip_hit 对照** | 进行中 | 否（跑 eval）；**promote 要批** |
| 2 | **真实 tip 成败金标小样**（替换 pad200 语义） | 根因唯一建议 | **是**（扩采规模 / 是否开训） |
| 3 | H-DET-4 / EXT-5 渲染消融（极小样） | GPU 空闲 | 否（只改评测/渲染消融协议） |
| 4 | H-DET-2 硬负中段簇加训 | 清单已备；勿抢 v15 | **是**（开训时机/样本量） |
| 5 | 再同构 pad200 | — | **禁止** |
| 6 | v12→v13/v14/v15 切检测主线 | tip-smoke 明显过线后 | **是**（永不自动） |

计划指针：`p_v13_real_tip_collect_plan.md` · `RESEARCH_AGENDA_DETECT.md` 优先队列。

### 7.2 判断 / 执行（等 tip 通）

| # | 项 | 要你批？ |
|---|---|---|
| v12 池重建 + 重冻 + accept（holdout **#6**） | **是** |
| 改阈值 / TP·SL / 成本假设 / 成功标准叙事 | **是** |
| tiered 档位/公式/提杠杆/充值 | **是** |
| Protections 日损%/连亏 N 上线阈值 | **是**（规格已有，默认未改） |
| H1/H3 shadow 确认、H16 放量入场 | tip 通后；切主线再批 |
| isotonic 再试 | **否决**（同构已死） |

### 7.3 旁路 / 运维（可并行）

| # | 项 | 要你批？ |
|---|---|---|
| VPS 装 Kuma / Grafana / exporter / Loki… | **是**（`docs/ops/VPS_OBSERVABILITY_PENDING.md`） |
| 看板 HTTPS / SSH 加固改机 | **是** |
| 本机 LWC/叠框/LS/工具深化 | 否（已做一批；继续不抢 GPU） |
| ONNX/OpenVINO 压 discover_wall | tip 通且仍慢后再议 |
| 清 forward_log / 重置前向时钟 | **是** |

### 7.4 Owner 个人卫生（周计划遗留）

- OKX API key 曾暴露：轮换 + 关提币 + IP 白名单（周计划仍标未确认）。  
- 核对是否还有无保护仓位残留。

---

## 8. 文档地图（读哪些）

### 8.1 每次接手必读（短）

1. [`HANDOFF.md`](../HANDOFF.md) 顶部「当前真相」  
2. [`CLAUDE.md`](../CLAUDE.md) 铁律 + 实盘纪律  
3. 本文件或当周 [`week_plan_20260720.md`](week_plan_20260720.md)

### 8.2 架构与议程

| 文件 | 读什么 |
|---|---|
| [`README.md`](../README.md) | 动机、架构图、怎么跑 |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | 模块级现行图 |
| [`docs/DOC_MAP.md`](../docs/DOC_MAP.md) | 活文档索引 |
| [`docs/RESEARCH_AGENDA.md`](../docs/RESEARCH_AGENDA.md) | H1–H19 + H-TIP + 旁路 H-FE/H-TOOL；优先队列 |
| [`docs/RESEARCH_AGENDA_DETECT.md`](../docs/RESEARCH_AGENDA_DETECT.md) | H-DET 子簇（tip/pad200/渲染/外源） |
| [`PROJECT_PLAN.md`](../PROJECT_PLAN.md) | 历史三阶段路线（顶注：已进实盘） |

### 8.3 本周关键实验报告

| 文件 | 内容 |
|---|---|
| `analysis/p_v14_failure_rootcause.md` | **必读**：tip 失败归因 C&gt;B&gt;A |
| `analysis/p_v14_pad200_train.md` | v14 终局数字 |
| `analysis/p_v12_htip_eval.md` / `p2a_v12_mainline_cutover.md` | v12 过线与切主线 |
| `analysis/p_tip_only_smoke.md` / `p_box_to_bar_lag.md` | tip-only 证伪、贴边门 |
| `analysis/p_tip_subset_val.md` | tip 子集折扣 |
| `analysis/backlog_future_optimizations.md` | 「现在不做」积木全表 |
| `analysis/p_overnight_20260722.md` | 夜间旁路纪要 |
| `analysis/p_wuzao_more_useful.md` / `p_wuzao_topics_scan.md` | 外源工具整仓口径 |
| `analysis/p_v13_real_tip_collect_plan.md` | 下一步金标采集计划 |

### 8.4 运维 / 工具

| 文件 | 内容 |
|---|---|
| `docs/LOCAL_DEBUG_TOOLS.md` | nvitop/netron/叠框命令 |
| `docs/EXEC_PROTECTIONS_SPEC.md` | 熔断规格（未改默认） |
| `docs/ops/VPS_OBSERVABILITY_PENDING.md` | VPS 装机待批 |
| `docs/learnings/*` | 事故蒸馏（OOM、IP 漂移、新鲜度门、isotonic…） |

### 8.5 不要做的文档维护

- 不要平行维护第二份「当前状态」（真相只在 HANDOFF 顶部）。  
- 不要改旧 `p*_report` 结论数字去「对齐现状」。  
- 改纪律时 **CLAUDE.md 与 AGENTS.md 必须同改**。

---

## 9. 风险与诚实声明

- 本总览是 **2026-07-22 夜** 快照；v15 训中数字会变——以 `v15_train_status.sh` 与训完后的 eval 报告为准。  
- tip-smoke 历史对照多用 VPS 账本快照；本机 K 线可能落后，但 v12/v13/v14 同口径结论一致。  
- accept PF / val 漂亮数字 **系统性高估** tip 可交易子集；确认级只认前向新鲜。  
- 检测层训练图历史上缺乏严格时间切分（~2.5% 落在 accept 窗）是结构性弱点——再次强调前向 100。  
- **未**在本文件写作时 promote 任何权重、未耗 holdout、未清 forward_log、未改三门/阈值。

---

## 10. 浓缩决策卡（可贴聊天）

```
项目：YOLO 找密集启动 → LGBM 排序 → VPS 实盘；确认级=前向新鲜100
主线：owner_best=v12；ACTIVE=v11 frozen；tip-smoke 全家 0/27
失败：v13/v14 pad200 已结案；勿再同构；根因 C 语义>B val>A MA
进行中：v15 tipval（修 val 分布）3060 训中；默认不 promote
目标：tip 着火 → 攒前向100
下一步要批：真实 tip 金标扩采/开训；任何 promote；holdout#6；VPS 可观测装机
```

---

*写于 2026-07-22。维护：结论变更时先改 HANDOFF 顶部，再视需要补一节到本文件或新开 `p_project_overview_YYYYMMDD.md`。*
