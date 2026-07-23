# Owner 多空人工审阅包

> 闸门：你手动区分 **long / short / skip**，再跑分边特征 + 因果 base rate。
> 本包**不会**替你填方向。

## 金标来源（读这一段就够）

| 项 | 值 |
|---|---|
| 主源 | `datasets/_deprecated_pretip/dense_owner_v11` |
| 图总数 | 11730（含空标背景） |
| **独立 owner 正框** | **332**（YOLO 仅 class+xywh，**无 side 字段**） |
| 优先小样 | **332**（分层抽样，默认先标这个） |
| MAD/错窗等跳过 | `{"mad_fail": 50, "no_series": 9, "cut_oob": 1, "holdout_cut": 34, "no_win": 1}` |

「一万多」若含 tip clone（如 v12 htip），那是克隆集；**审阅请基于本包的独立 owner 框**。

## 怎么开始标（推荐）

在仓库根目录：

```bash
PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py
```

浏览器打开终端打印的地址（默认 http://127.0.0.1:8765/gallery.html）。

### 快捷键

| 键 | 含义 |
|---|---|
| **L** | long（做多手法） |
| **S** | short（做空手法） |
| **K** 或 **X** | skip（看不清 / 不作为本轮样本） |
| **N** | 下一张 |
| **P** | 上一张 |
| **U** | 只看未标注 |
| **1** | 小样模式 |
| **2** | 全量模式 |

点按钮与按键等价。标注后**自动跳下一张**，并立刻写入：

- `reviews.jsonl`（追加）
- `review_sheet.csv` 的 `owner_side` / `owner_note` 列

刷新不丢（服务端落盘 + 浏览器 localStorage 备份）。

### 离线打开（不推荐）

`open analysis/output/owner_side_review/gallery.html` 也能看图，但**无法写盘**；
只能用页内「导出 CSV」下载进度。请优先用上面的 serve 脚本。

## 填完后跑什么

```bash
PYTHONPATH=. .venv/bin/python scripts/owner_side_feature_verdict.py \
    --sheet analysis/output/owner_side_review/review_sheet.csv \
    --tag owner_side_feature_verdict
```

未填任何 `owner_side` 时脚本会拒绝运行。

成功线（写在下游 docstring）：**某一边** train 段因果规则 PF@maker ≥ **1.3**
才算该方向有可部署增量。禁止 holdout；只扫 `<2026-05-04`。

## 诚实陷阱

1. **事后 hindsight**：若你看着框**后面的走势**再标多空，标签会被污染；
   尽量按「触发当下能判断的方向」标。最终裁判仍是因果 base rate，不是你的感觉。
2. 诊断列（`spread_chg8` 等）只读，**不要**被它们带节奏去填 side。
3. `skip` 不进下游正样本；宁可 skip 也不要瞎标。
