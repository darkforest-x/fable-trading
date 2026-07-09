# P2-11 E2 — 长段收核 `MAX_DENSE_BARS=24`

**日期**：2026-07-10  
**路径**：偏 B（坏图驱动）  
**纪律**：单变量；不改 x_pad / y_pad / min_bars / merge_gap；不训练；不碰 holdout。

## 坏图驱动

| 图 | 旧 dense 长度 | E2 后 |
|---|---:|---|
| PAXG_USDT_015960 | 74, 35 | **24, 24** |
| PI_USDT_017060 | 59, 28 | **24, 24** |
| ALLO_USDT_014860 | 9, 12 | 9, 12（不变，留给 E4 merge） |
| XRP 短框 | 8 | 8（回归应保持） |

## 改动

```text
MAX_DENSE_BARS = 24  # 新增
# run 更长时：在 run 内取 full_spread 均值最低的连续 24 根
```

`scripts/relabel_yolo_dataset.py` 原地重写 `dense_15m_full` 标签。

## 几何结果

| 指标 | E1 后 (pad6) | **E2 后** | Δ |
|---|---:|---:|---:|
| n_boxes | 7958 | 7958 | 0 |
| box_w_mean | 0.1176 | **0.0792** | −33% |
| share w>0.25 | 0.096 | **0.000** | 清零 |
| PAXG 左框 w | 0.372 | **0.126** | 可见收窄 |

## 对照页

- **红绿对照**：http://127.0.0.1:8643/label_audit_e2_compare.html  
- 普通审计：http://127.0.0.1:8643/label_audit.html  

## 诚实声明

- 这会改变「长横盘是否整段算一个密集对象」的语义：GT 偏向 **最紧核心**，不是整段 consolidation。
- 旧 yolo11s 权重与新 GT 不对齐；**未重训**。
- ALLO 分裂、边缘残框 **本轮未修**（单变量纪律）。
- 主线交易仍不依赖 YOLO。

## 下一步

1. Owner 看 E2 对照页：绿框是否符合「密集核心」。  
2. 认可 → 固定配置重训；不认可 → 调 `MAX_DENSE_BARS`（18/32）仍算同族单变量续做。  
3. 下一变量候选：E4 `MERGE_GAP`（ALLO）或 E3 边缘丢弃。
