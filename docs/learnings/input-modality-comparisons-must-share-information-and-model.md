# 图文输入对照必须固定信息边界与模型

- **问题**：2026-09-24 参考 DayTradingBench 的 text-vs-vision 设计，为 SPIKE 回放比较图片、数值和图文输入。要回答的是输入形式是否改变当前形态判断，不能把模型差异或未来信息误算为视觉能力。
- **死胡同**：直接照搬其不同模型榜单、给纯数值组继续附参考图、额外加入成交量或趋势摘要，都会同时改变多个因素；把无效 JSON 当 HOLD/不符合，则会把接口失败混进识别标签。这些是审阅时排除的设计，不是已运行的收益实验。
- **有效路径**：同一不可变观察固定 120 根已收盘 OHLC 与六条均线、截止时点、规则、模型和输出协议。三组都移除参考示例；vision 只发原 PNG，text 零图片且仅发同窗数值，hybrid 两者都有。只做分类且三组不画框，防止框任务天然偏向图像。每组先持久占用一次请求再发送，失败/重启中断保留未知状态；核对实际请求体及图片数量，而非只看参数名。
- **通用规则**：输入消融先列“各组实际看见了什么”，再固定共同因素。图片与精确数值是同一原始窗口的不同编码，不声称其信息精度等价。记录实际模型、顺序、token 和耗时；一致不等于正确。正式效果评测仍需独立事件、时间切分和人工标准，重复截图不能增加独立样本量。
- **牵连**：`yoyo/vision_research/comparison_input.py`、`comparison.py`、`zhipu.py`、回放路由与 UI、`tests/vision_research/test_comparison*.py`、`test_zhipu_comparison.py`。输入变化唯一变量；原成本、退出与训练/生产门保持原契约。外部设计来源：[Text vs Vision](https://daytradingbench.com/docs/text-vs-vision)、[Decision Process](https://daytradingbench.com/docs/decision-process)，查阅于 2026-09-24；借鉴设计思想，没有复制其交易执行逻辑或宣称其盈利结论成立。
