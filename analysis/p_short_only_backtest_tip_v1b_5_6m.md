# SHORT 回测：tip_v1b × 5 流动性币 × 6m（pre-holdout）

**日期**：2026-07-24  
**性质**：Owner 要求最快出 SHORT 关键数字的发现级回测；**非**晋升、**非** holdout、**非**实盘门。  
**检测器**：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`（未 promote）  
**池**：`data/judgment_yolo_owner_side_short_5_6m.csv`  
**训练 tag（Owner 指定）**：`p2b_yolo_owner_side_short_5_6m`  
**指标**：`analysis/output/p2b_yolo_owner_side_short_5_6m_metrics.json`（同内容副本 tag `…_tip_v1b_5_6m` 亦有）  
**扫墙钟**：首启 07:57:08Z → finalize 08:02:51Z ≈ **5.7 min**（含中途中断/resume；单币纯扫 ≈20s）

## 一句话结论

tip_v1b short YOLO 在 **5 币 × [2025-11-04, 2026-05-04)** 窗上训出 val AUC **0.599**、置换 **p≈0.009**、top-decile（n=24）扣 0.2% 后净收益 **+0.062%**——数字方向对，但 **val 仅 248 / top-decile 仅 24**，经济结论极脆，**不能**当确认级或晋升依据。规则 short 对照（非 tip_v1b 主链）见 §5。

## 1. 复现命令

```bash
# 币单（流动性 5：BTC/ETH/SOL/DOGE/XRP）
cat analysis/output/yolo_short_5_6m_symbols.txt

# chunked resume 扫（workers=1；6m；<holdout）
CHUNK_SERIES=1 \
  OUT=data/judgment_yolo_owner_side_short_5_6m.csv \
  SYMBOLS_FILE=analysis/output/yolo_short_5_6m_symbols.txt \
  MONTHS=6 END_BEFORE=2026-05-04 \
  LOG=analysis/output/yolo_owner_side_short_tip_v1b_5_6m_scan.log \
  PIDFILE=analysis/output/yolo_owner_side_short_tip_v1b_5_6m_scan.pid \
  WEIGHTS=runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt \
  bash scripts/run_yolo_short_pool_chunked.sh

# 训练（禁止 --eval-holdout）
PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_5_6m.csv \
  --tag p2b_yolo_owner_side_short_5_6m
```


半成品路径（本轮 resume 用过）：

| 文件 | 用途 |
|---|---|
| `data/judgment_yolo_owner_side_short_5_6m.csv` | 候选 CSV（checkpoint append） |
| `data/judgment_yolo_owner_side_short_5_6m.csv.done_symbols` | 已完成币 |
| `analysis/output/yolo_short_5_6m_symbols.txt` | 5 币名单 |
| `analysis/output/yolo_owner_side_short_tip_v1b_5_6m_scan.log` | 扫日志 |
| `analysis/output/SHORT_5_6M_PILOT.lock` | 防误杀锁 |

## 2. 数据统计

| 项 | 值 |
|---|---|
| 权重 | tip_v1b `best.pt` |
| side | short only |
| 信号窗 | `[2025-11-04, 2026-05-04)`（`--months 6 --end-before 2026-05-04`） |
| holdout 泄漏 | **0**（max signal_time = 2026-05-03 17:30） |
| 候选总数 | **1240** |
| 正类率 | **0.296** |
| outcome | tp 367 / sl 731 / timeout 142 |

分币：

| symbol | n | pos_rate |
|---|---:|---:|
| BTC_USDT_SWAP | 224 | 0.308 |
| ETH_USDT_SWAP | 236 | 0.297 |
| SOL_USDT_SWAP | 263 | 0.281 |
| DOGE_USDT_SWAP | 259 | 0.309 |
| XRP_USDT_SWAP | 258 | 0.287 |

时间切分（train 默认不加 holdout）：

| split | n | range |
|---|---:|---|
| train | 983 | 2025-11-04 → 2026-03-26 |
| val | 248 | 2026-03-27 → 2026-05-03 |
| holdout | 0 | （窗内无 ≥2026-05-04） |

## 3. 结果表（必报指标）

| 指标 | tip_v1b YOLO 5×6m | 规则 strict short（对照） | 规则 expanded short（对照） |
|---|---:|---:|---:|
| val n | 248 | 1438 | 5896 |
| val AUC | **0.599** | 0.533 | 0.599 |
| 置换 p | **0.009** | 0.023 | 0.001 |
| top-decile n | **24** | 143 | 589 |
| top-decile 毛收益 | **+0.262%** | −0.006% | +0.263% |
| top-decile 净（−0.2%） | **+0.062%** | −0.206% | +0.063% |
| top-decile 胜率 | **37.5%** | 31.5% | 46.2% |
| 全体 mean net | −0.141% | −0.244% | −0.115% |
| 单特征基线 top-decile 净 | −0.028% | −0.390% | +0.145% |
| best_iteration | 5 | 12 | 40 |

裁决口径提醒：成功标准是 **top-decile 净收益为正且置换 p&lt;0.01**；AUC 仅参考。本池两项表面上过线，但 sample 太薄。

## 4. 解读

- **扫**：死在 1/5（仅 BTC）是 Cursor 会话杀进程；`--resume` + `.done_symbols` + chunk=1 可续；本机 6m 窗约 **~20s/币**，远快于全历史。
- **统计边**：置换 p≈0.009、AUC≈0.60、优于 ma_spread 基线（基线 top-decile 净为负）→ 分数排序有一点真信息。
- **经济边**：top-decile 净仅 **+6.2bp**，且 **n=24**；阈值 0.4–0.7 全 0 信号（模型概率整体偏低），说明可交易带未成形。
- **early-stop=5**：极早停，过拟合风险与不稳定并存；扩大币池/窗或加样本前不要外推。
- **与规则对照**：点数上接近 **expanded 规则 short**（同量级净 +6bp），但规则池 val 厚一个数量级；**strict 规则 short 更差**（净负）。对照池是规则候选，**不是** tip_v1b 检测主链。

## 5. 规则 short 对照（标非 tip_v1b 主链）

已有产物（本轮未重训）：

- `data/judgment_dataset_v2_strict_short.csv` → `analysis/output/p2b_v2_strict_short_metrics.json`
- `data/judgment_dataset_v2_expanded_short.csv` → `analysis/output/p2b_v2_expanded_short_metrics.json`

用途：样本不足或 YOLO 池太薄时作量级参考。**不得**把规则池数字写成 tip_v1b 主链结论。

## 6. 风险与诚实声明

1. **未** promote / **未**改 ACTIVE / **未**动 holdout / **未** commit。
2. val top-decile **n=24** → 任何「赚钱」叙事都是发现级噪声级；确认级仍只认前向新鲜 100 笔。
3. 5 币流动性池 ≠ 全宇宙；高波 10 币 6m 试点（`10hv_6m`）是另一打包实验，勿混表。
4. tip_v1b tip-smoke 19/27 是检测器辅证，与本判断层数字独立；本报告不构成晋升。
5. 扫描中途进程曾被 Cursor 会话带走；最终靠 resume + finalize 收口——运维脆弱性仍在。

## 7. 下一步（需 Owner 决策）

1. **接受本表为发现级**，同窗扩到更多流动性币再训（仍无 holdout、**无** HV）？
2. 规则 expanded short 对照是否值得单独做 short-only 优化扫参（非 tip_v1b）？
3. **默认建议**：不 promote；Owner 已表示不管 HV——优先扩样本量（币数），再谈经济边。
