# CPCV 的乐观幅度可能比你要找的效应还大

**2026-08-03**,归因 +23.49bp 与 -15.91bp 的 44bp 差时实测出来的。

## 结论

同一批候选、同一目标、同一模型参数,**只把 15 折 CPCV 换成 5 折 walkforward,
顶档绝对收益掉 21.93bp,符号从正翻负。**

```
S1  15 折 CPCV(6 选 2,带 purge/embargo)   +19.48bp
S2  5 折 walkforward                        -2.45bp
                                    增量  -21.93bp   ← 全阶梯最大的一级
```

## 为什么

CPCV 在测试块的**两侧**取训练样本。purge/embargo 挡住了标签窗泄漏 —— 那部分它做对了,
本项目也验证过 embargo 从 72 行换成 7 天不改变结论。

**但它挡不住制度泄漏。** 模型见过测试块之后的市场状态:哪些币在 3 月崩了、
波动率在 4 月怎么变。实盘只有过去,永远。

所以 CPCV 的 lift 回答的是「**如果模型能看到未来制度,顶档值多少**」。
那不是一个可交易的问题。

## 为什么这条特别危险

本项目全部可争取的改进都在 **5~20bp** 量级(见
`docs/learnings/pool-internal-metrics-cannot-see-beta.md` 与各期 p2b 报告)。

**而 CPCV 的偏差实测 21.93bp —— 比要找的效应还大。**

这意味着在 CPCV 下做特征选择、比较出场规则、判断"某改动值不值",
**都可能是在测量偏差而不是测量效应**。07-30 那个 +23.49bp 就是这么来的:
它没算错,它回答的是错的问题。

## 怎么办

- **研究侧默认 walkforward。CPCV 只当上界参考,并在报告里注明它是上界。**
- 若必须用 CPCV(样本太少、要 15 折的统计功效),**同时报 walkforward 的数**,
  两者之差就是这一批数据上的乐观幅度,直接量出来而不是猜。
- 见到 CPCV 下的正结果,**先问它在 walkforward 下还剩多少**,再谈下一步。

## 附带的一个反直觉结果

同一次归因里,把 legacy_unaligned 的 v10 池换成 side_aligned_v1 的 P1 immutable 池,
顶档绝对**升了 5.80bp**。

我原本的假设是"我那个数算在 legacy 语义上所以虚高"。**假设被否定了** ——
数据和语义都不是差异来源,切分方案和特征集才是。**先量再归因,不要先归因。**

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/diag_attribution_23bp_vs_minus16bp.py
```

完整报告:`analysis/p_attribution_23bp_vs_minus16bp_20260803.md`,
预注册:`analysis/prereg_attribution_20260803.md`(提交于 `41a788d`,早于任何数字)。
