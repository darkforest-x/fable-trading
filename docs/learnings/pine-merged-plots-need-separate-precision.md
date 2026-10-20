# Pine 合并后价格精度与振荡器精度必须分开

- **问题**：区间 V1.11 默认继承币种价格精度，翻身 V1.1 在声明中固定两位小数。合并后照搬后者，会让低价币的 POC／VA 数据窗口数值过度舍入；ETH 两位小数的同屏对照无法发现这一点。
- **死胡同**：仅证明同一 ETH 图上新旧数值相同，不能证明不同价格精度的币种仍保持原显示语义。本次在源码核验中发现该限制，未宣称已经对低价币做过原生验证。
- **有效路径**：唯一声明不指定 format／precision，让区间继承品种格式；翻身的 10 个 plot／plotshape／plotchar 分别显式保留 `format.price` 和 `precision=2`。回归检查约束这两条边界，公式不变。
- **通用规则**：多指标合并时，除面板和坐标轴，还要检查声明级格式对所有子模块的影响；固定小数位只施加于需要它的输出。
- **牵连**：`monday_range_v2_0.pine` 与 `test_monday_range_v2_0.py`；依据：[TradingView plots](https://www.tradingview.com/pine-script-docs/visuals/plots/) 和 [declaration statements](https://www.tradingview.com/pine-script-docs/language/declaration-statements/)。
