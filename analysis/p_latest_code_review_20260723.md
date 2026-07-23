# 最新代码审查 — 2026-07-23

**范围**：`main` 近几天（约 07-22～07-23）代码向改动；对照铁律 1–6 + 实盘纪律 7–12。  
**方法**：`git log -20`、主题 commit 阅读、关键脚本逐文件读。  
**不做**：开训、大重构、promote、动 holdout。

## 0. 仓库快照

| 项 | 值 |
|---|---|
| HEAD | `de3d40a` base-rate verdict… |
| 分支 | `main`，相对 `origin/main`：**0 ahead / 0 behind** |
| 工作区 | **干净**（审查时无未提交改动） |
| 含义 | 无「未 push 私货」；以下全是已上 remote 的近两日代码 |

近 20 条主线是：base-rate 证伪 → v16 holdout 证伪 → real-tip 采集引擎 → tip-replay 回测/看板 → 教义 12 清事后路径 → v16 tipuni/3060 → pad200 MAD / v15 tipval（多数已归档）。

---

## 总判（先说人话）

近两天代码方向**对**：把「事后金标 / 回看窗 / 自家 val」从主路径清掉，换成盘口 tip-replay + 真 tip 采集；**没有** promote，实盘诚实 `detector=none`。

但栈已被证伪：v16 检测 holdout PF 0.78 亏；v11 判断在 tip 上反预测。当前正确姿势是空转攒 v17 数据，不是继续拧 pad200。

主要风险不在「又 promote 了」，而在：

1. **文档多层叠写**（顶部真相 vs 中段「主线仍 v12 / holdout 待触发」互相打架）；
2. **默认权重仍指向已删的 `owner_best.pt`**（主脉冲有兜底，旁路脚本会炸）；
3. **v17 采集/审阅包有几个半成品边角**（空计数、画廊路径、重写 sheet）；
4. **holdout #6 的发现级闸门 JSON 缺失**（过程记账不完整，结论本身已写进报告）。

---

## 1. 按模块（代码向）

### 1.1 真 tip 采集（v17 数据引擎）

**改了什么**

- `scripts/collect_real_tips_pulse.py`：每脉冲旁路；规则密集 tip（cap 10）+ 空背景负样本（8）；`BUDGET_SEC=120`；只写 `data/real_tip_collect/`；无 YOLO。
- `scripts/build_real_tip_review_pack.py`：manifest → 画廊 / `review_sheet.csv` / LS tasks。
- `scripts/forward_pulse.sh`：`FABLE_COLLECT_REAL_TIPS=1` 时调用（失败不挡脉冲）。

**为何**：v13–v16 证明「旧金标派生数据集教不会 tip」；要真盘口分布。

**铁律**：✅ 不 promote；✅ 不碰 holdout；✅ 检测只认盘口（采 tip 窗）；✅ 脉冲预算有硬顶；✅ `detector=none` 可跑。

**问题**

| 级别 | 点 |
|---|---|
| bug | `empty_total` 表达式是 `len(empty and [...] or [])`，语义拧巴，易报错/报错数 |
| 半成品 | 画廊 `<img src='{PROJECT}/{png}'>` 绝对路径，换机/HTTP 打开易裂图 |
| 半成品 | 每次 rebuild **整表重写** `owner_class`，已填审阅会丢（无 merge） |
| 风险 | VPS 是唯一写者 OK，但 Mac 上 `real_tip_review` 已入库而 `data/real_tip_collect` 常不在——审阅包易成空壳引用 |

---

### 1.2 tip fair revalidate（v15 公平尺）

**改了什么**

- `scripts/eval_v15_fair_tip.py` + `tip_detectability.py --full-ma`：分母拆「应开火 / 空背景误火」，弃 slice-MA 过宽赦免。
- 产物：`real_tip_fair_v{12,14,15}.json`、`p_v15_revalidate_fair.md`。

**为何**：旧 tip_hit / 无条件 smoke 误读 pad200。

**铁律**：✅ 发现级否决 promote；✅ 向「真 tip 金标」靠拢。

**问题**：默认 `tip_detectability` 仍是 slice-MA（兼容旧数）；新人若忘 `--full-ma` 会复读虚高。属文档/默认值陷阱，非新逻辑回退。

---

### 1.3 v15 tipval / pad200 MAD（已结案链）

**改了什么**

