# 匹配对照报告必须钉住候选总体

- **问题**：市场广度的阶段一候选与阶段二匹配随机对照分目录产出；只读取各自摘要时，容易把一个对照结果误接到另一版候选总体上。
- **死胡同**：仅在报告中写目录名或开发日期不能证明二者同源；解析候选明细来比较又会扩大报告器的数据读取边界。
- **有效路径**：让 matched manifest 固定阶段一 candidate context、source manifest 与自身 summary 的完整 SHA-256；整合器只哈希这些不透明输入，并同时核验阶段一 manifest 的输出钉点，再读取小型汇总表。所有展示的 sign-flip 行必须显式是 calendar_month。
- **通用规则**：跨阶段整合先验证下游 manifest 对上游输入和自身摘要的字节身份；需要摘要的报告器不要为此解析候选明细或原始市场表，也不要把事后看的 p 值或尾部保留率包装成预注册验收门。
- **牵连**：`yoyo/evaluation/spike_market_breadth_report.py`、`experiments/active/exp-spike-market-breadth-20260913-v1/results/manifest.json`、matched-control manifest。
