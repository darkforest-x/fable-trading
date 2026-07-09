# P2-11 E1 — 收紧 `x_pad_px`（12 → 6）

**日期**：2026-07-10  
**纪律**：单变量；不改 `y_pad_frac` / `MIN_DENSE_BARS` / `MERGE_GAP` / 增强 / conf；不训练；不碰 holdout。

## 动机

Round-1 打标审计（owner 确认）显示系统性 **box_too_wide**（代表：`PAXG_USDT_015960`）。  
smoke3 曾把 `x_pad_px` 从 6 提到 12 以换 mAP IoU 容错；E1 在标签几何上把它收回。

## 改动

| 项 | 前 | 后 |
|---|---:|---:|
| `src/detection/auto_label.X_PAD_PX` | 12 | **6** |
| `Y_PAD_FRAC` | 0.35 | 0.35（未动） |
| 图像 | — | **未重渲** |
| 标签 | 旧 pad=12 文本 | **原地重写**（`scripts/relabel_yolo_dataset.py`） |

新增：

- `scripts/relabel_yolo_dataset.py`：对已有 `SYMBOL_start.png` 用 `make_chart_transform` 重算框
- `src/detection/render.make_chart_transform`：只建坐标系不画图（加速 relabel）
- `tests/test_auto_label_padding.py`

## 复现

```bash
# 代码默认已是 X_PAD_PX=6
PYTHONPATH=. python3 scripts/relabel_yolo_dataset.py --dataset datasets/dense_15m_full
PYTHONPATH=. .venv/bin/python scripts/label_audit.py --seed 20260709
python3 -m pytest tests/test_auto_label_padding.py -q
```

审计页：`http://127.0.0.1:8643/label_audit.html`（seed 20260709）

## 数据集几何结果（dense_15m_full）

| 指标 | pad=12（前） | pad=6（后） | Δ |
|---|---:|---:|---:|
| n_boxes | 7958 | 7958 | 0 |
| box_w_mean | 0.1267 | **0.1176** | −0.0091 |
| box_w_median | 0.0813 | **0.0719** | −0.0094 |
| share w>0.25 | 0.1025 | 0.0959 | −0.66pp |
| share w>0.35 | 0.0574 | 0.0553 | −0.21pp |
| box_h_mean | 0.1209 | 0.1209 | 0 |

理论宽差：`2×(12−6)/1280 = 0.009375`，与 mean/median 降幅一致 → 单变量生效、无意外改框数。

### 问题样例 PAXG_USDT_015960（val，2 框）

| 框 | w (pad12) | w (pad6) |
|---|---:|---:|
| 左（长横盘） | 0.379688 | **0.371875** |
| 右 | 0.189844 | **0.180469** |

宽度收窄符合预期；长横盘本身仍可能被规则判为整段 dense（那是 segment 边界问题，属 E4 merge 或阈值语义，**不在 E1 范围**）。

## 解读

1. E1 成功把 GT 水平 padding 收紧，框数与高度不变 → 单变量干净。
2. 超宽框占比略降，但「长 dense 段 → 大框」根因仍在 segment 定义，不是 pad  alone。
3. **尚未重训**：旧权重在更紧 GT 上的官方 mAP 可能暂时下降（GT 更严）；应用新标签重训后才能判断 mAP/观感收益。
4. 主线交易仍不依赖 YOLO；本实验只服务检测层标签上限。

## 风险与诚实声明

- 旧 smoke3 故意加宽 pad 换 mAP；收回后若不重训，旧 `best.pt` 与新 GT 不对齐。
- relabel 依赖原始 cache 路径与 `start` 索引可复现；本机 7060/7060 成功、0 missing cache。
- 未评估 holdout；未改 conf/IoU 定义。

## 下一步

1. Owner 再看 seed `20260709` 审计页，确认 PAXG/宽框观感是否可接受。
2. 若认可 → 固定配置重训 yolo11s（单独 commit/run），与旧 mAP 同表对照。
3. 若仍嫌宽 → 下一单变量考虑 E3（边缘残框）或 E4（merge），**不要**同轮动 y_pad。
