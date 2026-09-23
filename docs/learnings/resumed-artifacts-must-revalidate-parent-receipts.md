# 续跑复用产物仍须核验父证据

- **问题**：相对波动入场研究的首轮回放逐笔匹配父账本，但子代理发现缓存快捷返回只验本轮 CSV 和 identity，没有重验当前父 stream 及其 receipt。
- **死胡同**：父 manifest 的哈希没变不代表它引用的每个文件仍完好；同一币两个周期还共享输入数据 SHA，单凭该 SHA 与全局 run identity 不能拒绝错周期缓存。
- **有效路径**：包括缓存返回在内都先验证当前父 receipt/全部账本和原输入 SHA，再核对子 receipt 的 symbol、minutes、parent_receipt_sha256；直接导入的账本读取实现也加入代码身份。保留首轮输出，提交修正后重跑并比较所有表哈希。
- **通用规则**：快路径必须与慢路径拥有相同身份前置条件；代码身份覆盖直接影响解释的依赖，不能只哈希入口脚本。
- **牵连**：`yoyo/evaluation/spike_v128_expansion_entry.py`、其行为测试、`exp-spike-v128-expansion-entry-20260923-v1/run_v1` 与修正后的 `run_v2`。
