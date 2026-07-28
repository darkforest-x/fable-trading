# tip_v1b 实际 K 线 ~1000 框包（S3，不 promote）

**日期**：2026-07-24  
**Owner 事项**：做空模型训完后，用该模型在真实 K 线上检出约 1000 张，**排除**已用于 short 金标训练集的样本。  
**权重**：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`  
**输出**：`analysis/output/owner_side_short_tip_v1b_detect1000/`  
**脚本**：`scripts/dump_short_tip_detect_sample.py`  
**纪律**：**未** promote / **未**动 holdout / **未**改 ACTIVE / **未**接执行器。

## 一句话

S3 目视包已落地：**1000** 张 tip-edge 检出图，覆盖 **224** 币；与 tip/pretip 金标 **0 碰撞**；框右缘 p50≈**0.997**（贴 tip）。这是 **Owner 审阅材料**，不是晋升门，也不是新训练集自动入库。

## 1. 复现

```bash
# smoke（已跑通 12 张）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/dump_short_tip_detect_sample.py --count 12 --preview 6 --device cpu \
  --out analysis/output/owner_side_short_tip_v1b_detect_smoke

# full 1000（launchd: com.fable.short_tip_detect1000）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/dump_short_tip_detect_sample.py --count 1000 --preview 40 --device cpu \
  --out analysis/output/owner_side_short_tip_v1b_detect1000
```

默认排除：

- `datasets/dense_owner_side_short_tip`
- `datasets/dense_owner_side_short`

协议：200-bar tip 窗、只保留 tip-edge（last 2 bars）、conf≥0.30、信号 `<2026-05-04`。

## 2. 结果

| 项 | 值 |
|---|---:|
| labeled | **1000** |
| tried tip windows | 1176 |
| no tip-edge box | 173 |
| spread gate drop | 0 |
| symbols | **224** |
| train collisions | **0** |
| box right-edge p50 | **0.997** |
| box right-edge p10 | 0.997 |
| wall | ≈1.7 min（CPU predict；scout 另计） |

Top 币（非均匀，有 per-symbol cap）：BARD 12 / MUBARAK 11 / GAS 11 / TRB 10 …

## 3. 审阅入口

| 文件 | 用途 |
|---|---|
| `analysis/output/owner_side_short_tip_v1b_detect1000/index.html` | 前 60 张预览墙 |
| `.../review_sheet.csv` | 填 `owner_keep` / `owner_note` |
| `.../previews/preview_*.png` | 绿框 + 右缘红线 |
| `.../images/train/*.png` | 全量 1000 图 |
| `.../manifest.json` | 机器可读摘要 |

## 4. 解读

1. 检出产率高（1000/1176≈85% 有 tip-edge 框）→ tip_v1b 在 scout 的密 tip 窗上很敢开火；**不等于**这些框都是可交易空头。  
2. 右缘贴边良好，几何上像 tip，不像旧 pretip 中位框。  
3. 排除金标成功（0 collision）——满足 Owner「勿与训练集重复」。  
4. 本包是 **S3 人工闸**，不替代 tip-smoke / 前向 100 / promote。

## 5. 风险与诚实声明

- scout 以 tip 密排优先，分布偏「像密集」的窗，不是成交量均匀抽样。  
- 排除键依赖 stem=`SYMBOL_tipidx`；若金标 stem 解析失败会漏排（本轮 recheck collisions=0）。  
- `exclude_skips=0` 在 scout 日志里偏低，因 exclude 集相对全宇宙 tip 索引稀疏；最终输出仍做了硬碰撞审计。  
- **禁止**把本目录当新 YOLO 训练集直接开训，除非 Owner 填完 review 并另批建集。

## 6. 下一步（需 Owner）

1. 打开 `index.html` / `review_sheet.csv`，抽检并填 keep/note。  
2. 若 keep 率可接受 → 再议是否扩成正式金标或仅作检测辅证。  
3. 仍默认：**不** promote、**不** holdout#8、**不**接 live。

## 7. 产物

- 脚本：`scripts/dump_short_tip_detect_sample.py`
- plist：`scripts/com.fable.short_tip_detect1000.plist`
- 包：`analysis/output/owner_side_short_tip_v1b_detect1000/`
