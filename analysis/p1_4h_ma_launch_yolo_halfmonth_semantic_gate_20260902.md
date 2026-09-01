# P1：4h YOLO 最近半个月因果语义门复扫（2026-09-02）

## 结论先行

Owner 明确回复“批准”后，本轮按同一 Grade-A full40 native-1280 checkpoint 记录为
**4h holdout 使用 #7**。没有重新训练、重新拉行情或调任何阈值；直接冻结上一轮半个月扫描的
1,764 个结构合法框，逐框增加已经在 pre-holdout 15m 验证集通过的因果数值语义门。

结果从 **221 个旧事件降到 34 个事件**，共 28 LONG / 6 SHORT、覆盖 33 个币：

| 口径 | 原结构扫描 | 加锁定语义门 | 变化 |
|---|---:|---:|---:|
| 结构/语义通过框 | 1,764 | **256** | **-85.49%** |
| 去重事件 | 221 | **34** | **-84.62%** |
| LONG / SHORT 事件 | 171 / 50 | **28 / 6** | — |
| 涉及币种 | 182 | **33** | -81.87% |
| 最右端仍成立 | 4 | **2** | SOON SHORT、0G LONG |

这说明 Owner 指出的“均线不密集、K 线离均线过远”不是错觉：旧后处理确实只验证了框的横向
core/post 结构，没有验证原训练形态语义。新门最常拒绝的是**末端六均线间距过宽 970 框**和
**核心六均线总包络过宽 872 框**；其次是核心 K 实体过大 592 框、核心方向进度不符 420 框。

但 34 个仍然只是 **15m 模型跨到 4h 后、又通过数值形态复核的 OOD 研究候选**，不是 4h 精度、
胜率或收益证明，`production_eligible=false`。

![4h 语义门配对结果](../experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate/paired_gate_overview.png)

全部 34 张 1920×1400 原图和每张完整冻结未来 K 线：
[打开交互图库](../experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate/all_global_future_charts.html)。

## 本轮到底改了什么

唯一变量是：在原结构合法框之后增加一层确定性语义判断。

以下内容全部不变：

- 冻结的 274 币宇宙、273 个可用币及每个 CSV 字节；
- 49,064 个 W18/W19 模型输入和 1,764 个原结构框；
- checkpoint SHA-256 `862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838`；
- confidence `0.25`、NMS `0.70`、native imgsz `1280`；
- core4/5、post2–9 和同币 5-bar 事件间距；
- 原预测的方向、confidence、cx/cy/w/h 与输入像素 SHA。

语义门逐框只读取该 W18/W19 右端及之前的 `open/high/low/close`，计算 Pine-RMA ATR14 和
SMA/EMA 20/60/120。每个候选都把 DataFrame 物理截断在 `window_end_i` 再计算：post3、post5
仅在图中已经可见时才检查，未来展示 K 线的使用量为 **0**。

冻结阈值没有因 4h 结果调整：

| 语义 | 门 |
|---|---:|
| 核心六均线总包络 | ≤ 1.50 ATR |
| 核心末端六均线间距 | ≤ 1.10 ATR |
| 核心最大 K 实体 | ≤ 1.20 ATR |
| 核心方向进度 | -0.60～1.30 ATR |
| post1 / post2 方向进度 | ≥ 0.00 / 1.00 ATR |
| 已可见 post3 / post5 | ≥ 1.25 / 1.75 ATR |
| 同方向均线平均斜率 | ≥ 0.03 ATR |
| 任一核心 close 至任一 MA 最小距离 | ≤ 1.00 ATR |
| 核心 close / body 至 MA 包络最大距离 | ≤ 1.90 / 1.50 ATR |

## 数据、血缘与 holdout 记录

| 项目 | 结果 |
|---|---:|
| 扫描端点区间 | 2026-08-18 00:00 ～ 2026-09-01 20:00 CST |
| 已确认 4h 端点 | 90 |
| 冻结宇宙 / 可用 | 274 / 273 |
| 候选涉及币种 | 182 |
| 实际物化 OHLCV 行 | 54,418 |
| 逐币 candle SHA 核验 | 273 / 273 |
| 原输入像素复放 | 1,764 / 1,764 |
| 模型推理 / 网络读取 | 0 / 0 |
| 阈值网格 / 训练 | 0 / 0 |
| checkpoint holdout 消耗 | **#7** |

原冻结源：
`analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1`。源 `summary.json`、
`accepted_candidates.csv`、`universe.json`、`verification.json` 的 SHA 在评分前写入并提交到
`preregistration.json`；273 个 candle SHA 再与源 `summary.fetch_audits` 逐个核对。

