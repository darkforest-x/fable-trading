# 两个 ATR 后期一致，但在最要命的位置不一致

**日期**：2026-08-20（单仓收敛 C5 语义去重盘点）
**结论**：`yoyo/data/indicators.py::atr14` 与
`yoyo/layers/l1_detection/numeric_baseline/indicators.py::atr_14` **不是同一个数**。
差异只在 warmup，指数衰减，200 根后耗尽——但 ATR 定义 TP/SL 障碍距离。

## 怎么发现的

收敛后做语义去重盘点，逐项实测"同一语义的多个实现是否真的一致"。
SMA/EMA 三个实现最大绝对差 **0.000e+00**（只有列名 `sma20` vs `sma_20` 不同）。
顺手用同一批 K 线比 ATR，以为也会是 0：

```
bar 14   :  0.1094
bar 40   :  0.0171
bar 100+ :  < 1.9e-4
bar 200+ :  < 1.1e-7
```

## 根因

```python
# 严格版（numeric_baseline）
tr.iloc[0] = np.nan                       # bar 0 没有前收，TR 无定义
atr = tr.ewm(alpha=1/14, adjust=False, ignore_na=True).mean()
atr.iloc[:14] = np.nan                    # 攒够 14 个 TR 之前不出数

# 宽松版（yoyo/data）
atr14 = tr.ewm(alpha=1/14, adjust=False).mean()   # 从 bar 0 出数，
                                                   # 且用 bar 0 的 high-low 播种
```

宽松版把一个**无定义的 TR**（bar 0 的 high−low，没有前收参与）当成有效值播了种。
EWM 的记忆按 (1−1/14)^n 衰减，所以这个错误的初值影响一路带下去，只是越来越小。

## 为什么不能当成 warmup 细节放过

一般说"warmup 那几根不准"是可以接受的，因为没人用序列开头做决策。**这里不成立**：

- ATR 直接决定障碍距离（−5 ATR 止盈 / +2 ATR 止损）。ATR 偏 0.11，障碍就偏 0.55 / 0.22。
- 任何"取某段行情的前 100 根扫信号"的路径都落在差异 > 1e-4 的区间里。
- 差异是**单向的**：宽松版的 ATR 系统性偏小还是偏大，取决于 bar 0 的 high−low 相对
  后续 TR 的大小——即取决于取数窗口从哪一根开始。**换个起点，同一根 K 线的障碍距离会变。**

最后一条才是真正的问题：ATR 本该是 bar 的属性，这里它变成了「bar + 你从哪开始读」的属性。

## 处置（以及为什么不是"直接修掉"）

**没有修。** 改任何一边都会移动所有用过它的已发布数字，而"哪一个才对"属于
CLAUDE.md 里 owner 保留的障碍参数范畴。当前做法：

1. 量化，写进 `docs/consolidation/DUPLICATE_SEMANTICS.md`；
2. 用 `tests/parity/test_duplicate_semantics.py::test_the_two_atrs_diverge_only_in_warmup_and_the_gap_decays`
   把差异**钉住**——bar 14 的 0.109、bar 100 后 < 2e-4、bar 200 后 < 1e-6；
3. 给 owner 三个选项（保持 / 统一到严格版 / 新代码用严格版），等裁决。

**一个被量化并钉住的分歧是已知量；同样的分歧没写下来，就是一个会在别人顺手整理
import 时改变的数字。**

## 可迁移的判断

- **"两个实现算的是同一个东西"必须实测，不能读代码得出。** 本轮 SMA/EMA 读起来
  和 ATR 一样像，实测一个差 0，一个差 0.11。
- **比较要看差异的形状，不只是最大值。** 最大差 0.109 听起来像 bug；
  看到它按 (1−1/14)^n 衰减，才知道是播种问题而不是公式问题——两者的处置完全不同。
- **递归指标（EWM / Wilder / streak）的 warmup 语义是接口的一部分**，
  和窗口长度一样需要写进 docstring。"前 N 根不准"不等于"前 N 根之后就一样"。
- 判断"要不要现在修"的标准不是"哪个对"，而是**"改了会移动哪些已发布数字"**。
  移动别人的结论需要 owner 点头；量化并钉住不需要。
