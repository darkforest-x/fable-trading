# 只做空全链路作战计划（short-only pipeline）

**日期**：2026-07-24  
**Owner 指令**：先跑通只做空完整链路——① short YOLO 检测 → ② short-only 判断层 → ③ 回测/优化。  
**状态**：**选项 1 数据集已重建**（tip 重裁+时间切分）；**未开训**（等 Owner 看 `owner_side_short_tip_sample30`）；v1 train aborted；判断层 short 骨架已铺；**未** promote / **未**动 holdout / **未**改 ACTIVE。

---

## 0. 一句话

多空分模、双链路已批；本报告只定义 **空边一条** 的三阶段闸门与复现命令。成功标准是 tip-smoke/诚实评估 + top-decile 扣成本净收益/置换，**不用 AUC 当成功标准**。

---

## 0.5 选项 1 进度（2026-07-24 12:45）

| 项 | 状态 |
|---|---|
| Owner 决策 | **选项 1**：tip 重裁窗 + 重写框 + 时间切分 |
| 新数据集 | `datasets/dense_owner_side_short_tip/`（**不**覆盖坏集 `dense_owner_side_short`） |
| 计数 | train **1037/1037**，val **324/324**；holdout **0** |
| `box_right_frac` | 旧 p50 **0.52** → 新 p50 **0.997**（≥0.9 = 100%） |
| 时间切分 | `VAL_CUT=2026-02-01`；train_max **2026-01-31 15:30** < val_min **2026-02-01 10:30** |
| 建集脚本 | `scripts/build_owner_side_short_yolo_tip.py` → `build_meta.json` |
| 样本 | `analysis/output/owner_side_short_tip_sample30/`（绿框 + tip 红线） |
| 训练 | **未启动** — 等 Owner 看图确认后再批开训 |

复现建集：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip.py
# → datasets/dense_owner_side_short_tip/ + sample30
```

开训草稿（**仅当 Owner 看图确认后**；勿擅自跑）：

```bash
PYTHONPATH=. .venv/bin/python -m src.detection.train \
  --data datasets/dense_owner_side_short_tip/data.yaml \
  --model models/yolo11n.pt \
  --name owner_side_short_tip_v1 \
  --epochs 100 --patience 20 --batch 8 --imgsz 960 \
  --device mps --workers 4 --cache disk --no-finetune
```

---

## 1. 现状盘点（2026-07-24 午；含叫停）

| 项 | 状态 |
|---|---|
| short YOLO 训练 | **v1 aborted**（坏 pretip 集）；**tip 集未开训** |
| 数据集（坏/旧） | `datasets/dense_owner_side_short/`：保留对照；勿续训 |
| 数据集（新） | `datasets/dense_owner_side_short_tip/`：见 §0.5 |
| 判断层入口 | 规则：`src/judgment/{candidates,labeling,features,build_dataset,train}.py`；YOLO 候选：`scripts/yolo_candidate_source.py` + `src/judgment/yolo_candidates.py` |
| short 标签源 | 检测金标：`review_sheet.owner_side=short`；判断障碍：`label_short_candidate`（及 trail/ma 变体，本轮默认不动） |
| 混边教训 | 混池 PF 不得作裁决；主表必须 long-only \| short-only（见 `docs/learnings/long-short-must-be-split-in-base-rate-tables.md`） |
| §7-2 dump | 3060 并行，**不杀** |

---

## 2. 三阶段定义与成功标准

### 阶段 A — 检测层（short YOLO）

**目标**：得到可诚实评估的空边 tip 检测器权重（研究用路径，默认不晋升）。

| 成功标准 | 说明 |
|---|---|
| tip-smoke 诚实评估 | 协议见 `analysis/p_tip_eval_fairness.md`；裁决以 tip-smoke / 真 tip 金标为准，**自家 val mAP / 旧 frozen-F1 永不作晋升裁决**（纪律 12） |
| 权重落盘 | `.../owner_side_short_v1/weights/best.pt`；early-stop 允许（patience 20） |
| 禁止 | 自动 promote / 改 `models/ACTIVE` / 清 `forward_log` |

**复现命令草稿**（**仅 Owner 看 tip sample30 确认后**；坏集勿续训）：

```bash
PYTHONPATH=. .venv/bin/python -m src.detection.train \
  --data datasets/dense_owner_side_short_tip/data.yaml \
  --model models/yolo11n.pt \
  --name owner_side_short_tip_v1 \
  --epochs 100 --patience 20 --batch 8 --imgsz 960 \
  --device mps --workers 4 --cache disk --no-finetune \
  > analysis/output/owner_side_short_tip_v1_train.log 2>&1
```

**训练结束后（需 owner 点头再跑 tip-smoke；路径示意）**：

```bash
# tip-smoke 同口径（示例；以现网 diag 脚本参数为准，勿改新鲜度三门）
PYTHONPATH=. .venv/bin/python scripts/diag_forward_detect_lag.py --tip-smoke \
  --weights runs/detect/runs/detect/owner_side_short_v1/weights/best.pt
```

### 阶段 B — 判断层（short-only）

**目标**：只吃空边候选的数据集 + LightGBM，池名带 `short`，不覆盖 long/v2 池。

| 成功标准 | 说明 |
|---|---|
| 数据集 | 规则池：`data/judgment_dataset_v2_{strict\|expanded}_short.csv`；YOLO 池（需 A 权重）：`data/judgment_yolo_owner_side_short.csv` |
| 训练指标（汇报用） | val AUC、置换 p、top-decile 毛/净、胜率、单特征基线——**裁决只看净收益+置换，不看 AUC** |
| 闸门 | **不加** `--eval-holdout`；holdout 记账 N=7 不动 |
| 单变量 | 本轮只搭 short-only 骨架；**不**改 TP/SL/成本/阈值预设 |

**规则 short 池（不依赖 best.pt，训练未完即可跑）**：

```bash
PYTHONPATH=. .venv/bin/python -m src.judgment.build_dataset \
  --side short --mode strict
