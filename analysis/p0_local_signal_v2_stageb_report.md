# P0 — Local Signal V2 Stage B：因果数据集重建与硬门槛通过

**日期**：2026-08-07  
**授权**：owner 全文生效（P0 自检绿 → 自动 P1）  
**HEAD（工程提交）**：`bed5e64`  
**范围**：新建 Stage B 数据集 + 审计；**未** promote ACTIVE / owner_best；**未**真下单。

---

## 0. 一句话结论

Stage A（`dense_owner_w20_midbox`）P0 **失败**（7 门过 3）。  
按授权重建 **Stage B**（`datasets/local_signal_v2_stageb`）后 **七道硬门槛全绿，`p0_pass=True`**，进入 P1 冷启动训练（3060，`owner_lsv2_stageb_cold`）。

---

## 1. Stage B 协议

| 项 | 值 |
|---|---|
| Mode | C（`confirm_delay ∈ {1,2}`） |
| Stage | B（`visible_end == decision`，未来 K = 0） |
| Box | `[anchor−2, decision]` |
| Window | 20–30 根，右端钉在 decision |
| Split | 时间序；最后 15% → val；train 与 val 之间 purge 150 bar |
| Holdout | 丢弃 `end_time ≥ 2026-05-04` |
| Negatives | empty_bg 1:1，带 timestamp，同一 split |
| 来源 event | Stage A `w20_manifest` 的 `mid_global`（不重做 pad MAD） |

---

## 2. 数据统计

| split | pos | empty_bg | 时间范围 (end_time) |
|---|---:|---:|---|
| train | 2030 | 2030 | 2025-06-05 → 2026-03-18 |
| val | 358 | 358 | 2026-03-20 → 2026-05-03 |
| **合计** | **2388** | **2388** | holdout 前 |

- 跳过 holdout 源事件 246（与 Stage A 泄漏数一致，现已剔除）  
- purge zone 1  
- manifest 行 = 图片数 = **4776**（守恒）

---

## 3. P0 七道门

| 门 | 结果 |
|---|---|
| visible_end ≤ decision（全样本 future_bars=0） | ✅ |
| box_end ≤ decision | ✅ |
| event 不跨 split | ✅ |
| 时间切分（train max < val min，gap ≈1.7d） | ✅ |
| 训练集无 holdout | ✅ |
| label 不越界 | ✅ |
| manifest 守恒 | ✅ |

**`p0_pass = True`** · 审计：`analysis/output/p0_local_signal_v2_stageb_audit.json`  
**决策**：`reports/ACCEPTANCE_DECISION.json` → `decision=accepted`

---

## 4. 与 Stage A 对照

| | Stage A w20 | Stage B lsv2 |
|---|---|---|
| 未来 K 中位 | 9 | **0** |
| split | symbol hash | **时间 + purge** |
| holdout 进训练 | 246 张 | **0** |
| hardneg 无 manifest | 2300 | 本阶段无 hardneg（P2） |
| P0 | FAIL | **PASS** |

---

## 5. 复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_local_signal_v2_stageb.py --limit 0
.venv/bin/python scripts/audit_local_signal_v2.py \
  --dataset datasets/local_signal_v2_stageb \
  --out analysis/output/p0_local_signal_v2_stageb_audit.json
.venv/bin/python -m pytest tests/test_local_signal_v2_stageb.py tests/test_w20_midbox_causality.py -q
export FABLE_3060_HOST=zzc@192.168.1.4
bash scripts/train_local_signal_v2_stageb_on_3060.sh
```

---

## 6. 风险与诚实声明

1. Stage B 位置分布集中在右侧（tip-aligned），**不是** Stage A 的宽位置随机；规范 Stage A 预训练仍可选（P1 矩阵 C1）。  
2. Anchor 仍来自 pad200 框中心，**不是**新人工 tip 金标；P1 只能验证「因果局部窗是否可学」，不能直接宣称盘口 edge。  
3. 训练尚未出结果；mAP **不作**验收主指标（规范 §18.8）。  
4. ACTIVE / owner_best / main forward_log **未动**。  
5. V2 holdout 预算 1 次留给最终验收，尚未消耗。

---

## 7. 下一步（授权内自动）

1. P1：`owner_lsv2_stageb_cold` 训完 → event 级 / tip 回放对照 Stage A  
2. 若 C 明显优于 baseline → 尽量 P2 hardneg  
3. P3：仅 paper scaffold（已有 `scripts/forward_paper_local_signal_v2_scaffold.py`）  
4. **禁止** promote
