# JavaScript 行情文件必须按数据语法解码，不能为了兼容直接执行

- **问题**：新浪 `qfq.js` 不是纯 JSON：第一行是 `var <symbol>qfq=<JSON object>`，后面还有块注释；因子行又是 `{d, f}` 对象而不是预想的二元素数组。原严格解析器因此 fail-closed。
- **死胡同**：把等号后的全部文本交给 `json.loads` 会把尾部注释也纳入解析；照上游示例使用 `eval` 虽能兼容 JavaScript 包装，但会执行非受信文本，既不安全也无法证明只读取了因子数据。
- **有效路径**：只截取等号后的第一行，仍用严格 `json.loads`；因子行只接受显式 `d`/`f` 字段或既有二元素序列，日期和正数因子逐项校验。修复后先在 `SH600000`、`SZ000001` 上与哈希锁定的东方财富 QFQ 60m 对照，P99 相对误差分别约 0.111% 和 0.092%，通过预注册的 2% 上限后才允许全市场扇出。
- **通用规则**：遇到 `.js` 行情文件，先识别“包装代码”和“数据主体”的边界；只解析可证明为 JSON 的最小片段，并为实际行 schema 建 fixture。任何兼容修复都必须在个股扇出前由固定哨兵做数值 parity，不能只以“解析不报错”为验收。
- **牵连**：`yoyo/data/sina_ashare.py`、`tests/test_sina_ashare_source.py`、`experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/qfq_parser_recovery.json`、新浪 `qfq.js`、日期因果 QFQ、禁止 `eval`。
