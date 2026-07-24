# Short 判断层重构 v2：扩币（30×6m）镜像基线 + top-K 单变量

**日期**：2026-07-24  
**性质**：发现级；**不加** `--eval-holdout`；**不** promote / **不**改 TP/SL / 0.2% 成本 / 阈值预设。  
**前置**：v1 在 5×6m 上 feat_mirror 净 +0.062%→+0.156% 但 n=24、p 变差 → Owner 定「先扩币再优化」。  
**本轮单变量**：仅特征截断 top-K=10（对照同池全 28 列镜像基线；**objective=binary**）。  
**并行主线更正**：同池已由 Owner 纠偏为 **regression**（见 `p_short_judgment_reg_align_v11.md`）；本报告只收口「扩币 + binary/top-K」支线，**不**与回归主叙事争位。

---

## 复现命令

```bash
# 1) 30 常见流动性 USDT SWAP × [2025-11-04, 2026-05-04)
#    tip_v1b best.pt；side=short；主路径已镜像特征
#    launchd: com.fable.yolo_short_30_6m → scripts/run_yolo_short_30_6m.sh
CHUNK_SERIES=1 OUT=data/judgment_yolo_owner_side_short_30_6m.csv \
  SYMBOLS_FILE=analysis/output/yolo_short_30_6m_symbols.txt \
  MONTHS=6 END_BEFORE=2026-05-04 \
  LOG=analysis/output/yolo_owner_side_short_tip_v1b_30_6m_scan.log \
  bash scripts/run_yolo_short_30_6m.sh

# 2) 镜像基线（新扫已走 align_short；无 holdout）
PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_30_6m.csv \
  --tag p2b_yolo_short_30_6m_mirror \
  --side short

# 3) 单变量：按基线 gain 截断 top-10
PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_30_6m.csv \
  --tag p2b_yolo_short_30_6m_mirror_topk10 \
  --side short \
  --features-file analysis/output/yolo_short_30_6m_topk10_features.txt
```

币名单：`analysis/output/yolo_short_30_6m_symbols.txt`（BTC/ETH/SOL/…/POL 等 30 常见 SWAP，非 HV 排序）。

---

## 数据统计

| 项 | 值 |
|---|---|
| 检测器 | tip_v1b `best.pt` |
| 窗 | `[2025-11-04, 2026-05-04)` |
| 币数 | **30** |
| 候选 n | **7519**（正类率 0.288） |
| 切分 | train 5973 / val **1500** / holdout **0** |
| 扫池墙钟 | **≈16.0 min**（08:15:00Z → 08:31:02Z；CHUNK=1 + launchd） |
| 特征 | 主路径 short 镜像（`order_score`∈[0,4] 计数语义；非旧 long 原义） |

对照：5×6m 池 n=1240、val top-decile n=24。

---

## 结果对照

| 指标 | 5×6m 镜像（参考） | **30×6m 镜像基线** | **30×6m top-K=10** |
|---|---:|---:|---:|
| tag | `…_5_6m_feat_mirror` | `p2b_yolo_short_30_6m_mirror` | `…_mirror_topk10` |
| n_features | 28 | 28 | **10** |
| val AUC | 0.590 | **0.518** | 0.500 |
| 置换 p | 0.014 | **0.125** | 0.505 |
| top-decile n | 24 | **150** | 150 |
| top-decile 毛% | +0.356 | +0.019 | −0.037 |
| top-decile 净%（−0.2%） | **+0.156** | **−0.181** | **−0.237** |
| top-decile 胜率 | 0.375 | 0.233 | 0.240 |
| best_iteration | 11 | **1** | **1** |
| all_mean_net | −0.141% | −0.133% | （同池） |

单特征基线（`ma_spread_pct` logreg，30×6m）：AUC≈0.50 量级、不作边。

基线 gain 非零仅 9 列（`atr_pct`,`ret_4`,`volume_ratio`,`vol_ratio_mean8`,`spread_chg8`,`ret_48`,`fast_slow_gap`,`ma_spread_pct`,`pre_range168`）；top-10 多塞了 gain=0 的 `spread_pos96`。

---

## 解读

1. **扩币成功扩大了 n**（val top-decile 24→150），但 **经济边消失**：净 −0.181%，AUC≈随机，置换不过线。  
2. **不是「镜像没接上」**：新扫 CSV 方向列已是 short 语义；问题在 **宇宙变宽后 tip_v1b 候选可分性变差**。  
3. **top-K=10 单变量失败**：AUC/净/p 全面不优于全特征；两边 `best_iteration=1` 说明 early-stop 几乎没学到结构——截断噪音特征救不了。  
4. 与 5×6m 对照：**少币高流动性子集上的正净，在 30 常见币上不复现** → 先前正净仍可能是小样本/币选择伪影（诚实：不能反过来证明 5 币「真有边」，只能说扩后发现级不过门）。

---

## 风险与诚实声明

1. 未动 holdout；未 promote；未改 TP/SL/成本。  
2. 30 币是「常见流动性」主观名单，不是交易量/HV 客观排序；换名单可能改结论——本轮不换第三套宇宙。  
3. 障碍仍为 YOLO 扫时的 TP5/SL2；换障碍须另批。  
4. `best_iteration=1` 使 feature importance 本身极噪；top-K 名单依赖该噪估计。  
5. 扫池与训练可复现；launchd 已 bootout 并 rename disabled。

---

## 是否值得继续 / 下一步（需 Owner）

**本轮（binary）结论**：扩币后 binary 镜像基线 **发现级不过门**；top-K **不值得**。同池 **regression** 已另报正净（见 reg 报告）——说明问题更像「目标函数/筛单哲学」而非「再截特征」。

选项（请选一条再动；与 HANDOFF 回归主线对齐）：

1. **沿回归主线**扩样本 / walkforward（默认建议；勿再开 binary 优化）。  
2. **换障碍**（trend / MA exit / trail）——须 Owner 批；在 regression 同构下做。  
3. **停判断层**转检测 1000 目视门。  
4. （关闭）继续 binary top-K / 再换第三套宇宙 —— **不推荐**。