## 箱体与事件漏斗

| 层级 | 数量 | 比例 |
|---|---:|---:|
| 原结构框 | 1,764 | 100.00% |
| 语义通过框 | **256** | **14.51%** |
| 语义拒绝框 | 1,508 | 85.49% |
| 原控制事件 | 221 | 100.00% |
| 至少有一框通过的控制事件 | **34** | **15.38%** |
| 对通过框重新按旧 5-bar 规则去重 | **34** | — |

LONG 框通过 245 / 1,463（16.75%），SHORT 仅 11 / 301（3.65%）。这不是 SHORT 更差的收益
证据，而是该 15m 权重和这套冻结语义门在 4h 上表现出明显方向不对称；不得据此改 SHORT 门。

### 拒绝原因（可重叠）

| 谓词 | 失败框 |
|---|---:|
| 末端均线间距 `ma_spread_end` | **970** |
| 核心均线总包络 `ma_envelope` | **872** |
| 核心最大 K 实体 `max_body` | 592 |
| 核心方向进度 `core_progress` | 420 |
| K 实体至 MA 包络 `body_to_ma_envelope` | 217 |
| close 至 MA 包络 `close_to_ma_envelope` | 169 |
| post5 | 166 |
| 均线方向斜率 `ma_slope` | 148 |
| post3 / post2 / post1 | 129 / 89 / 16 |
| 任一 close 至任一 MA 最小距离 | 12 |

同一框可同时失败多项，所以本表不能相加为 1,508。

## 对 Owner 指出案例的直接回答

- **USELESS**：原当前候选共有 8 个框，8/8 同时因“核心均线总包络过宽”和“末端均线间距过宽”
  被拒绝，现在不在当前信号里。
- **LA**：两个原控制事件合计 24 个框，13 个失败方向斜率、11 个失败末端均线间距，24/24
  全部拒绝，现在不在当前信号里。
- **SOON**：2/2 框通过，当前 SHORT；代表框均线总包络 0.974 ATR、末端间距 0.578 ATR、
  close 至 MA 包络最大距离 1.108 ATR、post2 为 +1.412 ATR。
- **0G**：当前 LONG 所属控制事件 15/15 框通过；代表框均线总包络 0.482 ATR、末端间距
  0.447 ATR、close 至 MA 包络最大距离 0.566 ATR、post2 为 +2.849 ATR。

所以最新端点原来的 4 个候选，经语义复核后只保留 SOON 与 0G。

## 34 个事件的时间结构

| 北京日期 | LONG | SHORT | 合计 |
|---|---:|---:|---:|
| 08-18 | 4 | 0 | 4 |
| 08-19 | 1 | 0 | 1 |
| 08-20 | 12 | 1 | 13 |
| 08-21 | 4 | 0 | 4 |
| 08-22 | 4 | 0 | 4 |
| 08-25 | 0 | 1 | 1 |
| 08-29 | 1 | 3 | 4 |
| 08-30 | 1 | 0 | 1 |
| 08-31 | 1 | 0 | 1 |
| 09-01 | 0 | 1 | 1 |
| **合计** | **28** | **6** | **34** |

08-20～08-22 仍占 21 / 34（61.76%），其中 20 个 LONG。语义门去掉了大量明显错误形态，却没有
消除市场状态集中；这批事件仍不能当成 34 个独立、有收益的机会。

## 零假设对照

本轮是检测语义审计，没有 4h Owner Gold 或预注册收益标签，因此 val AUC、胜率、PF、
top-decile 毛/净收益、单特征收益基线，以及同币 × 同时间块 × 同波动桶随机入场对照均按字面
**不适用**。本轮没有编造这些指标。

同等严格的实现层零假设是：固定每一根 K、每个框、模型类别、confidence 与事件归属，只在数值
语义计算中把 LONG/SHORT 反转。

| 配对层级 | 实际方向通过 | 翻转方向通过 | 配对精确双侧 p |
|---|---:|---:|---:|
| 1,764 个结构框 | **256** | 4 | **2.04×10⁻⁷⁰** |
| 221 个控制事件 | **34** | 1 | **2.33×10⁻¹⁰** |

两层都通过预注册的 `p<0.01` 方向一致性门。这证明实现不是无方向的“只筛密集图”，但仍没有证明
34 个候选是 4h 真阳性或有经济价值。

## 全局未来 K 线交付

34 张图全部为 1920×1400：

