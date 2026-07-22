# v15 发现级公平重验 — 2026-07-23

**纪律**：未 promote；未评 holdout；未清 forward_log；未真下单。  
**触发**：Owner「重新回测 v15…验收不对」+ `analysis/p_tip_eval_fairness.md` E1/E2。

## 一句话结论

**仍否决 promote v15。**  
旧验收有两处不公（slice-MA tip_hit 抬 v12 / 27 币无条件 smoke 混入大量应沉默），但换成 **full-MA + Owner 已认真 tip 小样分母** 后：v15 **没有**在应开火集合上相对 v12 形成可上线优势，反而在 **tip-empty-ok 上空背景贴边误火 ≈57%**（v12 贴边误火 0）。「不能上」结论**不变**；变的是否决理由更干净。

---

## 旧验收哪里不公

| 旧尺子 | 问题 | 对结论的偏向 |
|---|---|---|
| `tip_hit`（slice-MA） | 切窗后再 `add_mas`；与 live/pad200 训的 **full-MA** 不一致 | **抬 v12**（同 slice 训）、**压 pad200/v15** |
| tip-smoke 27 币 | 账本 unique symbol 的「当前 tip」，多数本该是 tip-empty-ok | 绝对 0/27 **偏苛**；不能单独证明「完全不会 tip」 |
| 混谈 raw / A′ | 「零框」与「有框被贴边门滤掉」说不清 | 易误读成「模型全哑」 |

本轮纠正：

1. true_tip 改 **full-MA**（`--full-ma`）  
2. 分母改 `v13_real_tip_preview`：**应开火** = tip-hit∪tip-miss-dense；**应沉默空背景** = tip-empty-ok；tip-noise 单列  
3. 同时报 **原始检出** vs **A′ 贴边 KEEP**（`TIP_EDGE_BARS=2`）

金标：`owner_class` 若空则用 **Owner 已认的 provisional_class**（审阅表尚未改判单元格）。

---

## 复现命令

```bash
# K 线需覆盖 preview 信号时刻（本机曾从 VPS 只读拉齐 27 币；勿回推）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/eval_v15_fair_tip.py \
  --preview analysis/output/v13_real_tip_preview \
  --conf 0.30 --tip-edge-bars 2 \
  --out analysis/output/v15_revalidate_fair.json

# 或分步：
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/tip_detectability.py --true-tip --full-ma --limit 120 \
  --weights models/owner_v15_tipval.pt \
  --out analysis/output/tip_rate_v15_fullma.json
```

权重：`models/owner_v15_tipval.pt`（对照 `owner_best`=v12、`owner_v14_pad200`）。conf=**0.30**（发现/入账默认，不是预览地板 0.20）。

---

## 主表 A — true_tip 几何（val 正样本裁贴右，n=120）

| 模型 | 旧 tip_hit（slice-MA） | **新 tip_hit（full-MA）** |
|---|---:|---:|
| v12 | **0.925** (111/120) | **0.0167** (2/120) |
| v14 | 0.033 (4/120) | **0.025** (3/120) |
| v15 | 0.0167 (2/120) | **0.0083** (1/120) |

解读：换 full-MA 后 **v12 优势消失**（证实旧 tip_hit 对 v12 偏松、对 pad200 尺子不公）。但 v15 **并未**因此翻盘——full-MA 下仍 ≈0，相对 v12/v14 无恢复。

---

## 主表 B — 真 tip 小样开火（full-MA + 同渲染；n_eval=47）

分母（去重后）：应开火 **9**（tip-hit 3 + tip-miss-dense 6）；tip-empty-ok **33**；tip-noise **5**。

