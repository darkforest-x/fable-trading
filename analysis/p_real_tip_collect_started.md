# 真实 tip 成败金标小样 — 已开干（2026-07-22 夜）

> Owner 已点头「按真 tip 采集开干」。**未**开训、**未** promote、**未**耗 holdout、
> **未**改新鲜度三门 / 脉冲 / ACTIVE。单变量：只做 tip 成败预标小样。

## 结论先行

本机 K 线停在 07-16，盖不住账本信号 → **在 VPS 上采集**后拉回本机。

| 项 | 值 |
|---|---|
| 目录 | `analysis/output/v13_real_tip_preview/` |
| Owner 审阅页 | **`analysis/output/v13_real_tip_preview/index.html`** |
| 审阅表 | `review_sheet.csv`（填 `owner_class` / `owner_note`） |
| tip+0 张数 | **48**（forward 32 + scout 16；PNG 去重后 47） |
| 窗几何 | 200 bar、右缘=tip、无后文（**非** pad200） |
| 权重 / 门 | `owner_best.pt`（v12）· conf 预览地板 **0.20** · `TIP_EDGE_BARS=2` |

预标四类（自动，**不是**训练 GT）：

| provisional | n | 含义 |
|---|---:|---|
| tip-hit | 4 | 贴边 KEEP + tip 近端密集规则 |
| tip-miss-dense | 6 | tip 近端有密集，无 KEEP（漏检候选） |
| tip-noise | 5 | 有 KEEP，tip 近端无密集（误检候选） |
| tip-empty-ok | 33 | 无密集、无 KEEP（背景） |

成功样例：`KGEN_USDT_SWAP_20260721_0530`（tip-hit）、scout `APT_USDT` / `ETH_USDT`。  
失败/漏检样例：`DOOD_USDT_SWAP_20260720_0815`（tip-miss-dense，青框=规则）、`ONE`/`RAVE`（tip-noise）。

## 做了什么

1. 增强 `scripts/collect_v13_tip_previews.py`：密集规则 ∩ tip_edge → 四类预标；青框=规则、绿=KEEP、橙=DROP；写 `index.html` / `stats.json` / `review_sheet.csv`。
2. VPS 跑（K 线覆盖账本）：
   ```bash
   OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
     scripts/collect_v13_tip_previews.py \
     --log data/forward_log.csv --limit 32 --conf 0.20 \
     --scout-dense 8 --scout-empty 8 --tip-plus-max 0 \
     --out analysis/output/v13_real_tip_preview
   ```
3. 拉回本机；账本快照 `analysis/output/forward_log_vps_20260722_tipcollect.csv`。

## Owner 怎么审

1. 浏览器打开：
   ```bash
   open analysis/output/v13_real_tip_preview/index.html
   ```
   顶栏可按四类过滤。
2. 对照图例：红竖线=右缘 tip；**青=密集规则预标**；绿=YOLO KEEP；橙=DROP。
3. 在 `review_sheet.csv` 填 `owner_class`（可改判）与 `owner_note`。重点看：
   - tip-hit 是否真像「盘口正在启动」
   - tip-miss-dense 青框是否该人手补框
   - tip-noise 绿框是否该扔
   - tip-empty-ok 是否误杀（其实有密集）
4. **审完一批且四类都有共识样例后**，才谈扩采 / 开训。现在 **不开训**。

## 计划默认最小值（已落地）与待 Owner 再批

| 项 | 本次默认 | 需 Owner 再批？ |
|---|---|---|
| forward `--limit` | 32（账本唯一 tip 上限） | 扩到全量更新账本 / 持续落盘 |
| scout dense/empty | 各 8 | 是否加大 scout、是否只要 live |
| `--conf` | 0.20（预览 raw；live 入账仍 0.30） | 是否主集只用 ≥0.30 |
| `TIP_DENSE_HIT_BARS` | 16（与空标 tip 包一致） | 是否收紧/放宽 tip 近端定义 |
| `--tip-plus-max` | 0（只 tip+0） | 是否要 tip+1/+2 形成窗 |
| 开训阈值 | **未开** | 目视通过后的目标张数 / 是否训 tip 头 |

## 风险与诚实声明

- 预标用规则密集，**不是**人手金标；forward 行多为事后补认，tip 当下大量 tip-empty-ok 与 tip-smoke 一致。
- scout 是 holdout 前形态对照，**无**前向 PnL；不能替代 live 成败分布。
- tip-hit 仅 4 张（含 scout）— 小样够目视，不够训。
- 本机未重跑（K 线过旧）；以 VPS 产物为准。
- 未动 holdout / promote / forward_log 清空。

## 产物索引

- 预览包：`analysis/output/v13_real_tip_preview/`（`index.html` · `manifest.json` · `stats.json` · `review_sheet.csv` · `*.png`）
- 脚本：`scripts/collect_v13_tip_previews.py`
- 计划：`analysis/p_v13_real_tip_collect_plan.md`（状态改为已开干）
- 学习：`docs/learnings/real-tip-gold-needs-owner-review-not-pad200.md`
