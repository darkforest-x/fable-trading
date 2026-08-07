# P1 — Local Signal V2 Stage B 冷启动（owner_lsv2_stageb_cold）

**日期**：2026-08-07  
**授权**：owner 全文生效（P0 绿 → 自动 P1）  
**范围**：Stage B 数据集上 60 epoch 冷启动；**未** promote ACTIVE / owner_best；**未**真下单；**未**消耗 V2 holdout。

---

## 0. 一句话结论

P0 通过后的 **P1 冷启动完成**。  
best.pt 在 **时间切分 val**（358 正 / 358 空负，2026-03-20→05-03，无 holdout）上：

| 指标 | best.pt（fitness） | 峰值 mAP50 的 epoch |
|---|---:|---:|
| epoch | **49** | 34 |
| Precision | **0.654** | 0.678 |
| Recall | **0.816** | 0.925 |
| mAP50 | **0.771** | 0.778 |
| mAP50-95 | **0.572** | 0.542 |

**mAP 不作验收主指标**（规范 §18.8）。本轮证明的是：  
在 **因果 Stage B 数据 + 时间切分** 上，YOLO 可以学到非平凡的 val 检测表现；  
**尚未**证明 tip 净收益 / event precision / 相对 legacy 200-K 的交易级优势。

---

## 1. 数据与训练配置

| 项 | 值 |
|---|---|
| 数据集 | `datasets/local_signal_v2_stageb` |
| 协议 | Mode C + Stage B（`visible_end==decision`，future_bars=0） |
| train | 2030 pos + 2030 empty_bg |
| val | 358 pos + 358 empty_bg |
| 时间 | train ≤2026-03-18；val 2026-03-20→2026-05-03；holdout ≥05-04 剔除 |
| 模型 | yolo11s cold，`owner_lsv2_stageb_cold` |
| epochs / patience / batch | 60 / 15 / 8 |
| imgsz | 960 |
| 增强 | fliplr/flipud/mosaic/mixup=0；`hsv_s/v=0.05`（继承 train_dense，铁律 5 严格讲仍违规） |
| 设备 | RTX 3060 · 1.315 h |
| 权重 | `analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt` |
| sha256 前缀 | `de80173ed05962d70bb19ae5…` |
| results | `…/results.csv` |

---

## 2. 与 Stage A / 旧基线对照（诚实口径）

| 模型 | val 定义 | mAP50 | 备注 |
|---|---|---:|---|
| Stage A w20 cold | symbol-hash，95% 含未来 K，含 holdout 泄漏 | 0.281 | **不可与 Stage B 直接比** |
| Stage A hardneg-c1 | 同上 + hardneg val | 0.238 | val 分母不同 |
| **Stage B cold（本轮）** | 时间切分、0 未来 K、0 holdout | **0.771** | 任务更难（因果），指标却更高 → 值得继续，**不是**实盘证明 |
| Legacy owner_v10 200-K | 旧 pad200 分布 | — | 本轮未重跑 A 臂（算力优先 Stage B） |

规范 §15 完整矩阵（A/B1/B2/C1/C2/C3）**未做完**——只完成了 **C 向（Stage B causal cold）** 一臂。

---

## 3. 复现命令

```bash
cd /Users/zhangzc/fable-trading
# 数据（若尚未构建）
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_local_signal_v2_stageb.py --limit 0
.venv/bin/python scripts/audit_local_signal_v2.py --dataset datasets/local_signal_v2_stageb

# 训练（3060）
export FABLE_3060_HOST=zzc@192.168.1.4
# 稳定路径：SSH 常驻（WMI .cmd 曾失败）
ssh zzc@192.168.1.4 "C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_dense.py \
  --name owner_lsv2_stageb_cold --model C:/fable/models/yolo11s_w20.pt \
  --dataset C:/fable/datasets/local_signal_v2_stageb --epochs 60 --patience 15 --batch 8 \
  --cache false --workers 2"

# 拉权重
scp zzc@192.168.1.4:C:/Users/zzc/runs/detect/runs/detect/owner_lsv2_stageb_cold/weights/best.pt \
  analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt
```

---

## 4. 风险与诚实声明

1. **val mAP ≠ tip edge**。Stage A 的教训：F1 0.40 与 tip smoke PF 0.27 可并存。  
2. Stage B 框在右侧（tip-aligned），位置随机 Stage A 预训练（C1）未做。  
3. Anchor 仍来自 pad200 中心，不是真 tip 金标。  
4. 完整 P1 矩阵与 event-level / FP-per-1000 / matched control **未交付**——下一步必须补。  
5. ACTIVE / owner_best / main forward_log **未动**。  
6. V2 holdout 预算 1 次 **未消耗**。

---

## 5. P2 门（授权：C 明显优于 baseline 才进）

当前状态：**不足以自动进 P2 hardneg 全量**。

理由：相对 baseline 的 **event / tip / 匹配对照** 数字尚未产出；仅 val mAP 不能触发「明显优于」。

**下一步（授权内自动继续）**：
1. Stage B tip smoke + preholdout tip 回放（同障碍同成本 + matched control）  
2. 若 tip 净收益与对照显著优于 Stage A / 随机 → 再开 P2 hardneg  
3. P3 paper scaffold 已存在；权重就绪后可挂 shadow paper log（不 promote）

---

## 6. 决策 JSON

见 `reports/ACCEPTANCE_DECISION.json`（phase=P1，decision=`needs_more_data` 于交易级门）。
