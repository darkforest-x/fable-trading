# 检测层研究议程（H-DET）— 均线密集 + 盘口 tip

> **从属**：主议程 `docs/RESEARCH_AGENDA.md` 的 H-TIP 子簇。  
> **成功标准（发现级）**：强制 tip 窗 + `TIP_EDGE_BARS=2` 后贴边开火率相对 v12 **明显 >0**；  
> frozen-F1 不崩（回撤 ≤0.03 量级）。**确认级**仍只认 VPS 前向新鲜 100 笔。  
> **纪律**：不耗 holdout、不自动 promote、不打断 `owner_v13_pad200` 训练。

## 状态图例
🟢 已验证（发现级） · 🔴 已证伪 · 🟡 排队中 · 🔵 进行中 · ⚪ 未开始

## 人话总览（2026-07-22）

| # | 人话 | 状态 | 今晚？ |
|---|---|---|---|
| H-DET-1 | pad200「框后无后文」训出的 v13，比 v12 更能在盘口 tip 贴边开火 | 🔵/🟡 **等训完对照** | 等 checkpoint 终局，脚本已备 |
| H-DET-2 | 把「有后文的中段簇」当硬负样本，能压住事后框 | ⚪ 未单变量测 | 否（勿抢 v13） |
| H-DET-3 | 验收只看右缘 N 根有没有框，不只看 mAP | 🟢 已作必报口径 | — |
| H-DET-4 | MA 线宽/颜色/留白等渲染差会伤 tip | 🟡 线索有、消融未跑 | 协议已写；GPU 忙则不动 |
| H-DET-5 | tip 窗单独降 conf 能抬 tip_fire | 🔴 证伪 | — |
| H-DET-6 | tip-only 调度能抬 tip_fire | 🔴 证伪 | — |
| H-DET-7 | 离线 true_tip tip_hit ≠ 实盘 tip_fire（协议鸿沟） | 🟢 已证实 | — |
| H-DET-8 | A′ 贴边入账门能止血事后账，但**不**制造 tip | 🟢 工程通过 / 🔴 当 tip 解药 | — |

## 假设表

| # | 假设（说人话） | 设计（单变量） | 判定 | 状态 |
|---|---|---|---|---|
| **H-DET-1** | **pad200**：把金标框右缘裁成窗末、左侧补满 200 根，正样本「无后文」→ v13 比 v12 提高 tip 贴边开火率 | 数据：`dense_owner_v13_pad200`；基座 `owner_v12_htip`；训完对 `owner_best`(v12) | (a) `tip_detectability --true-tip` tip_hit ≥ v12 且不崩 F1；(b) **强制 tip-smoke + tip_edge** 开火率 ≫ v12 的 0/27 | 🔵 **v13 训练中**（07-22）；epoch1 已有 mid-run `best.pt`，**终局评测等训完**，命令见下 / `scripts/eval_v13_vs_v12_tip.sh` |
| **H-DET-2** | **硬负样本**：有后文的中段密集簇（模型爱事后框的那种）标成负/背景，抑制「等后文再框」 | 在 v12/v13 数据上**只加** hard-neg 集（或空标中段窗），其它不变 | tip-smoke 开火率↑ **或** 中段框率↓（账本 tip_edge_rejected / lag 分布），且 tip 正召回不塌 | ⚪ 未开；v13 pad200 只拷了空标背景，**不是**本假设的中段硬负 |
| **H-DET-3** | **右缘 N 根验收**：检测实验必报「窗末 N 根是否有框」，mAP 只作辅 | 评测：`tip_hit` / `bar_in_win ≥ 200−N`（现 N=2）；禁只用 mAP 宣称成功 | 与实盘 tip_fresh 同语义的发现级指标写入每份 p 报告 | 🟢 **已落地为口径**（v12 tip_hit、tip-smoke、tip_subset strict）；继续强制 |
| **H-DET-4** | **渲染差异**：MA 线宽/颜色/y 留白/`MIN_REL_SPAN` 与训练不一致时 tip 掉点 | 极小消融：固定权重+同窗，只改 `render.py` 一两项，比 tip 窗 conf/贴边命中 | 同几何下 tip 开火率相对基线变化 > 噪声 | 🟡 **开放**；夜报/tip_subset 提示「全序列 MA 重渲 tip_hit≪ true_tip 0.925」。GPU 占满时只跑协议不抢训 |
| **H-DET-5** | **tip 窗 conf 单独阈值**（如 TIP_CONF=0.22）能抬 tip_fire | 同权重、同贴边门，只改 tip 窗 conf | tip-smoke fired 与 lag-walk tip_fire 相对 0.30 提升 | 🔴 **发现级证伪（07-21）**：0/27 vs 0/27，账本 tip_fire 1/32 不变；`analysis/p_tip_only_smoke.md` |
| **H-DET-6** | **tip-only 调度**（只扫右缘窗）能抬 tip_fire | `FABLE_YOLO_MODE=tip` vs live | tip_fire / tip_fresh 提升 | 🔴 **发现级证伪（07-21）**：不抬出生率；可作 CPU 省窗，不作新鲜度解药；同报告 |
| **H-DET-7** | **协议鸿沟**：离线 true_tip tip_hit 高 ≠ 盘口 tip 能开火 | 对照：v12 tip_hit 0.925 vs tip-smoke 0/27 + box-to-bar（KORU/EDEN） | 若 offline 高而 live≈0 → 训练分布/几何语义仍错位 | 🟢 **已证实**；`p_v12_htip_eval` + `p_tip_only_smoke` + `p_box_to_bar_lag`。驱动 H-DET-1 |
| **H-DET-8** | **A′ 贴边入账**（最后 N=2 根才入账）能挡事后框进账本，但**不能**从零创造 tip | `TIP_EDGE_BARS=2`；KORU/EDEN 类应被拒 | tip_edge_rejected↑、事后 lag 行↓；tip_fire 仍可为 0 | 🟢 工程止血已上线 / 🔴 **当 tip 解药证伪**（不过滤≠产生 tip）；`HANDOFF` + `p_box_to_bar_lag` |

