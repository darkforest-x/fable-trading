# V9 上级 SMA 方向过滤

状态：研究未证实，保留 TV V12.2。该实验评估固定29币的**纯V9双向信号**，不是先前570笔break+spike样本。

前段选参：15m→1h SMA50、1h→4h SMA20、4h→日线不加过滤。后段净R：51.7652→69.0416、47.1622→51.4119、-4.4794不变。15m增益区间跨0，1h仅剔除4笔亏单；选定门下多头后段仍负。

- 完整解释：`analysis/p1_spike_v9_htf_sma_20260920.md`。
- 最终统计：`run_v1/statistics_v2/`，每参数、多空、逐币、控制组、变化归因和完整逐笔账本。
- 原始回放：`run_v1/streams/`；输入/代码身份：`run_v1/identity.json`；BTC探针：`probe_v1/`。
- `run_v1/statistics/` 是保留的初版。v2消除本机BLAS虚假浮点警告、补齐归因和控制审计；收益、参数和24个p值均未改变。
- `review_and_qa.json` 记录Luna Max发现与主代理处理、58项测试和87流逐笔基线一致性；文件哈希见`delivery_manifest.json`。
- Notion：https://app.notion.com/p/3e08856479af813984a1fbbe2be6c194。

原门 p<0.01 在8月、3项Holm精确月符号检验下不可达，报告已明确披露设计限制。保留预注册，不把未证实解释为无效，也不看完结果后更换检验追过门。未改Pine、TV脚本、生产默认、风控或成本。
