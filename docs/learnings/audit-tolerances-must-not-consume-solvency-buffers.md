# 账本审计容差不能由未来利润放大到吞掉清算缓冲

- **问题**：Owner询问滚仓代码如何优化。只读审查发现，`audit_schedule`把容差设为`1e-8 * max(capital, abs(final), cost)`，当终值很大时，原定0.01U缓冲会被容差抵消，甚至负维持余量也能通过。
- **死胡同**：把同一个相对误差比例用于终值对账和是否触及清算边界。终值可以是百万U，而安全余量只有分币，两者不是同一验收尺度；未来大额盈利也不应放宽更早的偿付约束。另一个同类问题是价格校验只给`rtol`却保留`np.isclose`默认`atol=1e-8`，微价币的数量级错价仍可通过。
- **有效路径**：以100U、两根合成bar独立构造反例，直接给审计器一个余量为负的仓位，证明这是审计边界问题。建议新版本分别定义价格、金额对账和偿付边界容差；偿付误差必须显著小于业务缓冲，精度不足则拒绝判定或使用更高精度。合成复现已完成，应用代码修复尚未执行。
- **通用规则**：先按量纲区分数值误差与经济缓冲，禁止误差阈值改变“是否可行”的经济含义。对极小价格和巨额终值分别做对抗样本。共享参数校验还须在优化器与独立审计入口均拒绝NaN、无穷和非法负分量。
- **牵连**：`yoyo/evaluation/winner_roll_max10r.py:52,65-67,82-84`；旧367笔的生成时点、仓位与收益不会被后验审计缺口改写，而且先前独立1101账本/LP检查记录的最小维持余量为`0.010000009912U`。本次没有重跑或修改旧市场结果，也没有证明真实交易所不爆仓。新修复必须保留已被manifest绑定的旧源码。

## 100U最小复现

```python
import pandas as pd
from yoyo.evaluation.winner_roll_max10r import audit_schedule

low = (901 - .005) / 949
frame = pd.DataFrame({
    "open": [1., 2000.], "high": [1.1, 2000.],
    "low": [low, 2000.], "close": [1., 2000.],
})
result = audit_schedule(
    frame, [{"bar_i": 0, "price": 1., "quantity": 1000.}], 1
)
print(result["min_maintenance_buffer"], result["final_balance"])
# Observed: -0.005000000000130456, 1997099.0; no exception.
# The default required buffer is +0.01. This is synthetic, not market data.
```

其他只读复现：价格约1e-9的OHLC接受1e-12成交价；直接给审计器`fee=NaN`可返回NaN终值而不报错。默认研究参数有限且非负，优化器成交价直接取原open，因此这些入口缺口不等于旧生成结果出现了上述异常。
