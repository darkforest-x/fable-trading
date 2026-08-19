# yoyo-eth — 数值语义基线（已归档）

| 项 | 值 |
|---|---|
| 来源仓 | `darkforest-x/yoyo-eth` |
| 冻结 commit | `6147810afb46be1c664128e9a5359e8e7d0a3923` |
| 最终状态 | `closed_negative` |
| 迁入代码 | `yoyo/layers/l1_detection/numeric_baseline/`（12 个模块，字节一致） |
| 机器摘要 | [`summary.json`](summary.json) |
| holdout | **0 次消耗** |

## 问的是什么

> 不用 YOLO、不用图片——只用 OHLCV、SMA/EMA 20/60/120、ATR 和 27 个因果语义特征，
> 能不能把均线压缩候选排出一个序，让排名靠前的更容易先于一次盈利的做空？

## 答案

**不能，而且方向是反的。**

### 本仓最重要的一行数字

匹配随机对照组（同币 × 同月 × 同 ATR 三分位桶随机入场，同 horizon 同成本，每事件 20 次抽样）：

| 期 | 候选池整体（毛） | 匹配随机对照（毛） | 池 − 对照 |
|---|---|---|---|
| validation | −14.6bp | **+19.9bp** | **−34.5bp** |
| test | −39.1bp | −2.0bp | **−37.1bp** |

**MA 密集压缩事件在这两个时段是劣于随机入场的做空点。**
val 段 Top 10% 的 +62.6bp 是 5 个样本坐在下跌 beta 上，不能归给模型。

### 排序能力

| 切分 | 模型 spearman | p |
|---|---|---|
| validation | +0.167 | 0.28 |
| test | **−0.068** | 0.68 |

val 的微弱正相关没在 test 重现，方向翻转。讽刺的是 test 上朴素的
"越紧越好" 单特征排序（spearman +0.349, p=0.027）反而高于 27 特征模型。

### P03：+0.465 没有幸存

iteration_v1 报过 anchored val ρ=+0.465 (p=0.01, n=31)。同一批事件在 4 折
anchored walk-forward 下：对应折只剩 **+0.219**，下一折翻负 **−0.151**，
加权均值 **+0.065**（OOS n=140，噪声带 ±0.17）。原值的构成是
**该切分同时被用于 early stopping（模型选择）+ 单一 regime 顺风**。

唯一的正面观察（记为 OBSERVATION，不是结论）：anchored 臂在 **4 折全部**逐折优于
legacy，配对符号检验 p≈0.06。语义锚定方向对，幅度不够。

## 迁进来的东西

| 能力 | 落点 |
|---|---|
| 因果指标（SMA/EMA/ATR，warmup 语义明确） | `yoyo/layers/l1_detection/numeric_baseline/indicators.py` |
| 松压缩扫描器（3 种 trigger） | `.../scanner.py` |
| 27 个因果特征 | `.../features.py` |
| MFE/MAE short_utility 标签 | `.../labels.py` |
| anchored walk-forward | `.../walkforward.py`，通用版另见 `yoyo/evaluation/walk_forward.py` |
| 匹配随机对照 | 通用版 `yoyo/evaluation/matched_controls.py` |
| **Future Mutation Test** | `tests/causality/test_future_mutation.py`（已扩展） |

## 复现

```bash
python3 -m pytest tests/parity/test_numeric_baseline_parity.py tests/causality/test_future_mutation.py -q
```

原仓的一键管线（`scripts/run_mvp.py --config configs/mvp.yaml --force`）**未迁入**：
它会训练模型，而本次收敛不训练。配置已迁至 `configs/numeric_baseline/mvp.yaml`。

## 诚实声明（原报告自己写的，此处保留）

1. **样本量根本性不足**：val 44 / test 40 个事件，Top 10% 组只有 4–5 个样本。
   所有分组数字都不具备统计力，只能作管道正确性的演示。
   **池级结论（压缩事件劣于随机做空入场）是能带走的那部分；模型级数字不是。**
2. MVP 阶段**未做置换检验**，spearman 的 p 值不能替代排序置换检验。
3. embargo 不是完全 purge：EMA 递归尾部与 compression_duration 的 streak 理论上
   无界回看，只能声明，无法用有限 gap 隔断。
4. 成本用 SWAP_TAKER 0.10% 往返 + 0.2% 敏感度；**未建模 6 小时空头持仓的 funding**。
5. 复核图池含 train，top50 里大量 in-sample 的"完美案例"，不代表可复制性。

## 后续（若有人重启这条线）

P03 报告给 owner 的选项是 A 停止 / B 换标签（6h 固定 horizon → first-hit ATR barrier）
/ C 先翻图廊裁决。**冻结前没有选择任何一个。** 若重启，
下一个单变量是**标签/退出定义**，不是再调触发器。
