# 项目问题总览 + 为什么 YOLO「优化」不生效

写于 2026-07-10（接续 Codex 离线任务 + 上轮「继续」）。

## A. 整个项目现在有哪些问题

按严重度排序（不是按热闹程度）：

### 1. 主线 edge 在真实成本下极薄（最高）

- 阶段 3 首轮：PF ≈ **1.01 @ 0.3%** 成本，未过 1.3。
- 换 TP5/SL2 + maker 后 val 好看，但 **val 已选型 ~30+ 次**，只能排序不能宣称。
- 接入真实资金费后：覆盖样本净@maker+funding ≈ **+0.003%/笔**，filled-only 甚至略负。
- **唯一硬闸门**：前向 ≥100 笔（目标 ~08-05）。正式窗口目前只有少量 closed（冒烟见 2 笔量级）。

### 2. 前向样本还太少（最高，时间问题）

- 基础设施齐：冻结模型、`forward_track`、看板前向 tab、每日 `update_okx → forward_track → daily_digest`。
- 缺的是 **日历时间**，不是再扫一遍 val。

### 3. 检测层标签质量封顶 mAP（中，且非关键路径）

- 正式验收：yolo11s **mAP50 0.8569 < 0.90** → 暂停。
- Round1 人工/代理审计：失败主要是 **框过宽 / 边缘残框 / 分裂合并**，不是「完全标错图」。
- 见 `yolo_label_audit_findings.csv` + `yolo_label_audit_recommendations.md`。

### 4. 两套均线语义不对齐（中，架构债）

- YOLO / auto_label：**SMA/EMA 20/60/120** 渲染与打标。
- 判断主线：owner 拍板 **EMA 8-55**（20/60/120 净收益更弱）。
- 结果：把 YOLO 做准，也只是对齐「规则可学的视觉形态」，**不是主线 alpha 的检测入口**。

### 5. 宇宙扩张与薄流动性（中，进行中）

- OKX 现货式「401 个 USDT-SWAP」≠ 可交易研究宇宙。
- 扩展拉取进行中（~190/401 文件量级）；股票/ETF 类 SWAP 是黑名单候选，**未**自动进 `BLOCKED_BASES`。

### 6. 工程分叉（低）

- `codex/day1` 与 `main` 并行，大量 Codex 工作尚未合并。
- 不阻塞前向，但增加交接成本。

### 7. 操作台 / 模拟盘（低，按计划未做）

- P2.5 需要鉴权硬前置（你已暂不加访问控制）。
- P3 需要 demo API key。

---

## B. 为什么给 YOLO 加的优化不生效

「优化」如果指：换更大模型、SAHI 切片推理、调 conf、堆增强、指望 mAP 冲过 0.90 —— **杠杆选错了**。

### 原因 1：标签是上限，模型是学标签的学生

YOLO 学的是 `auto_label.py` 画的绿框，不是你心里的「漂亮密集启动」。

Round1 证据：

- `PAXG_USDT_015960`：超宽框盖住长横盘 → GT 本身松。
- 边缘窗：`ICP` / `BNB_011660` 残框。
- 分裂：`ALLO` 一段密集被拆成两框。

在脏/松 GT 上换 yolo11s：smoke **0.835 → 0.857**，只涨 ~2 个点，说明 **容量不是瓶颈**。

### 原因 2：SAHI 已对照实验证伪

同 80 张、同权重、同 conf：

- Direct：匹配 **77/97**
- SAHI：匹配 **75/97**，预测框 106→**178**

切片推理增加了碎框/误报，没有抬 recall-like。再调 SAHI 属于重复无效杠杆（除非单变量 + direct baseline 再来一轮）。

### 原因 3：正确关掉了 CV 增强

图表有方向语义，flip/mosaic/mixup/hsv 按纪律关闭。  
这会让「通用 YOLO 炼丹手册」里很多涨 mAP 的技巧 **不能用** —— 不是没加，是 **不该加**。

### 原因 4：检测层已是非关键路径

主线宇宙是 **规则扫描 + LightGBM 判断 + 前向**。  
YOLO 冒烟通过后，正式线失败即暂停。继续堆检测优化 **不改变** 阶段 3 终审结果。

### 原因 5：优化目标错了

项目成功标准是 **扣成本后的 top-decile 净收益 / 前向 PF**，不是 mAP50。  
即使 mAP 到 0.92，若判断层 edge 仍只有几个 bp，交易问题依旧。

### 原因 6：均线栈不一致

优化 20/60/120 视觉检测，主线交易信号却是 8-55 规则池。  
两边同时「变好」也不会自动串联成一条更强 alpha 管线。

---

## C. 什么才会真正「生效」

| 目标 | 该做的事 | 不该做的事 |
|---|---|---|
| 阶段 3 终审 | 养前向日志到 ≥100 | 再扫 val / 偷看 holdout |
| 抬 mAP | 先修标签（E1 收 x_pad），owner 批后再训 | SAHI 直接进主路径；放宽 IoU 定义凑 0.90 |
| 抬交易 edge | H1 scaled 等发现级候选走前向挑战 | 用 val PF 2.8 直接替换冻结主线 |
| 数据宇宙 | 扩展后 audit → 过滤子集 | 401 全量塞进主线 |

---

## D. 本轮已落地的离线交付

| 交付 | 路径 |
|---|---|
| 打标 findings | `output/offline_tasks/yolo_label_audit_findings.csv` |
| 根因与实验序 | `output/offline_tasks/yolo_label_audit_recommendations.md` |
| SAHI/FiftyOne 可行性 | `output/offline_tasks/yolo_tooling_feasibility.md` |
| SWAP 扩展中期报告 | `output/offline_tasks/swap_universe_expansion_report.md`（INTERIM） |
| 抽图缓存 | `output/offline_tasks/label_audit_extract/` |
