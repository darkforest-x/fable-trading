# A censored ledger cutoff is not a trade exit

- **问题**：冻结账本把观察窗最后一根的价和时间保存在 `exit` 形状中，图册若直接使用 `exit.reason` 会把 `censored` 画成已执行退出。
- **死胡同**：只要字段名是 `exit` 就把它标为退出，混淆了观察结束和成交结论，也会让读者误把期末价当实现收益。
- **有效路径**：以 `reason == censored` 单独显示“数据截止 · 尚无退出结论”，保留期末参考和初始 SL 截止线；实际退出才用退出箭头。
- **通用规则**：回测输出若将截尾观察与交易终态共用结构，UI 必须先分辨状态，再决定标签、颜色和是否可计入结果。
- **牵连**：`yoyo/evaluation/static/spike_v1_review/timing.js`、`app.js`、OKX 133 笔冻结账本。
