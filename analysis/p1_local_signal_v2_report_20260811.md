# Local Signal V2 P1 局部因果窗口对照报告

**日期**：2026-08-11

**阶段**：P1 历史发现级对照
**直接裁决（已按后续密度审计修正）**：`B2_local_fixed_w30_causal` 只证明 P1 局部化历史发现假设；当前 operating density 失败。它不得 promote，P3 判断层在 P2 hard-negative mining 与连续 causal-tip 密度回放通过前阻断。holdout/独立 forward 未运行，`production_eligible=false`。

## 1. 结论先行

- **2026-08-11 后续密度审计纠正**：此前把 proposal-pool 的 3,880 个 L1 fire rows 写成“交易/开单”是错误的，它们不是订单；但 B2 也确实放得过宽。conf=0.35 命中 56/357 easy-negative endpoints（15.69%），并命中已预筛 v10 proposal pool 的 3,880/7,795 行（49.78%，88.27 fires/ledger-day）。连续市场与可执行订单数尚未测量。
- 高计数不是候选重复、edge 映射或数组/PNG 推理差异；把 conf 抬到0.45虽降到8.35 fires/日，但正例召回从73.46%塌到6.98%，不能靠阈值修。
- B2（30 根固定因果窗）在 conf=0.35 达到 Event Precision 81.93%、Recall 73.46%、F1 77.47%、FP/1000 bars 81.12，三道冻结门全部通过。
- C3（20–30 根 causal-right-range）也通过；其低误报工作点为 Precision 74.71%、Recall 70.95%、F1 72.78%、FP/1000=120.28。
- B2 相对 C3：Precision +7.23pp、Recall +2.51pp、F1 +4.69pp、FP/1000 降 32.56%、重复检测率降 95.05%。因此机器选择 B2。
- B1（24 根固定窗）失败。它能在 conf=0.10 得到 99.16% Recall，但 Precision 只有 35.43%、FP/1000=904.90；升到 conf=0.20 后 FP/1000 降到 222.38，Recall 同时塌到 36.31%，完整阈值网格中不存在合格工作点。
- A（冻结 200 根旧模型）在同一 causal endpoint 重渲染后的最大 Recall 只有 7.54%，无法建立同 Recall 相对门。候选结果产生前已冻结绝对发现门：Precision≥50%、Recall≥50%、FP/1000≤250。

## 2. 范围与纪律

本轮只回答：在相同事件、相同时间切分、相同训练配方和相同事件尺下，20–30 根严格因果局部输入是否优于冻结 200 根旧模型，以及 24、30、20–30 三种窗口策略哪一种有可用工作点。

- 未新增标签，未把盘口/新数据作为前置条件。
- 未读取 `>=2026-05-04` 的 holdout；本配置 holdout 消耗次数为 0。
- 未改 ACTIVE、未 promote、未部署、未触发交易或 forward_log 清账。
- HSV、flip、mosaic、mixup 全部为 0。
- dataset seed=20260807；三条新训练臂实际 training seed=0。seed 字段歧义已在 B2 结果产生前勘误，trainer/wrapper 已改为显式传递。

## 3. 实验矩阵

| 臂 | 输入 | 位置/因果 | 训练 | 负样本 | 角色 |
|---|---:|---|---|---|---|
| A | 200 | 同一 decision 截止重渲染；旧权重 | 不重训 | legacy | 冻结 baseline |
| B1 | 24 | fixed、visible_end=decision | yolo11s cold | easy 1:1 | 局部窗口长度对照 |
| B2 | 30 | fixed、visible_end=decision | yolo11s cold | easy 1:1 | 局部窗口长度对照 |
| C3 | 20–30 | causal-right-range、visible_end=decision | yolo11s cold | easy 1:1 | 检验是否不需要 future Stage A |

交接规范中的 C1/C2 future/random pretrain 没有运行，因为仓库铁律禁止新增只能产出事后信号的路径；hard negative 也没有与窗口变量打包，留给 P2 单独验证。

## 4. 数据统计与硬门

| 项目 | B1 | B2 | C3 |
|---|---:|---:|---:|
| 正样本 | 2,388 | 2,388 | 2,388 |
| easy negatives | 2,388 | 2,388 | 2,388 |
| train 图（正+负） | 4,060 | 4,060 | 4,060 |
| val 图（正+负） | 716 | 716 | 716 |
| train 时间 | 2025-06-05 14:45 → 2026-03-18 12:45 UTC | 同左 | 同左 |
| val 时间 | 2026-03-20 06:00 → 2026-05-03 10:45 UTC | 同左 | 同左 |
| P0 数据门 | 8/8 pass | 8/8 pass | 8/8 pass |