# → data/judgment_dataset_v2_strict_short.csv

PYTHONPATH=. .venv/bin/python -m src.judgment.build_dataset \
  --side short --mode expanded
# → data/judgment_dataset_v2_expanded_short.csv

PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_dataset_v2_strict_short.csv \
  --tag p2b_v2_strict_short
# 禁止加 --eval-holdout
```

**YOLO short 池（权重就绪后；主链优先于规则池）**：

```bash
PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
  --side short \
  --weights runs/detect/runs/detect/owner_side_short_v1/weights/best.pt \
  --out data/judgment_yolo_owner_side_short.csv \
  --workers 4

PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short.csv \
  --tag p2b_yolo_owner_side_short
# 禁止加 --eval-holdout
```

### 阶段 C — 回测与优化

**目标**：在 short-only 表上做发现级回测/优化；主表禁止混边。

| 成功标准 | 说明 |
|---|---|
| 净收益 | top-decile（或预注册 accept 带）扣 **0.2%** 往返后净收益为正 |
| 置换 | p&lt;0.01（或报告中同等置换协议） |
| 分边 | 凡脚本同时含多空，主表必须 short-only；both 仅对照 |
| 确认级 | 仍只认前向新鲜 100 笔；val/accept PF ≠ 实盘 |
| 禁止 | holdout 消耗须另批；不自动 promote |

**复现命令草稿**（权重+判断数据集齐后；具体脚本随单变量实验再锁）：

```bash
# 例：在 short YOLO 池上做发现级扫参（勿混 long 行；勿 --eval-holdout）
PYTHONPATH=. .venv/bin/python scripts/ml_layer_opt_sweep.py \
  --data data/judgment_yolo_owner_side_short.csv \
  --tag ml_opt_yolo_owner_side_short
```

---

## 3. 依赖与闸门

```
[A] short YOLO train ──► best.pt 落盘
        │
        ├─ tip-smoke / 真 tip 金标诚实评估（晋升另批）
        │
        └─► [B2] yolo_candidate_source --side short
                    │
[B1] build_dataset --side short（可并行，不依赖权重）
                    │
                    └─► train --tag *_short（无 --eval-holdout）
                              │
                              └─► [C] short-only 回测/优化 → 报告
```

| 闸门 | 未满足时禁止 |
|---|---|
| A 权重未出 | 禁止 YOLO short 候选扫库、禁止 tip-smoke 结论、禁止 promote |
| B 未出 short 池 CSV | 禁止声称 short 判断层已重构完成 |
| holdout | **全程禁止**，除非 owner 显式批准并记账「第 N 次」 |
| ACTIVE / forward_log / 真金 / 新鲜度三门 | 本计划不动 |

---

## 4. 风险与诚实声明

1. **IT-00~15 / holdout#7**：决策时刻方向边与空边趋势出此前多为红/黄；本链路是 owner 选定的新命题（分模 short），**不**自动继承旧「无可交易边」定论为终审，但也**不**假设必过。  
2. **val mAP 好看 ≠ tip**：纪律 12；epoch 早期 mAP 波动不作晋升证据。  
3. **混边 PF**：历史教训是测量呈现 bug；本链路主表只报 short。  
4. **规则 short 池 ≠ YOLO short 池**：规则池可先验证标签/特征管道；主链裁决以 YOLO short 候选为准（检测层职责）。  
5. **障碍/成本未改**：本轮骨架未动 TP/SL/0.2%/阈值；若要改须 owner 另批（单变量）。  
6. **§7-2 大样本 dump** 与本链路并行，结果不互相续命、不互相阻塞。

---

## 5. 需 Owner 决策的点

0. **（当前闸门）看 tip sample30 后是否批准开训** `owner_side_short_tip_v1`？默认：**先不要训**，看图确认再说。  
1. **阶段 A 结束后是否跑 tip-smoke / 真 tip 金标验收**（研究用 vs 是否申请晋升门）？默认建议：先 tip-smoke 诚实报，**不** promote。  
2. **判断层主池**：规则 short（`judgment_dataset_v2_*_short`）作对照，还是 **YOLO short**（`judgment_yolo_owner_side_short`）作唯一主链？默认建议：YOLO short 主链，规则 short 对照。  
3. **阶段 C 障碍**：沿用 YOLO 历史 TP5/SL2，还是另批单变量扫障碍？（本报告默认先 TP5/SL2，与 `yolo_candidate_source` 一致；规则池仍为 labeling 默认 TP4/SL2——两池对照时勿混读。）

---

## 6. 本轮已落地（不依赖 best.pt）

- `scripts/build_owner_side_short_yolo_tip.py` → `datasets/dense_owner_side_short_tip/` + sample30（选项1；**未开训**）。  
- `src/judgment/build_dataset.py`：`--side short` → `scan_short_candidates` + `label_short_candidate`；默认输出 `judgment_dataset_v2_{mode}_short.csv`；记录含 `side` 列。  
- `scripts/yolo_candidate_source.py`：`--side short` → `label_short_candidate`；默认输出 `judgment_yolo_owner_side_short.csv`。  
- `HANDOFF.md` 顶部「当前真相」更新为本链路状态。  
- **未** commit；**未**启动 tip YOLO train；**未**启动 long YOLO；**未** consume holdout。
