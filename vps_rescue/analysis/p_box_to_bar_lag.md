# 框→bar 滞后机制（EDEN / KORU）— 2026-07-21

> 只读分析 + 最小复现。不改生产默认、不部署、不动 holdout。  
> 原始打印：`analysis/output/box_to_bar_repro.txt`

## 结论先行

**根因是几何语义错位，不是映射 bug。**

`right_edge_to_bar` 与 `ChartTransform.x_at` 往返 **200/200 精确对齐**；非正方形画布
（1280×742）与 12px margin 都被 transform 吃掉，无 off-by-one。模型在 tip 窗里框的是
「已带右侧后文的启动区」，右缘落在启动 bar（窗中段或 tip 前数根），管道忠实地把该右缘
记成 `signal_time` → 相对最新 bar 的偏移直接变成 detect lag，撞上 30min 三门。

| 案例 | 复现 tip | 框 right_norm | bar_in_win | 相对 tip | conf | score | 与账本 |
|------|----------|---------------|------------|----------|------|-------|--------|
| **KORU** | 04:30（detect 脉冲） | **0.9747** | **196**/199 | **3 bar / 45min** | 0.501 | **0.032329**（=VPS） | lag 62.4m |
| KORU | 03:45（信号 tip） | — | — | — | — | — | **0 框** |
| KORU | 04:00 / 04:15 | — | — | — | — | — | **0 框**（+1/+2 仍无） |
| **EDEN** | 13:15（约首次可检） | 0.8167 | **164**/199 | **35 bar / 525min** | 0.491 | 0.024715（过阈） | lag≈527m |
| EDEN | 04:45（信号 tip） | 0.5180 | **103**/199 | 96 bar（昨日形态） | 0.413 | — | 无当日信号框 |

KORU 是「近右缘但仍旧 3 根」的紧凑版；EDEN 是「窗中段 / 昨日簇」的夸张版。同一机制。

## 1. 几何假设（精读）

### 公式

```text
right_px = (cx + w/2) * tf.width
idx      = round( (right_px - tf.left) / tf.plot_w * (tf.n_bars - 1) )
signal_i = window_start + clamp(idx, 0, n_bars-1)
```

与渲染侧 `x_at(i) = left + i/(n_bars-1)*plot_w` 互逆。画布 **非正方形** 不影响 x 映射
（y 只用于画线，不进 bar 索引）。

### 窗调度

| mode | 窗 | 备注 |
|------|----|------|
| `tip` | 仅 `last_start = len-200` | 右缘=最新收盘 bar |
| `live` | tip + tip−1 + tip−2 + stride 回看，≤6 | 主线；注释已写明「框可映射到窗内任意 bar」 |
| `full` | stride 全史 | 离线建池 |

### 框过滤（`scan_series_with_yolo`）

1. `signal_i` 越界 → 丢  
2. **`mode != "live"` 且 `signal_i + 1 >= len(frame)` → 丢**（tip/full 要求入场 bar 已存在 → **tip bar 自身永不入账**）  
3. `signal_i < start_from_i` → 丢（FORWARD_START）  
4. `min_gap` 去重  

要点：**没有任何「必须靠近窗右缘」的过滤**。只要右缘映射到 tip−k（k≥1），tip mode 也会 KEEP。

## 2. 复现命令

```bash
# VPS 账本行（只读）
ssh root@103.214.174.58 'python3 -c "...KORU/EDEN from data/forward_log.csv..."'
# 本机：K 线临时拷到 /tmp（不写 data/kline_fetched），权重 models/owner_best.pt
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  # （见本会话内联脚本；产物 box_to_bar_repro.txt）
```

VPS 行（2026-07-21）：

- `KORU_USDT_SWAP` sig=`2026-07-21 03:45` det=`04:47:23Z` **lag=62.4m** score=0.03233 signal_i=1996  
- 影子 tip-only 同信号 **lag=72.6m**（无 ≤30m 行）  
- EDEN 两行仍为 07-19 事后（527m / 587m）

新鲜度算术：`offset_bars×15 + ~17min 脉冲 ≤ 30` → **只有 offset=0 可靠**；offset=1 已 borderline（~32）。  
KORU offset=3 → 45+17≈62，与账本一致。

## 3. 三个问题的答案

### 映射是「正确但语义错」还是 bug？