- 上方主图显示该币冻结快照的全部 4h K 线；
- `FIRST SIGNAL` 是事件第一次收盘可见边界；
- 右侧显示当时未知、现在冻结快照中已经观察到的全部未来，范围 0～89 根；
- 右下角仍是模型实际看到的 W18/W19 输入和原始预测框；
- 图头列出实际语义门的 MA 包络、close-to-MA 和 post2 数值；
- 未来区只用于人工复核，不参与框、confidence、语义门或事件 ID。

图库：
[`all_global_future_charts.html`](../experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate/all_global_future_charts.html)。

## 验证

独立 verifier 从冻结 candle 重新计算：

| 核验 | 结果 |
|---|---:|
| 语义特征与决策复算 | 1,764 / 1,764 PASS |
| 原模型输入像素复放 | 1,764 / 1,764 PASS |
| 通过子集 | 256 / 256 PASS |
| 控制事件配对 | 221 / 221 PASS |
| 处理事件重去重 | 34 / 34 PASS |
| 全局未来原图 SHA | 34 / 34 PASS |
| 未来 K 用于语义门 | 0 |
| 模型推理 / 网络读取 | 0 / 0 |
| 本轮范围测试 | 85 / 85 PASS |

第一次图库命令在写第一张图前因派生事件没有旧 `chart` 字段而失败，staging 自动删除且未产生
半套图片。修复仅把缺省文件名确定为 `order + symbol + side`，没有重算语义门、候选或事件；
前后 builder SHA 与 fix commit 记录在 `delivery_builder_fix.json`。

## 风险与诚实声明

- **仍是 15m → 4h 分布外。** 语义门修掉了形态语义明显不符，不等于训练数据已经覆盖 4h。
- **没有 4h 真值。** 34 个事件不能计算 precision/recall；“通过”只表示满足冻结数值形态。
- **没有经济标签。** 完整未来图很容易诱发事后挑图，本轮没有据未来走势调门或给收益评分。
- **方向不平衡。** LONG 框通过率 16.75%，SHORT 3.65%；先记录，不在本次 holdout 上修。
- **市场状态集中仍在。** 61.76% 事件集中于三天，可能仍有共同 beta 或行情渲染共振。
- **存续偏差仍在。** 宇宙是 09-01 当时仍 live 的合约；下架币不在源快照。
- **本轮是该 checkpoint 的 holdout 使用 #7。** 同一配置不得再因看完图而换阈值重试。
- 未训练、未 promote、未切换 ACTIVE/frozen、未写 forward、未部署、未发 Telegram、未下单。

## 裁决与下一步

本轮裁决为：**接受语义门作为这份冻结 4h 完成态扫描的研究过滤层**；拒绝把 34 个候选升级为
已验证 4h 信号、tip 信号或生产信号。

下一步若要判断“34 个是否真的有效”，必须先由 Owner 决定 4h 真值来源或未来收益标签、TP/SL、
成本和 matched-control 口径；这些都是新的实验变量，本轮没有擅自选择。若要训练真正 4h YOLO，
仍需独立 4h Owner Gold 与时间切分，不能把这 34 个机器筛选结果直接当金标。

## 复现命令

代码与预注册先提交于 `1462abe6b576ba40e3b811b4b5d9949a5e3cff6c`；图库缺省文件名修复为
`08a01552972fa086ffdb029db9069ab572eb1ade`。默认正式目录存在时会拒绝覆盖，复算请使用新目录：

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python scripts/apply_4h_ma_launch_yolo_semantic_gate.py \
  --source analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1 \
  --prereg experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/preregistration.json \
  --out experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate_rerun

PYTHONPATH=. .venv/bin/python scripts/build_4h_yolo_global_future_gallery.py \
  experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate_rerun

PYTHONPATH=. .venv/bin/python scripts/verify_4h_ma_launch_yolo_semantic_gate.py \
  --source analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1 \
  --out experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate_rerun

.venv/bin/python -m pytest -q \
  tests/test_apply_4h_ma_launch_yolo_semantic_gate.py \
  tests/test_build_4h_yolo_global_future_gallery.py \
  tests/test_scan_4h_ma_launch_yolo_latest.py \
  tests/test_scan_4h_ma_launch_yolo_half_month.py \
  tests/test_ma_launch_yolo_semantic_gate.py \
  tests/boundaries/test_layer_imports.py

python3 scripts/md_to_html.py \
  analysis/p1_4h_ma_launch_yolo_halfmonth_semantic_gate_20260902.md \
  --out-dir analysis/html
```
