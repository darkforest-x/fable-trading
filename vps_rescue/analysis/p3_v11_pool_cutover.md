# p3 — v11 池判断层切换 ACTIVE

日期: 2026-07-18  
一句话: 用 `owner_v11_chain`（frozen-F1 0.658）重扫 344 合约，得到 26653 候选；
回归冻结后切 ACTIVE。accept 窗口 @0.3%：**703 笔 / 净资金 +245.8% / PF 6.61**，
相对 v8 池笔数↑、PF 略降但仍过验收四项。

## ⚠️ holdout 记账

**本对比是 accept 窗口（≥2026-05-04）的第 4 次消耗**，项目所有者在对话中明确批准
「开全量 v11 重扫+重冻」。前三次:

1. 2026-07-08 — 2b 验收  
2. 2026-07-15 — Grok 回归切换回测  
3. 2026-07-16 — v8 池 cutover  
4. **2026-07-18 — 本对比（v11 池 vs v8 池）**

## 复现命令

```bash
# 1) 全量重扫（~4h on M4 workers=5）
PYTHONPATH=. OMP_NUM_THREADS=1 .venv/bin/python -u scripts/yolo_candidate_source.py \
  --weights models/owner_best.pt \
  --out data/judgment_yolo_swap_v11.csv \
  --workers 5

# 2) 冻结 + ACTIVE
PYTHONPATH=. .venv/bin/python scripts/freeze_model.py \
  --yolo-v11-pool --date 20260718 --write-active

# 3) 阶段 3 回测（必须 --frozen-config default，禁止落回 retrain）
PYTHONPATH=. .venv/bin/python -m src.backtest.run \
  --data data/judgment_yolo_swap_v11.csv \
  --tag p3_yolo_v11_reg \
  --frozen-config default
```

## 数据统计

| | 旧池 ACTIVE (v8) | **新池 (v11)** |
|---|---|---|
| 检测器 | owner_v8 时代扫描权重 | **owner_v11_chain F1 0.658** |
| 币种 | 267 | **344** |
| 候选 | 17573 | **26653** |
| 正类率 | ~0.32（报告时） | **0.3174** |
| 时间范围 | 2025-06 → 2026-05 段 | 2025-06-05 → 2026-07-15 |
| 判断配方 | 回归 realized_ret, val-q90 | **同左（唯一变量=池/检测器）** |
| 冻结阈值 | 0.02171 | **0.02022** |
| best_iteration | 80 | **61** |

## 结果表 — accept 窗口（≥2026-05-04），MAX_CONCURRENT=10

| 指标 | 旧池 v8 @0.3% | **新池 v11 @0.3%** | v8 @0.4% | v11 @0.4% |
|---|---|---|---|---|
| 笔数 | 428 | **703** | 428 | 703 |
| 净收益（对资金） | +154.9% | **+245.8%** | +150.6% | +238.8% |
| 每笔净 | +3.62% | **+3.50%** | +3.52% | +3.40% |
| PF | 7.50 | **6.61** | 7.10 | 6.25 |
| 胜率 | 79.7% | **77.1%** | 79.4% | 77.0% |
| 最大回撤 | 0.70% | **0.76%** | 0.72% | 0.78% |
| 验收四项 | 4/4 | **4/4** | — | — |

工件: `analysis/output/p3_yolo_v11_reg_backtest.json`  
冻结: `models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718.{txt,json}`  
ACTIVE → 同上；ACTIVE_PREV → v8；SHADOW_V8_REG → v8 冻结路径。

## 解读

1. **覆盖扩大**: 候选 1.52×、accept 笔数 1.64×（428→703），币种 267→344。  
2. **单笔质量略降**: 每笔净 3.62%→3.50%，PF 7.50→6.61，胜率 79.7%→77.1%——更广召回的正常代价。  
3. **资金曲线仍更强**: 同成本下净资金回报 154.9%→245.8%（更多并发填充的成交）。  
4. **验收四项全过**: 净>0、PF≥1.3、maxDD≤20%、n≥100。  
5. **PF 仍“好得反常”**: 与 v8 同量级怀疑（检测层时间切分、模拟器无滑点等）；**前向 100 笔仍是终审**。

## 已执行的切换

- `models/ACTIVE` → `frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt`  
- `frozen.default_config()` → v11 池（`judgment_yolo_swap_v11.csv`）  
- 看板 score cache 失效条件: dataset_sha256 变化 → 下次请求重建  
- v8 保留: `yolo_v8_pool_config` + ACTIVE_PREV + SHADOW_V8_REG  

## 风险与诚实声明

- 第一次 backtest 曾因 default 仍指 v8 而 **retrain 落假数字**（阈值 0.76 像二分类）；  
  已用 `--frozen-config default` + 冻结件重跑，阈值 **0.02022** 与元数据一致。  
- accept 窗口第 4 次消耗；数字只作排序/切换依据，不宣称可实现收益。  
- 前向时钟未重置（仍 YOLO 主线 07-15 起）；live 检测已是 v11 权重，判断现跟 v11 冻结。  

## 下一步（需 owner 决策的标出）

1. VPS rsync + restart dashboard/forward（工程，默认可做）  
2. 是否重置前向账本为「v11 判断时钟」（**owner**）  
3. 继续攒前向至 100 笔终审  
