# Holdout #7 — A 因果空边趋势出（no_tp / trail4）— 2026-07-23

**这是该配置第 7 次消耗 holdout**（owner 明确批准：「申请 holdout #7 只测 A 的因果空边
（no_tp 或 trail4）」→ 可以）。完整记账：①07-08 2b ②07-15 回归 ③07-16 v8 ④07-17 v10
⑤07-18 v11 ⑥07-23 v16 tip-replay ⑦**07-23 A 因果空边趋势出**。

窗口 **≥2026-05-04**（至数据尾 ~2026-07-22）。**未**测 long / oracle / 新规则 /
改参网格；**未** promote / 改 ACTIVE / 真下单。

## 裁决句（醒目）

**证伪。** train 过线的两档趋势出场，在 holdout 上全部塌到 ~1.0：

| 出场 | 段 | n | 胜率 | 净合计@m | PF@maker | PF@0.2% |
|---|---|---:|---:|---:|---:|---:|
| **no_tp_sl2_h144** | train | 6166 | 20.7% | **+21.81** | **1.415** | **1.222** |
| **no_tp_sl2_h144** | **holdout** | 2243 | 17.6% | **−0.05** | **0.997** | **0.855** |
| **trail4_atr_h144** | train | 6166 | 42.0% | **+15.38** | **1.359** | 1.141 |
| **trail4_atr_h144** | **holdout** | 2243 | 36.7% | **−0.53** | **0.969** | **0.805** |

- 成功线偏好 PF@maker ≥1.3：**两档皆未过**（差 ~0.3–0.4）。
- 净收益：maker 下接近打平或略亏；扣 0.20% 明确亏损。
- 相对 train：PF 掉 **~0.42 / ~0.39**——不是抽样噪声量级。

## 复现命令

```bash
# 默认无 --eval-holdout 时仍只扫 train（<2026-05-04），不会碰 holdout
PYTHONPATH=. .venv/bin/python scripts/short_trend_ab.py --eval-holdout \
  --n-symbols 0 --tag short_trend_holdout7
# 输出:
#   analysis/output/short_trend_holdout7.json
#   analysis/output/short_trend_holdout7_main.csv
#   analysis/output/short_trend_holdout7_periods.csv
```

## 预注册范围（未扩）

| 项 | 值 |
|---|---|
| 入场 | `spread_expand_chg8`（与 `p_short_trend_ab` / `p_trend_exit_base_rate` 一致） |
| 方向 | **仅 short** |
| 入场价 | **next_open** |
| 出场 | 仅 `no_tp_sl2_h144`、`trail4_atr_h144`（同参） |
| 样本 | holdout **≥2026-05-04** |
| 成本 | maker 0.06% + legacy 0.20% |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；预留 holdout 前 bar 做指标预热 |

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 311（holdout 窗内够长的系列；train 报告为 233） |
| 信号时间 | 2026-05-04 → 2026-07-22 |
| 空边触发 | 2345（结算 2243；尾部不够 144 窗的未结算） |

## 月度（holdout 够 ~2.5 月）

### no_tp_sl2_h144

| 月 | n | PF@maker | 净合计@m |
|---|---:|---:|---:|
| 2026-05 | 813 | 0.981 | −0.13 |
| 2026-06 | 942 | 1.003 | +0.03 |
| 2026-07 | 488 | 1.013 | +0.05 |

三月皆在 ~1.0，**无一 ≥1.3**。

### trail4_atr_h144

| 月 | n | PF@maker | 净合计@m |
|---|---:|---:|---:|
| 2026-05 | 813 | 1.163 | +0.91 |
| 2026-06 | 942 | 0.886 | −0.90 |
| 2026-07 | 488 | 0.832 | −0.54 |

5 月擦到 1.16 后 6–7 月翻车；全样本仍 <1.0。

## 解读

1. **Train 边不迁移**：`p_short_trend_ab` 的「月度稳健过线」在干净 holdout 上消失——
   利润堆在 2025H2–2026Q1 的结构未能延续到 5–7 月。
2. **2026-04 train 翻车是预警**：当时 `no_tp` 月 PF 0.678；holdout 整段在 ~1.0，
   更像制度换挡后的均值回归，而非「偶发坏月」。
3. **两档同塌**：no_tp 与 trail4 同步失败 → 不是单一出场参数运气，是**入场+方向底座**
   在 holdout 无扣成本 alpha。
4. **maker 打平 ≠ 可交易**：PF≈1.0 扣完 maker 净≈0；实盘滑点/资金费只会更差。

## 风险与诚实声明

- 本报告**明确消耗 holdout 第 7 次**；同一配置族不得再「看一眼」而不记账。
- 窗约 11 周、2243 笔——样本量够证伪 train≥1.3 的主张；**不**证明该形态永远无边，
  只证明**本预注册因果规则+两档趋势出在本 holdout 窗不可交易**。
- holdout 币数（311）> train（233）：因未截断序列、更多币在 5–7 月够长；规则与参数
  未改，不作 cherry-pick。
- 未改 ACTIVE / 三门 / forward_log；**不构成开空或 promote 授权**。
- Oracle / 手标框**未**测（按 owner 范围）。

## 下一步（需 owner 决策）

1. **收口本挑战者**：A 因果空边趋势出 **holdout 证伪**——勿 promote、勿开空。
2. 继续旁路攒真实 tip（v17 数据）/ 换命题；不要再为同一规则申请 holdout。
3. 实盘维持 detector=none 空转。

输出：`analysis/output/short_trend_holdout7.json` / `_main.csv` / `_periods.csv`；
脚本扩展：`scripts/short_trend_ab.py --eval-holdout`。
