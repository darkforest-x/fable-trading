# P0 — 局部信号 V2 交接规范：旧管线审计、基线冻结与因果门测量

**日期**：2026-08-07 · **上游**：`YOLO局部信号检测重构_Claude开发交接规范_V1.md`（owner 2026-08-07）
**范围**：只读审计 + 新增审计脚本与测试。**未训练、未读 holdout、未改 ACTIVE、未部署、未下单。**
**HEAD**：`0595dd2798f332dd3ad1698d749af68bf43f165c`（main）

---

## 0. 一句话结论

规范描述的 V2 管线**不是从零开始——它今天凌晨已经在本仓库跑通了一轮**
（`scripts/build_w20_midbox_dataset.py`，docstring 记的就是 "Owner protocol (2026-08-07)"，
产出 `datasets/dense_owner_w20_midbox` 2635 正样本 + 训练权重 + tip 回测）。

但把规范 §12.1 的机器不变量真正跑一遍，**当前数据集 P0 不通过：7 道门只过 3 道**。
失败的三道不是"还没做"，而是三件互相独立的事同时成立：

1. **95.3% 的训练图能看到 decision bar 之后的真实 K**，中位 9 根、最多 25 根 —— 按规范
   §3.3 这属于"生产训练图出现 decision_bar 之后的真实 K"，直接判 P0 失败。它是合法的
   **Stage A**，但目前没有 Stage B，也没有任何东西阻止把 Stage A 的 val 数字当成实盘结论。
2. **split 按 symbol 哈希切，不按时间切**。同一事件不跨集合（这条过了），但 train 与 val
   的时间范围重叠 **397.3 天**，规范 §7.2 要求的时间前后切分完全不存在。
3. **246 张正样本（9.34%）取自 holdout 期（≥2026-05-04）**，其中 209 张在 train。
   按铁律 1，这是训练集直接吃进 holdout。

另有一条**待 owner 裁决的实时事项**：一条 holdout 回测已经排队，会在当前 pre-holdout
回测结束时**自动**启动（见 §8）。

---

## 1. Task 1 — 旧管线映射（LEGACY_PIPELINE_MAP）

2026-08-03 的四层拆分把 L1 迁到了兄弟仓库 `~/yoyo-trading`，本仓库通过
`PYTHONPATH=.:../yoyo-trading` 引用。规范 §10 建议的 `src/local_signal_v2/` 目录**不应新建**——
对应组件已经存在，见铁律 14 与规范 §10 末句"优先复用，不要复制"。

| 规范里的组件 | 实际位置 | 说明 |
|---|---|---|
| K 线数据入口 | `yoyo/data/loader.py`（`list_series` / `load_series`）+ `data/kline_cache`（只读软链）、`data/kline_fetched` | 15m bar |
| 均线计算 | `yoyo/layers/l1_detection/data.py: add_mas` | SMA/EMA 20/60/120 六条 |
| 图像渲染 | `yoyo/layers/l1_detection/render.py: render_chart` | 1280×742 固定；无网格/坐标/文字/信号 overlay；`MIN_REL_SPAN=0.06` 纵向下限 |
| 像素↔bar 映射 | 同上 `make_chart_transform` / `ChartTransform.x_at` / `y_at` | 规范 §17"价格到像素映射可验证"已具备 |
| 旧 200 根标签源 | `datasets/dense_owner_v14_pad200`（`*_pad200` stem）+ `data/golden_pool.json` | owner 手标框 |
| 旧框→bar 还原 | `scripts/build_w20_midbox_dataset.py: resolve_pad_window` | 重渲染 MAD ≤ 5.0 对齐 stored PNG |
| split 规则 | `src/detection/owner_eval.py: split_of` | **sha1(symbol) % VAL_MOD**，非时间切分 |
| 冻结评估尺 | `src/detection/owner_eval.py: is_eval_symbol` + manifest | eval 币种在 w20 构建时被 `Skip("eval_symbol")` 排除 |
| 候选生成 / 扫描 | `yoyo/layers/l1_detection/candidates.py`、`scan.py` | 实盘只扫 tip/tip-1/tip-2（铁律 12） |
| YOLO 训练入口 | `scripts/train_w20_midbox_on_3060.sh` → 3060 上的 `C:\fable\train_dense.py` | 训练一律走 3060 |
| 前向 / paper | `yoyo/layers/l4_execution/`、`data/forward_log.csv`（VPS 唯一写者） | 铁律 9 |
| 层间契约测试 | `~/yoyo-trading/tests/test_layer_boundaries.py`（AST 强制） | 铁律 14 |

