# 独立核查与原生交付收据

2026-09-20，经济run_v1固定源码36d27908a4。此记录不替代源代码、完整账本与SHA manifest。

## 独立只读审查

预算reviewer角色配置为Terra High。先审执行与统计代码，再读取两币冻结源和run_v1；没有使用原trace函数做逐笔对账。

- 两源SHA和每币315360正式bar一致。
- BTC2335、ETH2246实际交易，入场、事件极值SL、固定2R、第一触及/跳空/双触、日末、单仓顺序无差异。价格最大误差BTC1.46e-11、ETH4.55e-13。
- 独立closed/marked曲线最大差BTC6.82e-13、ETH5.68e-13；all/early/late各统计最大差4.44e-10。
- 所有控制parent、相同匹配strata和入场前bar波动桶无错配。BTC1465/ETH1435条控制落在别的策略交易时间，这是无条件均匀抽样的预期交集；初审误判major后，复核estimand撤回。没有按结果改变对照池。
- `float_precision="round_trip"`读CSV完全再现BTC后段p0.6019699015049248、ETH0.04554772261386931及Holm0.6020/0.09109544522773862。默认解析在ETH一个等号处差1/20001，不是经济结论变化。
- BTC最差净-19.880019R实为2R赢家，风险0.00914076%、成本21.880019R；独立算术一致。

## 自动检查

18专项通过，命令在主报告。`boundary_checks.log`为468通过、8既有失败：

- `ma_morphology_reuse_recover.py`旧跨仓路径1项；
- 既有`spike-v8-total2-1h-native-20260914-delivery`缺source_commit引出teacher测试5项；
- `candidates.py`与`render.py`两项旧迁移哈希不符。

不掩盖失败，不在本策略任务内无关修复。

## TradingView原生收据

- 新私有“NY 首4小时区间假突破回归 · v1”，revision1，2026-09-20当地13:57保存并加入标准OKX BTCUSDT.P 5m图。
- Pine源SHA `1819ae81b47e28fc84f0d48ac4c8d93a092d9ffcbbc02182980148a04e003066`，编辑器全文复制与粘贴的11653字符源码一致；编译后策略报告显示50交易，无编译错误。未改旧SPIKE源码。
- 图表加载窗口Aug10–Sep20；该面板数量、qty=1现金结果不用于三年研究。TV显示calc_on_order_fills通用lookahead caution，源码把fill callback与confirmed close状态处理分开。
- 原生9月17日参考H76648.7/L76147.9，与Python/API H76655.2不同。额外09:20Z空单entry76621/SL76728可见；API09:10 close76653.3位于两个高点之间。直接API复读07:05 bar返回O76539.8/H76655.2/L76539.8/C76592.9、confirm1。
- budget-debugger只读复核未确认Pine状态机bug，但缺少TV相关原生bar值，不能把“区间计算不同”直接提升为已证实的输入源根因；尚无完整逐笔parity。
- 尝试原生图表CSV导出时界面显示当前Essential、需升级Premium；未购买、未绕过。未将未完成导出当成已取得数据。
- 未创建报警或真实订单。单独浏览器图表保留为交付入口，私有脚本可在“我的脚本”找到。

## 知识记录

Notion研究页`3e18856479af8131a2b9d56908ea9397`，状态拒绝、可用于生产false，关联旧策略Inbox与来源资料。创建后已fetch核对关键成本、结果和禁生产字段。