共同事件尺包含 715 个 decision endpoints：358 个正事件 endpoint、357 个真实 easy-empty 背景 endpoint。A/B1/B2/C3 使用完全相同的 eval IDs，仅各自从同一 decision endpoint 向左重渲染不同长度。

硬门结果：0 event 跨 split、0 decision 后 K 线、0 label 越界、4,776 image/label/manifest 数量守恒、100% market-bar 可追溯、固定 dataset seed manifest/hash 可复现。

## 5. 统一事件尺与冻结门

- threshold grid：0.05–0.95，步长 0.05。
- event match：预测框中心映射回局部 bar，与 anchor 相差不超过 ±2 根。
- 每个 endpoint 最多计 1 个 TP；额外匹配框与非匹配框都计入 FP，另报告 duplicates。
- FP/1000 bars 分母为 715 个实际扫描 endpoints，不重复累计窗口内部 K 线。
- 候选绝对门：Event Precision≥0.50、Event Recall≥0.50、FP/1000≤250。
- 合格工作点选择：先满足三门，再最小化 FP/1000；相同时优先更高 Precision、Recall。

## 6. 训练诊断（不作接受裁判）

| 臂 | 完成轮次 | best epoch | box P | box R | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|---:|---:|
| A | legacy | legacy | N/A | N/A | N/A | N/A |
| B1 | 24（early stop） | 9 | 34.71% | 99.16% | 51.57% | 37.16% |
| B2 | 20（early stop） | 5 | 71.81% | 79.61% | 76.31% | 48.26% |
| C3 | 60 | 49 | 65.70% | 87.71% | 78.83% | 58.41% |

mAP 只说明 YOLO 自家 validation 的框拟合健康度。C3 的 mAP50-95 高于 B2，但事件主指标反而由 B2 胜出，证明不能用 mAP 代替业务裁决。

## 7. 事件级主结果

下表对通过臂报告冻结 gate operating point；对失败臂报告 best-F1 point，并明确标注失败，避免伪造不存在的合格工作点。

| 臂 | 报告点 | conf | Event P | Event R | Event F1 | FP/1000 | duplicates/检出事件 | 平均延迟 bars | 裁决 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| A | best F1 / max R | 0.05 | 2.96% | 7.54% | 4.25% | 1,239.16 | 0.000 | 1.667 | FAIL |
| B1 | best F1 | 0.10 | 35.43% | 99.16% | 52.21% | 904.90 | 0.408 | 1.487 | FAIL |
| B2 | gate + best F1 | 0.35 | 81.93% | 73.46% | 77.47% | 81.12 | 0.008 | 1.475 | PASS / selected |
| C3 | gate point | 0.45 | 74.71% | 70.95% | 72.78% | 120.28 | 0.154 | 1.496 | PASS |

C3 的纯 best-F1 点在 conf=0.40：Precision 67.95%、Recall 83.52%、F1 74.94%、FP/1000=197.20。机器低误报选择器按预注册目标选择 conf=0.45，而不是事后挑最高 F1。

## 8. 归因与项目方向

当前证据只支持继续验证“两层架构”的局部输入方向：L1 YOLO 从200根全局图转向30根严格因果局部图与小结构框。但当前B2 operating density失败，不能直接把候选送入P3 LightGBM/规则判断层。

B1 的失败说明“更短”不是越短越好。24 根窗让模型出现明显置信度断层：低阈值几乎全报，高一档阈值又丢掉大部分事件。30 根窗在同一数据和配方下形成了更宽的可用工作区；C3 的范围窗可行，但重复框和 FP 高于 B2。本轮只证明当前历史样本上的行为差异，不声称 30 是所有市场/周期的普遍最优值。

下一轮是交接规范中的P2 hard-negative mining：只增加难负例，保持B2 30根窗、事件尺和训练配方不变；同时在独立的非holdout时间块执行连续causal-tip endpoint密度回放。P2密度与事件门通过后才允许进入P3判断层。

## 9. 经济指标与对照组适用性

| 项目标准指标 | 本报告状态 | 原因 |
|---|---|---|
| val AUC | N/A | P1 是目标检测，不是 L2 排序/分类实验 |
| 置换检验 p | N/A | 本轮没有收益排序或方向性策略假设 |
| top-decile 毛/净收益 | N/A | 没有交易入场、TP/SL 或成本归因 |
| 胜率 / PF | N/A | 同上 |
| 单特征 baseline | N/A | 无结构化特征模型 |
| 匹配随机入场对照 | N/A | 本轮只裁决事件检测，不主张经济 edge |