---

## 2. Task 2 — 基线冻结

| 项 | 值 |
|---|---|
| commit | `0595dd2798f332dd3ad1698d749af68bf43f165c`（w20 相关脚本尚未提交，见 §9） |
| 旧 200-K 基线权重 | `models/owner_v10_chain.pt` · sha256 `b9a84b5f…cbc7d953` |
| w20 cycle-0 权重 | `analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt` · sha256 `7ad42cf0…0dbf9fe2` |
| w20 hardneg-c1 权重 | `analysis/output/w20_overnight/cycle_hardneg_c1/weights/best.pt` · sha256 `e2e8933e…8f989d18` |
| 正样本 manifest | `datasets/dense_owner_w20_midbox/w20_manifest.json` · sha256 `efabb41b…caeefa9841b007` |
| 空背景 manifest | `…/w20_neg_manifest.json` · sha256 `a7f27bcf…3c00c285ae5e` |
| 训练配置 | `epochs=40 patience=12 batch=8 imgsz=960 optimizer=AdamW lr0=1e-4 model=yolo11s` |
| 增强 | `fliplr=0 flipud=0 mosaic=0 mixup=0 degrees=0 erasing=0`，但 **`hsv_s=0.05 hsv_v=0.05` 非零** |

**指标现状（同表对照）**：

| 模型 | 数据 | val 组成 | mAP50 | 最佳 conf | F1 | P | R | 纯负误火 |
|---|---|---|---|---:|---:|---:|---:|---:|
| w20 cycle-0 | w20_midbox（pos+empty_bg） | 405 正 + 405 负 | 0.2812（ep50/60） | 0.15 | 0.403 | 0.355 | 0.467 | 0.126 |
| w20 hardneg-c1 | + 2300 hard negative | 405 正 + 405 空 + 553 hardneg | 0.2377（early-stop ep18/40，best ep6） | 未评 | — | 0.247 | 0.383 | 未评 |

> **这两行的 mAP 不可直接比较**：hardneg-c1 的 val 集换了（多了 553 张 hard negative），
> 分母不同。规范 §18.8"只汇报 mAP"正是此处的陷阱。

**唯一的因果级证据**（tip replay，`analysis/output/w20_smoke2.json`）：

| 口径 | n | 胜率 | PF | 净 bp | 匹配对照 lift | 置换 p |
|---|---:|---:|---:|---:|---:|---:|
| w20 cycle-0 tip smoke（3 币 / 2026-04-15…04-25） | **11** | 0.182 | 0.266 | **−155.0** | **−241.4 ± 126.3** | 0.509 |

n=11，什么都证不了；但它是目前唯一在盘口口径下的测量，**方向为负**。
val F1 0.403 的 "PASS" 与它不矛盾——两者根本不是同一个任务（见 §6）。

---

## 3. Task 3 / 4 — event schema 与 causal sampler：已有的部分与真实缺口

规范的时间语义（§3.1）与现有 manifest 字段可以**一一对上**，不需要新造 schema：

| 规范字段 | 现有 manifest 字段 | 状态 |
|---|---|---|
| `anchor_bar` | `mid_global`（旧框横向中心） | ✅ 已有 |
| `confirm_delay` | `half` ∈ {2,3}（框对称，右边界 = anchor+half） | ⚠️ 语义等价，但规范 §2.2 要求左 2 右 `confirm_delay`；现为对称，且 half=3 超出规范 `confirm_delays: [1,2]` |
| `decision_bar` | `small_bars[1]` = `mid_global + half` | ✅ 可导出 |
| `visible_end_bar` | `win_start + win_len − 1` | ✅ 可导出 |
| `box_start/end_bar` | `small_bars` | ✅ 已有 |
| `window_start/end_bar` | `win_start` / `win_len` | ✅ 已有 |
| `anchor_x_ratio` | `box_pos_frac` | ✅ 已有 |
| `event_id` | **缺**（`stem` 事实上唯一：2635 stem / 2635 行，每事件 1 crop） | ⚠️ 缺显式字段，但当前无多 crop，故未产生泄漏 |
| `config_hash` / `image_sha256` / `renderer_version` | **缺** | ❌ 规范 §12 要求 |
| `window_end_timestamp`（负样本） | **缺**（负样本 manifest 只有 `win_start`/`win_len`，无时间戳） | ❌ 负样本无法做 holdout 审计 |
| hard negative 的 manifest 行 | **完全缺失**（2300 张图 0 行） | ❌ 见 §5.5 |

