# v13 训练期间可做项 — 短报告（2026-07-22）

**纪律**：未抢 MPS / 未杀 v13 / 未耗 holdout / 未 promote / 未改 LIVE。  
v13 当时状态：`src.detection.train … owner_v13_pad200` 仍在跑（约 epoch 3/40）；稳定权重 `models/owner_v13_pad200.pt` 尚无。

议程：`docs/RESEARCH_AGENDA_DETECT.md` · 假设簇：`analysis/p_yolo_dense_hypotheses.md`

---

## 今晚实际做完的两件

### 1）H-DET-2 硬负样本清单（只造表 + 预览，不训）

| 项 | 内容 |
|---|---|
| 脚本 | `scripts/build_hardneg_mid_cluster_inventory.py` |
| 定义 | v11 train GT：`right ∈ [0.30, 0.90)` 且 `bars_after ≥ 8`（窗内簇后仍有后文） |
| 产出 | **2892** 框 / **2835** stem / **197** 币 |
| CSV | `analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_candidates.csv` |
| JSON | `analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_summary.json` |
| 预览 | `analysis/output/hardneg_mid_cluster/previews/`（10 张，青框=硬负候选，黄线=`x=0.95` tip 带） |
| 协议 | `analysis/output/hardneg_mid_cluster/PROTOCOL_train_after_v13.md`（**v13 后再训**） |

**学到的**：中段「有后文」金标不是稀缺噪声——近三千条，后文长度 p50≈**83** 根。这正是模型爱事后框的训练燃料；pad200 只把正样本裁成无后文，**没有**把这类窗标成负。框高 p50≈0.105（相对图高），供以后和 tip 框对照。

**假设状态**：H-DET-2 ⚪ → 🟡（清单与训后协议已备好；**尚未**单变量开训）。

### 2）tip-smoke 评测包加固（CPU 预检，不推理）

| 项 | 内容 |
|---|---|
| 评测脚本 | `scripts/eval_v13_vs_v12_tip.sh`（支持 `--dry-run`；训中拒 mid-run `best.pt`；绑 forward 快照；打印「禁 val mAP 当 tip」检查清单） |
| 强制窗清单 | `scripts/build_tip_smoke_forced_windows.py` → `analysis/output/tip_smoke_forced_windows.json`（27 币 / 32 行，来自 `forward_log_vps_20260721.csv`） |
| dry-run 结果 | v12/脚本/yaml/forward/清单均 OK；缺的只有稳定 v13 权重（预期） |

**学到的**：一键路径依赖已齐；阻塞点只剩「训完落盘」。本机 tip-smoke 仍可能缺部分 K 线 → 脚本已保留 VPS 只读兜底提示。

---

## 故意没做

- 任何 YOLO 前向 / 新训 / 扫全库  
- H-DET-4 渲染消融（协议已有；GPU 忙）  
- 再搜外源空话  

---

## 仍等什么

**唯一大结论阻塞**：H-DET-1 — `owner_v13_pad200` 终局 → tip-smoke vs v12。

### 明早 v13 后第一命令

```bash
# 确认稳定权重存在且训练进程已退出，然后：
bash scripts/eval_v13_vs_v12_tip.sh
```

发现级通过线：tip-smoke 贴边开火 ≫ v12 的 0/27；**禁止**用 val mAP 讲 tip 故事；**禁止**自动 promote。

若 tip-smoke 仍≈0：再开 H-DET-4；硬负用今晚清单立项（需 owner 批单变量加训）。