## 已入库证据（发现级，登记结论）

| 来源 | 对应假设 | 一句话结论 |
|---|---|---|
| `analysis/p_tip_only_smoke.md` | H-DET-5、H-DET-6 | tip-only / TIP_CONF 不抬 tip_fire；强制 tip 0/27 |
| `analysis/p_box_to_bar_lag.md` | H-DET-7、H-DET-8 | 映射无 bug；语义是「框启动区」→ tip−k；A′ 推荐并已上线 |
| `analysis/p_tip_subset_val.md` | H-DET-3、H-DET-7 | tip_strict 相对全量净折扣 **0.0465**；strict tip-hit ~3–4% |
| `analysis/p_v12_htip_eval.md` | H-DET-3、H-DET-7 | v12 true_tip tip_hit **0.925** / F1 0.650；≠ live |
| `analysis/output/diag_tip_smoke.json` | H-DET-5/6 | VPS v12：tip&live 均 **n_fired=0**（27 币） |

## 训完后最小对照（H-DET-1，发现级）

```bash
# 勿打断正在跑的 train；等 pipeline 写出 models/owner_v13_pad200.pt 后：
bash scripts/eval_v13_vs_v12_tip.sh

# 或逐步：
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \
  --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights models/owner_v13_pad200.pt \
  --out analysis/output/tip_rate_v13_pad200.json

# tip-smoke 需要账本符号对应的 K 线（本机常缺 → 优先 VPS 只读跑，或 --from-log 拷贝）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --weights models/owner_v13_pad200.pt \
  --out analysis/output/diag_tip_smoke_v13.json
```

**通过线（发现级）**：tip-smoke 贴边开火率相对 v12 **明显 >0**（不是只看 true_tip tip_hit）；  
再谈 owner 目视预览 / 影子权重。**禁止**自动 promote。

## H-DET-4 渲染消融协议（极小样，可 GPU 空闲时跑）

单变量每次只改一项；权重固定 `models/owner_best.pt`（v12）；窗=已知 tip 漏火币 3–5 张：

1. 基线：现行 `render.py`（MA `thickness=1`、`MIN_REL_SPAN=0.06`、MARGIN）  
2. 变体 A：MA 线宽 1→2  
3. 变体 B：`MIN_REL_SPAN` 0.06→0.03（更「捏」）  
4. 记录：raw 框数、tip_edge KEEP 数、max conf  

GPU 被 v13 占用时：**只写协议，不跑**。

## 优先队列（检测层）

1. **H-DET-1** — v13 终局 vs v12 tip-smoke / true_tip（唯一阻塞）  
2. **H-DET-4** — 若 H-DET-1 仍 tip≈0，先极小渲染消融排除分布伤  
3. **H-DET-2** — 硬负中段簇（新数据实验，需 owner 批开工）  
4. H-DET-5/6/8 — 已结案，勿复读当主药  

## 报告指针

- 本簇汇总：`analysis/p_yolo_dense_hypotheses.md`  
- 主议程指针：`docs/RESEARCH_AGENDA.md` § D / 优先队列  