**正确但语义错。** 往返测试无误差；YOLO 归一化框 × `ChartTransform` 一致。语义上：右缘被定义为「信号 bar」，但模型画的是「启动形态的空间范围」——有后文时更容易框住，右缘停在启动 bar 而非当前 tip。

### tip mode 为何仍产出 lag≫30？

1. tip 窗照样跑 `right_edge_to_bar` → 可映射到 tip−3（KORU）或 tip−35（EDEN）。  
2. tip 过滤只挡 **tip 自身**，不挡 tip−k。  
3. 信号 tip / tip+1 / tip+2 上 KORU **零框**；要等 tip+3 才出现右缘落在信号 bar 的框 → 结构性 ≥45min 信号龄 + 脉冲 → 过不了 30 门。  
4. 影子 tip-only 最小 lag 仍 72m（KORU），印证「换成 tip 调度」不够。

### tip_hit 0.925 vs tip_fresh=0

离线 tip_hit 是 **金标重渲**（窗末=真信号 bar）协议命中率；实盘问的是「形态形成当下 tip 有没有框」。二者不等价——KORU/EDEN 复现是直接反例。

## 4. 修复选项（只提案）

| 选项 | 做法 | tip_hit（离线） | 新鲜度 / tip_fresh | 误报 | Owner 批？ |
|------|------|-----------------|-------------------|------|-----------|
| **A** 右缘最后 N% 才入账 | 例 `right_norm ≥ 0.95` | 几乎不变（评估协议未改） | **不够**：KORU 已是 97.5% 仍 lag 62m | 降一点中段假框 | 要（改入账集合） |
| **A′**（推荐工程止血）最后 N **根 bar** | 例 `bar_in_win ≥ 198`（offset≤1）或 `≥199`（仅 tip） | 不变 | **直接抬 tip_fresh 口径**；KORU/EDEN 类事后行不再进账本 | 召回骤降（过阈但非 tip 全丢） | **要**（等于收紧可交易定义） |
| **B** tip 窗强制 `signal_i = tip` | 忽略映射 | 与 tip_hit 协议更「像」但假 | 表面 tip_fresh↑ | **高**：窗内任意旧形态框都会变成「当前 tip 信号」，特征/标签错位 | **要**；不建议作默认 |
| **C1** 训练侧继续 tip 分布 | 更多「无后文」金标 / 难例 | tip_hit 已高；目标改 **live tip 出生率** | 治本；慢 | 低（若标对） | 要（数据/训练实验） |
| **C2** tip mode 允许 tip bar | 与 live 对齐，去掉 `tip_needs_entry_bar` | 无 | 仅当模型真在 tip 开火时有用；不修中段框 | 低 | 要（改 tip/影子语义） |
| **C3** 主线改 tip-only 调度 | 减事后补账噪音 | 无 | 不抬出生率（KORU tip 窗照样 62m） | 低 | 要 |

**推荐顺序**：先 **A′（offset≤0 或 ≤1）** 作入账硬门（与 30min 预算同构），消灭「有过阈、无新鲜」的假进度；并行 **C1** 才是提高真 tip 密度的路径。**不要上 B。** A（纯像素%）单独上线解决不了 KORU。

A′ 伪代码（默认关闭，需 owner 批才接主线）：

```python
MAX_TIP_OFFSET = 0  # or 1; None = 现状
if MAX_TIP_OFFSET is not None and (window - 1 - bar_in_win) > MAX_TIP_OFFSET:
    continue  # drop mid-window / near-right-but-stale boxes
```

## 5. 风险与诚实声明

- 本地 K 线从 VPS 只读拷到 `/tmp`，未写入 `data/kline_fetched`。  
- KORU 复现 tip=04:30 与 det=04:47 对齐；score 与 VPS 逐位一致，排除「本机权重/特征漂移」。  
- EDEN `first_hit` tip 取 13:15 为既有诊断近似，不是重新跑满 80-bar 扫描；机制已与 `diag_detect_lag_eden.json` / learning 一致。  
- 本报告**不**声称 A′ 之后 tip_fresh 会大于 0——只声称事后行会少进账；真 tip 密度仍取决于模型。  
- holdout 未触碰。

## 6. 下一步（需 owner）

1. 是否批准 **A′** 入账硬门（N=0 或 1）？未批前不改 VPS / 不接主线默认。  
2. 真金：在 tip 路径打通前是否挂起自动开仓——另议（纪律 11）。  
3. C1 训练议程与 A′ 可并行，互不替代。
