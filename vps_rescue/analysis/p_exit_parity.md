# P-EXIT-PARITY：回测 vs 前向出场逻辑等价性验证（2026-07-20）

**结论先行：两套出场实现在纸面逻辑上等价。** 所有能"平仓"的路径（TP、SL、同 bar 双触、
超时、跳空穿越）逐笔 outcome / label / exit_offset / realized_ret / exit_time 完全一致；
差异只存在于两处**设计上有意的非对称**（部分视界、tip 信号），不影响任何已平仓裁决的数值。
未发现需要 owner 拍板"改哪一套"的逻辑分歧。另有两个非分歧但值得记录的注意项（见 §5）。

- 本次**不消耗 holdout**、不读 forward_log、不改任何 src/ 生产代码；仅新增
  `tests/test_exit_parity.py`（合成数据）与本报告。
- 范围：回测标注器 `src/judgment/labeling.py::label_candidate`（`src/backtest/run.py`
  逐笔重放的就是它在建数据集时算出的 outcome）vs 前向解析器
  `src/judgment/forward_scan.py::resolve_forward_exit`（VPS 实盘账本 open→closed 的唯一写入方）。
  实盘 executor（OKX 括号 OCO 真单）与回测的偏差属于另一分析，不在本报告范围。

## 1. 方法

1. 静态对照：逐条读两套实现，把入场价、障碍定义、同 bar 优先级、超时、出场价、成本
   扣法列成对照表（§2）。
2. 等价性测试：`tests/test_exit_parity.py` 构造合成 OHLCV（信号 bar + 路径 bar，
   ATR=1、entry=100，障碍 = 105/98），把**同一个 frame** 分别喂
   `label_candidate(tp_mult=5, sl_mult=2, horizon=72)` 和 `resolve_forward_exit`，
   断言逐字段一致。覆盖：触 TP（含恰好触线 `high == upper`）、触 SL、同 bar 双触、
   SL 早于 TP / TP 早于 SL、超时、跳空向下/向上穿越、入场 bar 当 bar 出场、
   部分视界、tip 信号、atr_pct 地板双拒，外加 400 条种子固定的随机游走模糊测试
   （逐字段断言 + 四种 outcome 全部出现过才算覆盖）。
3. 不写 harness 改语义——两套函数签名足够接近，直接调用即可，无适配层。