- pad200：`build_crop_pad200_dataset.py` **MAD 默认开**（修 okx_* 错窗）。
- v14 MAD-on 训完仍 tip-smoke 0；v15「val 也 tip 对齐」仍崩。
- 07-23 `scripts/_archive_pretip/` 归档 v13–v15 训/同步脚本（35 个）。

**为何**：先修标签，再证伪「只怪 val early-stop」。

**铁律**：✅ 未 promote；✅ 增强路径仍走 SAFE_AUG（见下）；归档避免再同构 pad200。

**问题**：归档后 `analysis/p_v14_windows_train.md` 等仍写 `bash scripts/sync_v14_to_windows.sh`——路径已死，应改指 `_archive_pretip/` 或标明考古。

---

### 1.4 v16 tipuni + 3060 sync

**改了什么**

- `build_v16_tipuni_dataset.py`：正负统一新鲜渲染；负样本 MAD 消歧；livetip 空背景 clean 重渲；**val 正样本改从 v15 tipval**（034b821，owner 目检出中段框）。
- `sync_v16_to_windows.sh`：默认 `zzc@192.168.1.3`；只推数据集 + yolo11n/s；**冷启动、v12 永不作底座**。
- `v16_train_start.sh`：WMI 起远端 `train_dense.py`；注释写明 NO promote。

**为何**：消「pad200 风格 vs 旧图」捷径；教义 12 + owner 冷启动裁定。

**铁律**：✅ 不 promote；✅ holdout 有 `--allow-holdout` 门；✅ 增强声明保留。终审失败后维持空转——正确。

**问题**

| 级别 | 点 |
|---|---|
| 文档≠代码 | 模块 docstring 仍写「Val keeps the v14 split」；代码已 skip v14 val 正样本 + 拷 v15 |
| 半成品 | Mac 仓**无** `train_dense.py`（只在 3060 盒）；`sync_v16` 也不 scp 它 → SAFE_AUG 漂移无法从 git 审计 |
| 过程 | HANDOFF 曾要求 discovery PF≥1.3 才耗 holdout #6；仓内**无** `v16_discovery_preholdout.json`，却有完整 holdout 产物——闸门记账缺口（owner 预授权可覆盖，但应写清） |

---

### 1.5 tip-replay 回测 + base-rate

**改了什么**

- `backtest_tip_replay.py`：逐 bar 只见过去；A′ / MIN_GAP / maker；默认拒 holdout。
- `analyze_v16_judgment_filter.py`：对 holdout fire 复算判断层（模块级脚本，无 CLI）。
- `base_rate_dense_offline.py`：纯规则密集 vs 随机；**硬切 &lt;2026-05-04**。

**为何**：旧 PF 6.61 是事后自洽；要钱判 + 拆开「几何本身有无 alpha」。

**铁律**：✅ holdout 门；✅ base-rate 不碰 holdout；✅ 未 promote。

**问题**

- 报告复现写 `/tmp/_v16_judged.py`，仓内是 `scripts/analyze_v16_judgment_filter.py`——半成品/文档漂移。
- tip-replay 默认 cost 用 `FORWARD_COST`（maker 0.06%），与铁律叙事里常见的 0.2% 往返验收口径不同——报告已声明，但看板读者易混。

---

### 1.6 教义 12：live 只扫盘口 + detector=none

**改了什么**

- `yolo_candidates.py` live：`tip/tip-1/tip-2` only，删 stride 回看。
- `forward_scan.py`：权重缺失 → 打日志空转，账本/K 线继续。
- 删 v12 shadow；删 `owner_best*.json` 元数据。

**为何**：回看窗只能产事后行，与新鲜度门结构性冲突。

**铁律**：✅ 纪律 12 落地；✅ 不自动 promote。

**问题**：`DEFAULT_WEIGHTS = models/owner_best.pt` **仍指向已删文件**。主脉冲有 `FileNotFoundError` 兜底；`tip_detectability` / `benchmark_check` / `model_prelabel_pack` 等默认仍会炸。看板 `_owner_detector()` 会诚实显示「不存在」——对，但 overview 旧验收 PF 仍在别的 API 里。

---

### 1.7 Webapp

**改了什么**

- `/api/backtest/tip_replay` + 回测页置顶 v16 tip-replay；旧事后数字折进「已废弃方法学」。
- `status_strip`：训练旁路改盯 `owner_v16_tipuni_cold`（60 ep）。