**本轮新增（P0 交付物）**：

- `scripts/audit_w20_midbox_causality.py` —— 把规范 §12.1 的不变量做成可跑的门。
- `tests/test_w20_midbox_causality.py` —— **27 passed**。因果算术用合成样本钉死
  （`visible_end ≤ decision` 的边界：window 结束在 decision 当根算 causal，晚一根就不算），
  再对盘上真实 manifest 断言结构门。规范 §14"causal guard 有自动测试"这条现在成立。

---

## 4. P0 七道门（机器可审计，`analysis/output/p0_w20_causal_audit.json`）

| # | 门（规范 §12.1 / §16.1） | 结果 | 数值 |
|---|---|---|---|
| 1 | `visible_end_bar ≤ decision_bar` | ❌ **FAIL** | 2512/2635 违反（95.33%） |
| 2 | `box_end_bar ≤ decision_bar` | ✅ PASS | 0 违反（框右边界恒等于 decision） |
| 3 | 同 event 不跨 split | ✅ PASS | 0 跨集合；每事件 1 crop；symbol 重叠 0 |
| 4 | 时间切分 | ❌ **FAIL** | sha1(symbol) 哈希切；train/val 时间重叠 397.3 天；purge/embargo = 0 bar |
| 5 | 训练集不含 holdout | ❌ **FAIL** | 246 张 ≥2026-05-04（train 209 / val 37），最晚 2026-07-10 |
| 6 | label 不越界 | ✅ PASS | 0/7570 |
| 7 | manifest 与图片守恒 | ❌ **FAIL** | 7570 张图 vs 5270 manifest 行，**2300 张 hard negative 无来源记录** |

**`p0_pass = False`。** 按规范 §14"P0 失败时停止，不训练模型"，**不应在此数据集上继续全量训练**。

---

## 5. 逐条证据

### 5.1 因果性 —— 这批数据是 Stage A，不是 Stage B

未来可见 K（= `visible_end_bar − decision_bar`）分布：

| p0 | p25 | 中位 | p75 | max | 均值 | >0 占比 | ≥5 根占比 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 5 | **9** | 14 | 25 | 9.71 | **95.33%** | 76.09% |

成因在构建脚本本身：窗口起点只要求小框**完整落在窗内**
（`build_w20_midbox_dataset.py:245-250`，`w0_lo = s1 − win_len + 1`），
没有 `window_end ≤ decision` 这一条。这正是规范 §2.4 的 Stage A 定义，本身合法；
问题是**目前只有 Stage A，且它的 val 指标被当作了验收依据**（`MORNING_README.md` 的 "PASS"）。

左侧上下文中位 9 根 —— 一张 24 根的图里，信号前 9 根、信号后 9 根，
**信号后可见的历史和信号前一样多**。

### 5.2 位置随机化 —— 这条做对了

| 桶 | 实际占比 | 规范 §5.2 Stage A 目标 |
|---|---:|---:|
| 左中 [0, 0.35) | 31.5% | 20% |
| 中 [0.35, 0.55) | 25.6% | 35% |
| 中右 [0.55, 0.75) | 23.7% | 30% |
| 右 [0.75, 1] | 19.1% | 15% |

`box_pos_frac` p10=0.16 / 中位 0.50 / p90=0.82，窗口长度 20–30 各 224–265 张近似均匀。
位置确实由 `crop_start_bar` 重采样得到（重渲染真实窗口），**不是**规范 §2.3 禁止的图片平移。
规范 §5.3 要求的两项 shortcut 诊断（内容遮挡测试、分桶 precision）**尚未做**。