这些指标不是遗漏，而是不适用于检测层 P1。任何收益主张必须等候选进入 L2/forward 后，另用同币×同时间块×同波动桶对照和 0.2% 往返成本重新验证。

## 10. 风险与诚实声明

- 本轮共同尺来自既有历史标签，不是独立未见 forward；所以只能做发现级选择，不能 promote。
- 阈值在同一 pre-holdout validation 曲线上裁决，没有独立 confirmation；生产结论仍为 needs_more_data。
- 只跑了一个 training seed；虽然三臂公平一致，但尚未验证 seed 稳定性。
- 当前只有 easy negatives；后续密度审计已显示其中15.69% endpoint会触发，而已预筛proposal pool触发率49.78%，所以当前B2密度按失败处理。hard negatives、连续市场和新鲜前向仍未验证。
- A 的旧训练几何与当前同 decision 小 anchor 任务不对齐，因此 A 的失败主要说明 legacy 权重不适合新任务；不能把 93.45% FP 降幅外推为线上提升。
- B2 优于 C3 可能同时包含上下文长度、位置分布和置信度校准效应；本轮不作更强因果外推。
- 未读取 holdout，未改 ACTIVE，未部署，未下单。

## 11. 自动化与产物

- 完整项目测试：568 passed、2 skipped、14 warnings、0 failed。
- 矩阵汇总：`analysis/output/p1_local_signal_v2/comparison.json`
- B1/B2/C3 权重与训练证据：`analysis/output/p1_local_signal_v2/training/`
- A/B1/B2/C3 事件曲线：`analysis/output/p1_local_signal_v2/*_event_eval.json`
- 机器裁决：`reports/ACCEPTANCE_DECISION.json`
- Owner 交付：`analysis/html/p1_local_signal_v2_report_20260811.html`

## 12. 从零复现命令

```bash
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py \
  --out datasets/local_signal_v2_stageb_strictneg_v2 --seed 20260807

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py \
  --fixed-window-len 24 --out datasets/local_signal_v2_p1_b1_w24 --seed 20260807

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py \
  --fixed-window-len 30 --out datasets/local_signal_v2_p1_b2_w30 --seed 20260807

for ds in \
  datasets/local_signal_v2_stageb_strictneg_v2 \
  datasets/local_signal_v2_p1_b1_w24 \
  datasets/local_signal_v2_p1_b2_w30
do
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/audit_local_signal_v2.py \
    --dataset "$ds"
done

bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/local_signal_v2_p1_b1_w24 \
  --name p1_b1_causal_w24_cold --epochs 60 --patience 15 --batch 8 --seed 0 \
  --host zzc@192.168.1.4

bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/local_signal_v2_p1_b2_w30 \
  --name p1_b2_causal_w30_cold --epochs 60 --patience 15 --batch 8 --seed 0 \
  --host zzc@192.168.1.4

bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/local_signal_v2_stageb_strictneg_v2 \
  --name p1_c3_causal_range20_30_cold --epochs 60 --patience 15 --batch 8 --seed 0 \
  --host zzc@192.168.1.4

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_p1_eval.py

for arm in A B1 B2 C3
do
  case "$arm" in
    A) weights=models/owner_v10_chain.pt ;;
    *) weights="analysis/output/p1_local_signal_v2/training/$arm/weights/best.pt" ;;
  esac
  MPLCONFIGDIR=/tmp/mplconfig PYTHONPATH=.:../yoyo-trading .venv/bin/python \
    scripts/eval_local_signal_v2_p1.py --arm "$arm" --weights "$weights" \
    --out "analysis/output/p1_local_signal_v2/${arm}_event_eval.json" \
    --device mps --batch 8
done

.venv/bin/python scripts/summarize_local_signal_v2_p1.py
.venv/bin/python scripts/md_to_html.py \
  analysis/p1_local_signal_v2_report_20260811.md --out-dir analysis/html
```

## 13. 下一步选项（需 owner 决策）

- 推荐：进入 P2 单变量 hard-negative mining，基线固定为 B2 30 根窗；不改窗口、seed、阈值网格或事件尺，并增加独立时间块的连续causal-tip密度回放。
- P3 LightGBM/规则判断层在P2密度与事件门通过前阻断。
- 可选：先做 B2 多 seed 稳定性复核，再进入 hard negatives；代价是额外训练时间，但不消耗 holdout。
- 当前禁止：直接 promote B2、读取 holdout、改 ACTIVE、部署或下单。
