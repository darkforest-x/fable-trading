# pad200 切割审计 — Owner「框不对」— 2026-07-22

**结论先说**：不是叠画把 xywh 画成 x1；**是数据 bug**。  
`dense_owner_v13_pad200` bulk 构建时 **`mad_gate=false`**，一律按 `end_incl`（stem=窗末）回算原窗；但 v11 里 **`okx_*` stem 实际是窗起点**。金标框被映射到错窗的 OHLC → Owner 目视「框罩错 K 线」。  
**未**重导出全量数据集、**未**重训、**未** promote。代码已改：MAD 默认开。

---

## 1. 切割步骤（说人话）

输入：`dense_owner_v11` 一张 200 根 K 线金标图 + YOLO 框（`class xc yc w h`，归一化中心点格式）。

1. **读 stem 数字**（如 `okx_AAVE_USDT_SWAP_006660` → idx=6660）→ 在全历史里找到这 200 根是哪一段。  
2. **裁右缘**：看金标框最右压到第几根 bar → 这根叫 `cut`；`cut` **之后**的 K 线全部丢掉（无后文）。  
3. **左补满 200**：新窗 = `[cut-199, cut]`（历史不够就 skip，禁止短窗拉伸）。  
4. **重渲图 + 重写标签**：同一批 bar 的价格上下界，映射到新图的 YOLO xywh；框会靠右，因为窗末就是 cut。

关键公式：

```
# stem → 原窗起点（必须消歧，见下）
win_start = idx - 199     # end_incl：stem=窗末
# 或
win_start = idx           # start：stem=窗起点

cut_local  = max(bar_of(box_right))     # 框右缘 → 原窗内 bar
cut_global = win_start + cut_local
pad_start  = cut_global - 199           # 新窗左端
pad_window = series[pad_start : cut_global]   # 恰好 200 根

# 标签：原框 (b0,b1, price_hi, price_lo) → 新窗 bar (t0,t1) + 新图 y
t0 = (win_start + b0) - pad_start
t1 = (win_start + b1) - pad_start
```

---

## 2. Stem 索引：已知坑 + 本轮确认

| 约定 | 含义 | 谁用 |
|---|---|---|
| `end_incl` | stem = 窗末 bar，`iloc[idx-199:idx+1]` | round8/9 风格（多数非 okx） |
| `start` | stem = 窗起点，`iloc[idx:idx+200]` | **`okx_*` / 更老 pack** |

历史 bug 笔记：`docs/learnings/stem-index-is-window-end-not-start.md`（曾把 end 当 start）。  
反方向同样致命：**把 start 当 end**。

v13 bulk 摘要（`pad200_summary.json`）：

```json
"mad_gate": false,
"win_index_default": "end_incl"
```

→ 有存档 PNG 也不做像素 MAD，**okx_* 全军覆没**。

抽样（okx 87 + 非 okx 90，seed=42）：

| 组 | n | end_incl 与存档 MAD≤5 | bulk 错窗（start 对、end 错） |
|---|---:|---:|---:|
| **okx_*** | 87 | **0%** | **98.9%** |
| 非 okx | 90 | 48.9% | 0% |
| 非 okx both MAD>5 | 90 | — | 51.1%（存档≠当前 kline，属漂移另案） |

全库 pad200 正样本 3947，其中 **okx=1228（31%）** → 约 **~1200 条框在错 K 线上**。

close-corr 门（corr≥0.999）**挡不住**：错窗内部仍自洽（见 stem 笔记「自洽≠对窗」）。

---

## 3. 对照 sample20（Owner 看过的那 20 张）

| 审计 | 张数 | 代表 |
|---|---:|---|
| **BUG_wrong_window** | **7** | #02 PNUT、#05 PEOPLE、#07 LINK、#09 AAVE、#10 APT、#12 PNUT、#13 PNUT |
| ok_end_incl（窗对） | 4 | #01 LUNA、#08 OL、#14 HOME、#17 MORPHO |
| drift_both_high | 6 | #03 ETH、#04 OKB、#06 SUI、#11 ETH、#15 PEPE、#16 WLFI |
| background | 3 | #18–20 |

核对过：

- **叠画**：supervision/xywh→xyxy 正确（`x1=(xc-w/2)*W`）；标签数字与框像素一致。  
- **原图**：`v13_train_sample20/raw/` 与 `datasets/dense_owner_v13_pad200/images/train/` **逐字节相同**（是 pad200 产物，不是抽错集）。  
- **重算**：bulk 路径（无 MAD）与磁盘标签/图 **MAD=0 完全一致** → 磁盘就是错窗产物，不是事后画坏。

纠正对照画廊：

```bash
open analysis/output/v13_train_sample20_corrected/index.html
# 红卡 = 错窗；compare_bulk_vs_mad/ 左=数据集 右=MAD纠正
```

---

## 4. Owner 看到「框不对」的最可能原因

| 假设 | 判定 |
|---|---|
| 叠画把 xc 当 x1 | ❌ 否 |
| 抽错非 pad200 集 | ❌ 否 |
| 标签语义 Owner 不认可（贴右太短/不像密集） | ⚠ 对 **窗正确** 的 4 张仍可能有观感争议，但解释不了「很多」 |
| **切割用错 stem→窗（okx_ 当 end_incl）** | ✅ **主因** |
| 存档与当前 kline 漂移仍强切 | ⚠ 次因（6/20），框几何「自洽但图不对味」 |

---

## 5. 最小修复（已做 / 未做）

**已做（代码）**：`scripts/build_crop_pad200_dataset.py`

- MAD 相对存档 PNG **默认开启**（`--mad-gate` / `--no-mad-gate`）。  
- 文档改为写明：v11 stem **混合**约定，禁止盲信 end_incl。

**未做（需 Owner 点头）**：

- 不自动清空 / 重建 `dense_owner_v13_pad200`。  
- 不重训 v13、不 promote。  

若批准重建，建议：

```bash
# 新目录，勿覆盖正在对照的旧集，除非 Owner 明确要求
PYTHONPATH=. .venv/bin/python scripts/build_crop_pad200_dataset.py \
  --src datasets/dense_owner_v11 \
  --out datasets/dense_owner_v14_pad200 \
  --resume   # MAD 默认 on；16GB 注意 RAM，可配 caffeinate
```

---

## 6. 风险与诚实声明

- 本审计抽样 177 条，不是 3947 全量 MAD；okx 错窗比例外推有抽样误差，但 98.9% + 1228 条量级足以定性。  
- `both_high` 币种即使开 MAD 也会被 skip（MAD>5）——重建后正样本数会少于 3947。  
- 窗正确的样本上，Owner 仍可能觉得「框不像密集启动」——那是金标语义/贴右协议问题，与本 bug 正交。

## 7. 下一步（Owner 决策）

1. 是否批准 **MAD-on 重建 pad200 集**（建议新 tag `v14_pad200`）？  
2. 重建后是否开 **单变量** 再训（仍不自动 promote）？  
3. 漂移币（both_high）是 skip 还是人工审？