### 5.3 split —— 按币切，不按时间切

`split_of` = `sha1(normalized symbol) % VAL_MOD`。好处：147 train 币 / 32 val 币零重叠，
同事件不可能跨集合（门 3 因此通过）。坏处：

- train 时间范围 2025-06-05 → 2026-07-10；val 2025-06-08 → 2026-07-10；**重叠 397.3 天**。
- 加密货币横截面高度共动，同一段行情同时出现在两侧，val 指标必然偏乐观。
- purge/embargo = 0 bar（规范 §7.3 要求 ≥ 最大窗口 + 均线回看，此处应 ≥ 30 + 120 = 150 bar）。

### 5.4 holdout —— 训练集吃进了 246 张

铁律 1 的 holdout 边界是 **2026-05-04**（`scripts/diag_barrier_grid.py:50` 等 6 处脚本里都是这个常量）。
w20 正样本最晚 `end_time` 是 **2026-07-10 18:30 UTC**，超界 246 张（9.34%），train 侧 209 张。

需要说明的是：**这是继承来的，不是 w20 新引入的。** 源头是
`datasets/dense_owner_v14_pad200` 的 owner 手标框本身覆盖到 2026-07；
HANDOFF.md:1010 早就登记过这个隐患——
> "检测层训练无时间切分（~2.5% 标注图落在 accept 窗口内）是结构性弱点；v10 应登记'检测层训练图截止 2026-05-04'实验"

那个实验一直没做。w20 把比例从 ~2.5% 放大到 **9.34%**。

### 5.5 manifest 守恒 —— 2300 张 hard negative 没有来源记录

`add_w20_hardneg_pack.py` 写图和空标签，但只写了一个汇总 json，没写 manifest 行：

| split | images | pos | empty_bg | hardneg | manifest 行 |
|---|---:|---:|---:|---:|---:|
| train | 6207 | 2230 | 2230 | 1747 | 4460 |
| val | 1363 | 405 | 405 | 553 | 810 |

其中 `hardneg_weak` 那 800 张来自 5d gallery 的弱触发（conf ∈ [0.15, 0.30)），
脚本自己的 docstring 写明是 **"Heuristic FP; not owner-labeled"** ——
规范 §6.3 允许"旧模型高置信度误报"作为 hard negative，但这批是**低**置信度触发，
且没有人工确认它们真的不是信号。把它们钉成负类，等于用模型自己的犹豫训练模型。

---

## 6. 与既有测量的对照（这一节决定 V2 值不值得做）

规范 §1.2 的问题定义（全局图噪音大、框太大、位置偏差、事后信息泄漏）与本仓库
2026-08-05 已经量出来的数字**高度一致**，而且那批数字比规范假设的更严重：

**（a）未来上下文依赖曲线**（`reports/future_dependency_report_20260805.md`，同一批 100 信号、同渲染、同 conf/iou，唯一变量是信号右侧留多少根 K）：

| 右侧未来 K | v10_chain 复现率 | v12_htip 复现率 |
|---:|---:|---:|
| 0（盘口） | 10% | 9% |
| 20 | 39% | 41% |
| 99 | 62% | 72% |

conf 全程稳在 0.45–0.55 —— 失效模式不是"犹豫"，是**看不见**。

**（b）监督目标本身就不是盘口对象**：499 个 ⭐标杆里只有 **2 个**画在盘口，中位可见未来 **97 根**
（`docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md`）。

**（c）H-TIP 重训不能修**：v12 专为盘口触发重训，tip 9% vs 未修复 v10 的 10%；
归一化后反而更低（12.5% vs 16.1%）。v13/v14/v15/v16 四次失败（V5 蓝图约束 C4）。

**这三条合起来说明什么**：w20 的中位 9 根未来上下文，落在上面那条曲线的
10%→39% 区间的左半段。**规范的核心假设（缩小到 20–30 根 + 位置随机化能解决问题）
在 Stage A 上是没被这些证据否定的，但 Stage B 才是它要证明的东西，而 Stage B 目前不存在。**
现有 w20 val F1 0.403 与 tip smoke PF 0.266 之间的落差，与上表 39% vs 10% 的落差是同一个现象。

---

## 7. 规范与仓库铁律的冲突（需 owner 裁决）

