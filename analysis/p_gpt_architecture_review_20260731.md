# fable-trading 架构与方法学审阅（2026-07-31）

来源：ChatGPT 分享审阅（lite 包）+ 本仓**完整工作区**复核与 P0 安全修复。  
分享：`https://chatgpt.com/share/6a6c0dc3-2d54-83ea-ac03-7da5b61e0840`  
范围：核心逻辑 / 训练 / 数据集 / 判断层 / 回测与前向协议。  
**不消耗 holdout，不自动 promote，不启用 short 实盘下单。**

---

## 1. 一页执行摘要

| 级别 | 结论 |
|------|------|
| **P0（已确认并部分修复）** | ACTIVE L2 是 **short** 池，但 `forward_scan` 曾硬写 `side="long"` + **long** TP/SL 几何；executor 见 long 就 **buy**。保护只拒显式 short，被伪装 long 绕过。历史 `forward_log` **不能**计为 short 前向 100 笔。 |
| **P1** | short 收益公式两套（`entry/exit-1` vs `1-exit/entry`）；wide 池同 bar 双触碰标 TIMEOUT；q90 实际 ~91% 过门；`realized_ret` 已是 net_taker 仍被再减成本；ACTIVE 文件非运行时权威（`latest_artifact(default_config)`）；离线 tip 时间戳 vs live 不等价。 |
| **L1 尚可** | 因果 200 窗渲染、YOLO 关 flip/mosaic/mixup/hsv 已落地。 |
| **L2 训练纪律尚可** | 时间切分 + purge；问题在协议拼接与阈值/成本语义。 |
| **处置顺序** | ① 停把当前 log 当 short 成绩 ② 修方向/特征/标签/成本协议 ③ 新 `protocol_version` 再累计 100 笔新鲜样本。 |

**架构评分（复核后）：5 / 10**（P0+H11+标签/成本/幂等已修；short 实盘执行与阈值语义未闭环。）

---

## 1b. 第二轮深度修复（2026-07-31 agent，按 GPT 清单）

| 问题 | 动作 | 状态 |
|------|------|------|
| 主路径仍强制 `exit_resolver=long` | `run_forward_tracking` 传 `None`，走 side-aware | **已修** |
| ACTIVE 非 runtime 权威 | `load_runtime_artifact()` 读 `models/ACTIVE` | **已修** |
| short 两套 PnL | `label_short` 改为 `1-exit/entry`（对齐 dump/v10） | **已修** |
| wide 同 bar TIMEOUT | dump → `SL_AMBIGUOUS` | **已修** |
| 成本双重扣除 | `evaluate(..., returns_are_net=)` 自动检测 net_taker | **已修** |
| 幂等键含 score | `signal_key` 去掉 score | **已修** |
| q90 过宽 ~91% | 报告增加 `pass_rate`；**不擅自改 thr**（owner 决策） | 指标可见 |
| short 真开仓 | 仍拒单 | **待 owner** |
| 清 forward_log | 未做 | **待 owner** |
| 离线 tip 时间戳 vs live | 未改生成器 | 研究项 |

测试：`66 passed`（forward/short/frozen/runtime ACTIVE）。

---

## 2. 最小修复方案（A）

### 2.1 目标

| 目标 | 做法 |
|------|------|
| 账本方向正确 | forward 写 `side=short`（来自 `FrozenConfig.side`） |
| 结算几何正确 | `resolve_forward_exit_short`：下 TP / 上 SL，PnL=`1-exit/entry`（对齐 v10 wide `net_barrier_*`） |
| 特征语义正确 | `extract_feature_rows_for_side(..., "short")` |
| 不误开多 | executor 仍 long-only → 见 `side=short` **拒单**（`skipped_unsupported_side`），**优于** 假 long 买入 |
| 不自动开 short 实盘 | **不**在本轮实现 OKX 空头下单（需 owner 另批） |

### 2.2 代码改动（本轮已落地）