**为何**：防止看板继续把 PF 6.61 读成现役真相。

**铁律**：✅ 展示层，不切 ACTIVE。

**问题**：总览/验收 tile 仍走旧 `window_metrics(accept)`；只有回测 tab 换了口径——**半换装**。`models/ACTIVE` 仍指向判断层 v11 frozen（预期，因未切检测器）。

---

### 1.8 杂项健壮性

- `loader.py`：`encoding_errors=replace`——防单坏字节崩整跑；数值 CSV 上合理。
- `train.py` SAFE_AUG：`fliplr/flipud/mosaic/mixup/hsv_h=0`，但 **`hsv_s/hsv_v=0.05`**。铁律字面「hsv 全关」vs 代码「微抖动」——**长期微不一致**，非这两天引入；3060 的 `train_dense.py` 不在仓内，无法确认是否同值。

---

## 2. 铁律对照速查

| 纪律 | 近两日代码 | 评 |
|---|---|---|
| 1 holdout | tip-replay 默认拒；#6 已耗并记账；base-rate 避开 | 过程闸门 JSON 缺，结论门有 |
| 2 时间切分 | tip-replay / base-rate 按时间窗 | ✅ |
| 3 无前视 | tip-replay / collect / live 扫 | ✅（旧事后路径已清） |
| 4 单变量 | v14→v15→v16 基本单变量推进 | ✅ |
| 5 增强禁用 | Mac `train.py` 主开关关；hsv_s/v 微开 | ⚠ 字面微违 |
| 6 data 不入 git | collect 写 data/ | ✅ |
| 7 新鲜度三门 | 本次未改 30min | ✅ 未乱动 |
| 8 脉冲预算 | collect 120s 旁路 | ✅ 设计上安全 |
| 9 VPS 唯一写者 | collect 设计在 VPS | ✅ |
| 10 不自动 promote | 全程未 promote；ACTIVE 未改检测 | ✅ |
| 11 真金 | 无下单/改仓代码 | ✅ |
| 12 检测只认盘口 | live tip×3；pre-v16 权重清；金标门写进教义 | ✅ |

---

## 3. 明显 bug / 半成品 / 文档不一致（汇总）

**代码**

1. `build_real_tip_review_pack.py`：`empty_total` 表达式错误/脆弱。
2. 同脚本：画廊绝对路径；rebuild 冲掉 owner 已填列。
3. `build_v16_tipuni_dataset.py` docstring 与 val 源不一致。
4. `DEFAULT_WEIGHTS` / 多脚本默认仍 `owner_best.pt`（文件已删）。
5. `analyze_v16_judgment_filter.py` 无 argparse；报告指向 `/tmp/_v16_judged.py`。

**文档 / 状态**

6. `HANDOFF.md` 顶部「holdout #6 完成」vs 中段「⑥(待触发)」；多处「主线仍 v12」vs「现役检测器:无」。
7. `docs/RESEARCH_AGENDA.md` 仍写「跟 v13 pad200 训练中」——过时。
8. 归档后 v14/v15 sync 路径文档未改。
9. `v16_discovery_preholdout.json` 缺失 vs 看板/HANDOFF 闸门叙事。
10. 回测 tab 已换 tip-replay，overview 仍旧验收 PF——半换装。

**架构债（知情）**

11. Windows `train_dense.py` 不在 git → 增强/冷启动行为以盒上文件为准。

---

## 4. 不建议现在做的事

- 不要再同构 pad200 / 从 v12 微调。
- 不要把 `owner_v16_tipuni_cold.pt` promote 进 live。
- 不要清 `forward_log` / 改 ACTIVE（需 owner）。
- 修文档与旁路默认权重可以排队；**开训等 owner 审完真 tip sheet**。

## 5. 建议的下一步（需 owner 拍板）

1. 审 `v13_real_tip_preview/review_sheet.csv` + 后续 `real_tip_review/`（v17 闸门）。
2. 是否接受「实时盘口下形态扣成本后可能无 alpha」（base-rate 已偏随机）。
3. 是否授权清理：HANDOFF 中段过时段、默认权重改 `none`/显式路径、review_pack 小修（merge sheet + 相对路径）。

---

*本审查只读分析；未改业务代码、未开训、未 push。*