| # | 冲突 | 说明 |
|---|---|---|
| C-1 | 规范 §2.4 Stage A（位置 0.20–0.85 随机、允许未来 K） **vs** 铁律 12"凡只能产出事后信号的路径……非盘口分布数据集一律不得存在" | 字面冲突。w20 数据集就是这样的数据集，且已建成。规范用"Stage A 只能作表征预训练、不得宣称实时效果"来约束它——**如果 owner 采纳规范，铁律 12 需要显式修订为"Stage A 允许存在，但晋升唯一门仍是真 tip 金标 + tip-smoke"**，否则两份文件互相否定，下一个会话必然踩坑 |
| C-2 | 规范 §10 建议新建 `src/local_signal_v2/` **vs** 铁律 14"新代码一律写进 `yoyo/`；旧 `src/` 是转发壳" | 建议不新建目录，缺口以脚本 + `yoyo/layers/l1_detection/` 内的采样器补齐 |
| C-3 | 规范 §19 要求 `reports/P0_AUDIT.md` **vs** CLAUDE.md 质量标准要求 `analysis/pXX_report.md` + `analysis/html/` | 本报告按仓库规范落在 `analysis/`；规范的文件名映射记在这里 |
| C-4 | 铁律 5"hsv 全关" **vs** 3060 `train_dense.py` 实际 `hsv_s=0.05 hsv_v=0.05` | 继承自旧训练器，非 w20 引入。红绿 K 线语义靠色相，`hsv_h=0` 保住了要害，但严格讲违反铁律 5 |
| C-5 | 铁律 1 holdout 记账 **vs** 已排队的 holdout 回测 | 见 §8 |

---

## 8. 正在跑 / 刚结束的作业（截至 2026-08-07 12:45 CST）

| 作业 | 状态 |
|---|---|
| `backtest_w20_midbox_tip.py --start 2026-03-01 --end 2026-05-03`（PID 99115） | **运行中**，92/311 币，已 3364 笔。按当前速率还需约 10 小时 |
| ⚠️ `backtest_w20_midbox_tip.py --start 2026-05-04 --end 2026-07-01 --allow-holdout --holdout-n 1`（PID 99116 守候） | **已排队，会在上一条结束时自动启动**。这是一次 holdout 消耗 |
| `scan_w20_midbox_5d_gallery.py` | 已完成，`analysis/output/w20_midbox_5d_gallery/index.html` |
| 3060 `owner_w20_midbox_hardneg_c1` | **已结束**：patience=12 触发早停，ep18/40，best≈ep6，末次 val P 0.247 / R 0.383 / mAP50 0.238 |
| `overnight_w20_midbox.py` 轮询器 | **已死**（Mac 上 0 进程，log 停在 08:03）。cycle_hardneg_c1 的本地 results.csv 是 12:35 的快照 |

> **关于那条 holdout 回测**：它是今天 08:22 由 owner 或上一会话挂上的，命令里带了
> `--holdout-n 1`，说明当时就打算按"该配置第 1 次消耗"记账。本会话**没有动它**（既没杀也没催）。
> 按铁律 1，它需要 owner 在对话里明确批准 + 报告里记录第 N 次消耗。请裁决：**放行还是掐掉。**
> 参考：HANDOFF.md:416 记录全局 holdout 消耗此前已到 N=10。

---

## 9. 风险与诚实声明

1. **本报告没有做规范 §15 的 P1 对照实验**，因为 P0 未通过，规范 §14 与 §18.10 都禁止在此之前全量训练。
   所以"20–30 根局部窗是否优于 200 根"这个问题，本报告**没有回答，也不该在这里回答**。
2. **门 1（因果性）的判定依赖一个映射假设**：把 `half` 当作 `confirm_delay`。
   如果 owner 的本意是"框对称、decision 另算"，那 decision_bar 应另立字段，
   未来 K 的数量会变，但**不会变成 0**——窗口右端中位比框右端晚 9 根，这是构建规则的直接后果。
3. **hardneg-c1 的 P/R 与 cycle-0 不可比**（val 集不同）。§2 表里已标注，不要跨行读。
4. **tip smoke 只有 11 笔**。它不构成"w20 失败"的证据，只构成"w20 尚未被证明"的证据。
   正在跑的 311 币 pre-holdout 回测才有裁决力。