1. `src/judgment/frozen.py`  
   - `FrozenConfig.side`  
   - `default_config()` / `yolo_v12_pool_config()` → `side="short"`
2. `src/judgment/forward_scan.py`  
   - `_artifact_trade_side`  
   - `resolve_forward_exit_short`  
   - scan 按 side 选 resolver + short 特征对齐 + 正确 `side` 字段  
   - short 的 `maker_filled` 启发式改为 high>open
3. `src/execution/executor.py`  
   - 文档：short 必须拒单，禁止静默转 buy
4. `tests/test_forward_short_side.py`  
   - short TP/SL 与 long 反向路径

### 2.3 后续（未做，需 owner）

| 项 | 说明 |
|----|------|
| short 执行 | posSide/sell + 括号单；纸面验证后再 demo |
| ACTIVE 权威 | `forward`/`latest_artifact` 读 `models/ACTIVE` 路径，失败则拒绝脉冲 |
| 标签公式统一 | 冻结 canonical short 为 `1-exit/entry`；对齐 `label_short_candidate` 或改 labeler |
| 同 bar 双触 | wide dump 与 `label_short` 统一 sl_ambiguous |
| 成本 | 训练/报告若目标已是 net_taker，禁止再减 ROUND_TRIP |
| 阈值 | q90 若 ~90% 过门，改选分位或换模型后再冻结 |
| 清账 | 新 protocol 新 log 段；旧 34 条标注 long 协议污染 |
| 幂等键 | `clOrdId` 去掉 score 或固定 signal 主键 |

### 2.4 验证命令

```bash
PYTHONPATH=. python3 -m pytest tests/test_forward_short_side.py tests/test_forward_runtime_policy.py tests/test_forward_tracking.py -q
# 静态
rg -n 'side.: .long.|resolve_forward_exit_short|extract_feature_rows_for_side' src/judgment/forward_scan.py
```

---

## 3. 完整工作区 H1–H17 复核（C）

| ID | 假说 | 完整仓状态 | 证据 |
|----|------|------------|------|
| **H1** | short 信号被做成 long | **修复前确认；修复后 ledger=short、executor 拒 short** | 旧：`side": "long"`；现：`_artifact_trade_side` + short resolver |
| **H2** | short 未用 short 特征对齐 | **修复前确认；修复后 live 用 `extract_feature_rows_for_side`** | 训练 `build_dataset --side short` 已对齐；forward 曾只用 long `extract_feature_rows` |
| **H3** | q90 实为 ~91% 过门 | **确认** | freeze thr≈-4.4e-4；val pass_rate 报告 ~91%；`best_iteration=1` |
| **H4** | walkforward 未全折为正 | **确认** | meta `all_folds_net_positive` 否 |
| **H5** | binary 与 regression 目标成本口径混用 | **部分确认** | v10 `realized_ret ≡ net_barrier_taker`；binary 历史池可能不同 |
| **H6** | wide 同 bar 双触 → TIMEOUT | **确认** | `dump_v9_candidates_dual_label.both_labels` else 支 → TIMEOUT；`label_short` → sl_ambiguous |
| **H7** | short 收益两套公式 | **确认** | `label_short`: `entry/exit-1`；dump/v10: `1-exit/entry`；forward short 现跟 dump |
| **H8** | 离线 tip 时间戳 vs live | **高风险，未全量量化** | live tip 窗 vs 离线 box→末端 bar；需专项对照 |
| **H9** | maker_filled 方向偏 | **确认（long 启发式）；short 已反转 high>open** | 仍是启发式，非成交回报 |
| **H10** | forward maker 成本 vs executor taker | **确认** | 看板 maker 0.06%；executor market |
| **H11** | ACTIVE 非运行时权威 | **确认** | `forward.py` → `latest_artifact(DEFAULT_FROZEN_CONFIG)`，不读 `models/ACTIVE` 文件内容 |
| **H12** | 100 笔混协议 | **确认风险** | 现 log 为 v11 long 时代；与 v10 short 不可混计 |
| **H13** | holdout 多次消费 | **文档确认** | HANDOFF N 次消耗记录 |
| **H14** | 置换检验看 AUC 非净收益 | **确认** | train 报告路径以 AUC/p 为主叙事并存 top 净 |
| **H15** | 47 特征 vs 28 特征叙事混 | **文档高风险** | 以 ACTIVE feature_columns 为准 |
| **H16** | live 可出现 tip-3 | **数学可能** | start 偏移 + tip edge；需 log 统计 |
| **H17** | 幂等键含 score | **确认** | `executor` key: `source|symbol|signal_time|score` |