复现命令（从零）：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/test_exit_parity.py -v
```

## 2. 静态对照表

| 维度 | 回测（labeling.label_candidate） | 前向（forward_scan.resolve_forward_exit） | 是否一致 |
|---|---|---|---|
| 入场价 | 信号 bar 下一根的 open（`open[signal_i+1]`） | 同左；tip 信号时暂用信号 bar close 作代理，下一脉冲回填真值 | ✅（tip 代理是账面暂态，不进裁决） |
| 入场时间 | `signal_time + 1 bar`（build_signals/score_with_artifact 推得） | 入场 bar 的 `open_time` | ✅（K 线连续时同值） |
| TP 障碍 | `entry + tp_mult × ATR14(signal bar)`，主线调用显式传 `tp_mult=5.0` | `entry + TP_MULT × ATR14(signal bar)`，`TP_MULT=5.0`（forward_types 常量） | ✅（数值同；来源见 §5 注意项 1） |
| SL 障碍 | `entry − 2.0 × ATR14(signal bar)` | 同左，`SL_MULT=2.0` | ✅ |
| 触发判定 | `high >= upper` / `low <= lower`，向量化 argmax 取首触 bar | 完全相同的 argmax 逻辑 | ✅ |
| 同 bar 双触 | 顺序不可知 → 保守记 SL（`sl_ambiguous`，label 0，按下障碍价出） | 同左 | ✅ |
| 超时 | 入场 bar 起第 72 根（18h）收盘价出场，label=0 | 同左（`HORIZON_BARS` 直接 import 自 labeling） | ✅ |
| 出场价 | TP/SL 按障碍价成交（跳空穿越也按障碍价，理想化）；超时按 horizon 收盘 | 同左 | ✅（共享同一理想化，见 §5 注意项 3） |
| atr_pct 地板 | `atr_pct(signal bar) >= 0.0015` 否则弃 | 同左（同一个 `ATR_PCT_MIN` 常量） | ✅ |
| 视界不足（未触障碍） | 返回 None，候选不进数据集 | 返回 `status=open`，等下一脉冲续判 | ⚠️ 设计非对称（§3） |
| tip 信号（入场 bar 未打印） | 返回 None | 返回 open + 代理入场字段，下一脉冲回填 | ⚠️ 设计非对称（§3） |
| 成本扣法 | 出场函数不扣；`backtest/run.py` 在 gross_ret 上扫 0.2%/0.3%/0.4% 往返（spot taker 口径） | 出场函数不扣；账本存毛收益，看板/digest 报表层减 `FORWARD_COST=0.0006`（swap maker 口径） | ⚠️ 口径不同但均为报表层、owner 决策的路由表（src/costs.py），非逻辑分歧 |

## 3. 设计上的非对称（不构成不一致）

两处都只影响"何时能给出裁决"，不影响"裁决给出后的数值"：

1. **部分视界**：数据不足 72 根且障碍未触时，回测标注器直接弃样（None），前向记
   open 挂账。若障碍已在可见窗口内触发，前向立即 closed，且字段与全视界下的标注器
   输出逐位相同（测试 `test_partial_horizon_with_barrier_touch_still_closes_identically`）。
   副作用：每条序列最后 72 根内的候选不会进训练集，但前向会照常裁决——这是候选宇宙
   边缘差异，不是同一笔交易两种结果。
2. **tip 信号**：信号 bar 即最新收盘 bar 时，前向以 open+代理入场记录（2026-07-20
   实时路径），`merge_forward_log` 在下一脉冲用真 next-bar open 覆盖入场三字段；
   最终平仓裁决所用的 entry 与回测定义相同。

## 4. 测试结果

```
15 passed in 2.36s   （.venv/bin/python -m pytest tests/test_exit_parity.py -v）
```

| 测试组 | 数量 | 结果 |
|---|---|---|
| 平仓路径逐字段一致（TP/SL/双触/超时/跳空/入场 bar 出场/先后次序） | 9 | 全部通过 |
| 设计非对称按预期（open vs None） | 3 | 全部通过 |
| atr 地板双拒 | 1 | 通过 |
| 恰好触线（`high == upper`）双侧同判 | 1 | 通过 |
| 400 条随机游走模糊（四种 outcome 全覆盖，逐字段 + exit_time） | 1 | 通过 |

## 5. 注意项（非分歧，建议记录在案）

1. **`labeling.py` 模块默认值是 TP4/SL2，不是主线 TP5/SL2。** `TP_ATR_MULT=4.0` 是
   2b-v2 时代的默认；主线 yolo 数据集（`scripts/yolo_candidate_source.py`）与全部
   sweep 脚本都**显式传** `tp_mult=5.0`，前向用的是 `forward_types.TP_MULT=5.0` 常量。
   即：等价性目前靠"调用方记得显式传参"维持。谁若直接调 `label_candidate()` 裸默认
   建数据集（如 `src/judgment/build_dataset.py` 的 legacy strict/expanded 路径就是裸
   默认 = TP4），得到的就不是主线障碍。
   **owner 决策项**：是否把 `labeling.py` 默认值改为 5.0 / 或让两处共享同一常量。
   本次未改（默认障碍参数属 owner 决策，铁律升级规则）。
2. **成本口径分层但异源**：回测验收用 0.3% spot taker 档，前向看板净值用 0.06% swap
   maker 档。两者都来自 `src/costs.py` 路由表（owner 决策），比较回测 PF 与前向净值时
   须注意口径不可直接对读。无需改动，仅提醒。
3. **跳空按障碍价成交是两套共享的理想化**：bar 开盘已跳过 SL 时，两套都按障碍价
   （高于实际可成交价）记出场——纸面等价，但相对真实成交双双偏乐观。这属于
   executor-vs-回测偏差分析的范围（另一分析在跑），此处只确认两套纸面一致。

## 6. 风险与诚实声明

- 测试用**合成数据**，未用真实 K 线回放对账（真实对账需读 forward_log，本次约束禁止）。
  合成覆盖了 OHLC 单调性内的主要边界，但不排除真实数据中的 NaN 排列、K 线缺口
  （open_time 不连续时 exit_time 语义为"入场时间 + offset×15min"，两套同源，
  仍一致，但与真实日历时间可能有别）等未枚举形态。
- 等价性只覆盖**出场**。入场候选宇宙（YOLO 扫描窗、MIN_GAP 去重、freshness 门）
  两侧本就不同构，不在本次范围。
- 模糊测试种子固定（20260720），400 条序列中四种 outcome 均出现；换种子理论上可能
  暴露新形态，但两套核心判定是逐行同构的 argmax 逻辑，风险低。
- 本次零消耗 holdout；未读、未写 forward_log / models / 阈值；未 commit。

## 7. 下一步选项

1. （owner 决策）是否统一 `labeling.TP_ATR_MULT` 默认值与 `forward_types.TP_MULT`
   为单一常量来源——消除"靠显式传参维持等价"的隐患（改默认值属障碍参数变更，需批准）。
2. （无需决策）executor（OKX 真单）与本纸面逻辑的偏差分析在另一线进行，完成后可与
   本报告拼成三方对照。
3. （无需决策）`tests/test_exit_parity.py` 已可入常规测试集，后续任何人改动两套出场
   代码会立刻被拦。