5. **246 张 holdout 样本已经进过训练**（cycle-0 与 hardneg-c1 都用了这份数据）。
   这意味着这两个权重在 ≥2026-05-04 区间上的任何评估都**不是干净的样本外**——
   包括 §8 里排队的那条 holdout 回测。这一点必须在放行前想清楚。
6. w20 相关的 9 个脚本 + 数据集 + 权重目前**全部未提交**（`git status` 未跟踪）。
   本报告引用的 sha256 是当下磁盘状态，未入版本库。
7. 本会话未训练、未读 holdout、未改 ACTIVE/frozen、未部署、未下单、未 promote、未清 forward_log。

---

## 10. 下一步选项（★ = 需要 owner 决策）

**★ D-0：那条排队的 holdout 回测，放行还是掐掉。**
建议**掐掉**——理由见 §9.5：训练集已含 246 张 holdout 样本，这次消耗买不到干净的样本外结论。
掐掉的命令：`kill 99116`（只杀守候进程，不影响正在跑的 pre-holdout 回测）。

**★ D-1：铁律 12 与规范 Stage A 的关系（C-1）。** 二选一：
(a) 采纳规范，把铁律 12 修订为"允许 Stage A 存在但不得作为晋升依据"；
(b) 维持铁律 12，则 w20 现有数据集必须整体重建为纯 Stage B。
**建议 (a)** —— Stage A 的表征价值有 §6 的证据支持，禁止它等于禁止唯一还没被证伪的方向。

**★ D-2：holdout 边界是否强制施加到检测层训练图。**
HANDOFF.md:1010 登记过这个实验但从未执行。建议把 `end_time < 2026-05-04` 做成构建期硬过滤
（代价：正样本 2635 → 2389，−9.3%）。

**D-3（不需决策，P0 补齐项，按优先级）**：
1. 给 `build_w20_midbox_dataset.py` 加 `--causal` 模式：`w0_hi = min(s0, decision − win_len + 1)`，
   产出 Stage B 数据集；同一批 event、同一 seed，与 Stage A 并存不覆盖。
2. split 改时间切分 + purge ≥150 bar；保留 symbol 分组以免同事件跨集合。
3. `add_w20_hardneg_pack.py` 补写 manifest 行（含 `window_end_timestamp`、`hard_negative_type`）。
4. 负样本 manifest 补 `end_time`，使 holdout 审计能覆盖全量。
5. manifest 补 `event_id` / `config_hash` / `image_sha256` / `renderer_version`。
6. 规范 §5.3 的两项 shortcut 诊断（内容遮挡、分桶 precision）。
7. 把 `audit_w20_midbox_causality.py` 挂进构建脚本收尾，让数据集不通过门就不落盘。

**P1（P0 通过后才开）**：规范 §15 的 A/B1/B2/C1/C2/C3 六臂矩阵，统一 event 集与时间切分，
主指标 event precision / FP per 1000 bars，**不看 mAP**。

---

## 11. 复现命令

```bash
# 1. 因果 / split / holdout / 守恒 审计（只读，约 20 秒）
cd /Users/zhangzc/fable-trading
.venv/bin/python scripts/audit_w20_midbox_causality.py \
    --out analysis/output/p0_w20_causal_audit.json

# 2. 因果 guard 单元测试（27 passed）
.venv/bin/python -m pytest tests/test_w20_midbox_causality.py -q

# 3. 本报告转 HTML（交付给 owner 的是 HTML）
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \
    analysis/p0_local_signal_v2_audit_20260807.md --out-dir analysis/html
```

数据集与权重的重建命令（**本轮未执行，仅登记**）：

```bash
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_w20_midbox_dataset.py --limit 0
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/add_w20_midbox_negatives.py --ratio 1.0
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/add_w20_hardneg_pack.py --n-dense 1500 --n-weak 800
bash scripts/train_w20_midbox_on_3060.sh --name owner_w20_midbox_cold --epochs 40 --patience 12
```

---

**P0 结论：不通过（3/7 门通过，4 门失败）。按规范 §14 停在此处，等待 owner 对 D-0 / D-1 / D-2 的裁决。**