---

## 4. 分章（压缩）

### 4.1 架构

意图：`YOLO tip → L2 score → forward_log → executor`。  
断裂点：side / barrier / 特征镜像 / 成本 / 工件权威。  
品种 15m SWAP 主路径一致；ETH 3m 旁路。

### 4.2 检测与数据

- 因果窗与禁增强：合格。  
- 标签可含未来 24 根确认：属标签前视（允许），图本身应无未来 K。  
- pretip 已清理；抽样包不可当全量。  

### 4.3 判断层

- 候选 YOLO；特征需 short 对齐（已修 live）。  
- 目标 net_taker 作 realized_ret：训练减成本会 **双重扣费**。  
- 阈值 q90 名不副实（过宽）。  

### 4.4 回测 / 前向

- tip-replay / paper / forward_log **三套问题**，不可互换。  
- 本机 `forward_log` 最新 detected **2026-07-21**，v11 路径；顶栏 v10 是配置展示，**不是**该 log 的写入协议。  
- 纸面 16 ≠ 前向裁决。  

### 4.5 前向页「一晚上没信号」

**正确读法：** 前向账本停更，不是「昨晚 26 笔事后」。累计 34/26/1 是旧样本结构。

---

## 5. 方法学红线

### 已遵守（主干）

- 时间切分 + train/val purge  
- L2 训练默认不读 holdout  
- YOLO 破坏性增强关闭  
- 生产候选源禁止 rules  
- 确认级文档指向新鲜前向  

### 已违反 / 高风险

- 方向合同（P0，代码已部分修）  
- holdout 历史多次消费  
- 成本双重扣除  
- ACTIVE 文件与 runtime 选择脱节  
- 旧 forward_log 当 short 成绩  

---

## 6. 下一步实验（单变量，不耗 holdout）

1. **协议烟测（本机）**：构造下跌路径，assert short TP、long SL 相反；assert ledger side=short；assert executor skip short。  
2. **成本口径**：报告层对 v10 禁止再减 maker（或改显示为「已含 taker」）。  
3. **阈值语义**：val 上报告 `pass_rate` 与 top-decile 分列；若 pass>30% 不得称 q90 精选。  
4. **ACTIVE 读取**：一行加载改为解析 `models/ACTIVE` 路径。  
5. **新前向段**：`protocol_version=short_v10_p0fix` 新 log；旧 log 归档只读。  

成功标准：协议一致 + 离线/live 同 side 几何；**赚钱与否仍只认新协议下 100 笔新鲜**。

---

## 7. 待 owner 决策

1. 是否授权 **short 实盘/纸面** 执行路径（当前仅拒单）？  
2. 是否清空/归档污染 `forward_log` 并重置 100 笔计数？  
3. canonical short PnL 锁定哪套公式？  
4. ACTIVE 文件是否升格为唯一 runtime 权威？  

---

## 8. 复现与关联

```bash
# 本报告相关测试
PYTHONPATH=. python3 -m pytest tests/test_forward_short_side.py -q

# 旧污染账本（只读）
python3 -c "import pandas as pd; d=pd.read_csv('data/forward_log.csv'); print(d['detected_at'].max(), d['model_path'].iloc[-1])"
```

关联：`AGENTS.md` 铁律；`HANDOFF.md`；`models/frozen_*v10*20260731.json`；ChatGPT 分享全文。