| 模型 | 应开火 hit **raw** | 应开火 hit **A′边** | 空背景误火 **raw** | 空背景误火 **A′边** | tip-noise A′边 |
|---|---:|---:|---:|---:|---:|
| v12 | 2/9 (0.22) | **1/9 (0.11)** | 6/33 (0.18) | **0/33 (0)** | 5/5* |
| v14 | 3/9 (0.33) | **3/9 (0.33)** | 18/33 (0.55) | **18/33 (0.55)** | 2/5 |
| **v15** | 2/9 (0.22) | **2/9 (0.22)** | 19/33 (0.58) | **19/33 (0.58)** | 1/5 |

\* tip-noise 金标本身由 v12 KEEP 预标，v12 在该桶 5/5 **近乎同义反复**，不作「v12 更吵」证据。

A′ 混淆（正例=贴边 KEEP）：

| 模型 | TP（应开火） | FN | FP（empty） | TN（empty） |
|---|---:|---:|---:|---:|
| v12 | 1 | 8 | **0** | 33 |
| v14 | 3 | 6 | **18** | 15 |
| v15 | 2 | 7 | **19** | 14 |

按类（A′ / raw 同向时合并写）：

| 金标桶 | v12 edge | v15 edge | 含义 |
|---|---:|---:|---|
| tip-hit (应中) | 1/3 | 2/3 | 小样噪声级 |
| tip-miss-dense (应中) | 0/6 | **0/6** | v15 仍全漏 |
| tip-empty-ok (应沉默) | **0/33** | **19/33** | v15 贴边乱开火 |
| tip-noise | 5/5* | 1/5 | 见上 |

v12 在 empty 上 **raw 6 / edge 0**：有中段框、A′ 滤掉——「有框被滤」≠「零框」。v14/v15 的 empty 误火 **raw≈edge**，是真贴边误火，不是门滤问题。

---

## 相对旧报告：结论变了吗？

| 问题 | 旧（`p_v15_tip_val.md`） | **公平重验后** |
|---|---|---|
| Hypothesis B（val tip-align 救 tip）？ | 否决（slice tip_hit 仍崩） | **仍否决**（full-MA tip_hit 更崩；真 tip 应开火未过线） |
| v15 能否 promote？ | 否 | **仍否** |
| 否决主因是否「尺子冤死 pad200」？ | 部分可能 | **否**：full-MA 后 v12 tip_hit 也归零；真 tip 上 v15 空背景误火远差于 v12 |
| tip-smoke 0/27 是否单独成立？ | 当过主证据 | **降级为辅**：分母不纯；本轮以条件分母为准 |

**仍否决 v15 上线**；主线仍 v12。勿把「旧 tip_hit 不公」读成「v15 其实可以上」。

---

## 风险与诚实声明

- 真 tip 小样 **n 小**（应开火仅 9）；预标是规则密集∩v12 门，**不是**人手逐框 GT；Owner 未改 `owner_class` 单元格时按已认 provisional。  
- tip-noise / tip-hit 桶与 v12 行为部分纠缠；跨模型比 **应开火 hit** 与 **empty 误火** 更干净。  
- 本机 K 线曾停在 07-16；评测前从 VPS **只读**拉齐覆盖 preview 的币种（未回推、未改 VPS 写者地位）。  
- 未跑交易 PF / frozen F1 / holdout；本篇只做发现级。  
- conf=0.30；预览采集曾用 0.20——阈值不同不混表。

## 产物

- 本报告：`analysis/p_v15_revalidate_fair.md`  
- 汇总：`analysis/output/v15_revalidate_fair.json`  
- 分模型：`tip_rate_{v12,v14,v15}_fullma.json`、`real_tip_fair_{v12,v14,v15}.json`  
- 脚本：`scripts/tip_detectability.py --full-ma`、`scripts/eval_v15_fair_tip.py`

## 下一步（需 Owner 决策）

1. **不 promote v15**（本轮建议；已默认执行）。  
2. 是否在审阅页改判 `owner_class` 后重跑同脚本（分母更硬）。  
3. 停同构 pad200；真 tip 金标扩采 / 另开假设——另批。
