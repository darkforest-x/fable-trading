# 周期必须显式声明，不能靠引擎的默认值推断

- **问题**：把为 5m 写的回放引擎直接喂 1m 序列。引擎的缺口判定写死 `diff != 5min`，
  于是**每一根 1m K 线都被判成数据缺口**，BB 段段重启、永远不预热，结果是 0 笔交易——
  而且它不报错，只是安静地什么都不做。
- **死胡同**：看到"没有信号"以为是行情里真的没有形态，或者以为 1m 上规则不触发。
  这类失败没有异常、没有警告，产物里只有一个空表。
- **有效路径**：让调用方**显式声明周期**并自己算好 `_data_gap` 列传进去（引擎已支持
  外部提供的缺口列），同时把"确认收盘时刻"和"持仓小时数"也参数化——它们同样藏着
  `+5min` 和 `/12` 这两个常数。默认值保持 5m，所有既有 5m 调用逐字节不变。
- **通用规则**：换周期时先 grep 引擎里的 `minutes=`、`/12`、`Timedelta(minutes=`；
  任何把周期写死的地方都要么参数化要么显式拒绝。**宁可让它对不认识的周期报错，
  也不要让它安静地产出空结果。**测试里要有一条"不声明周期就会把每根当缺口"的反例，
  否则这个坑会再来一次。
- **牵连**：`yoyo/evaluation/bb_stoch_parameter_replay.py::_gaps`、
  `yoyo/evaluation/btc_xau_bb_stoch_study.py::declared_gaps`、
  `yoyo/evaluation/eth_bb_stoch_study.py::match_context/enrich` 的 `minutes` 参数、
  `tests/evaluation/test_btc_xau_bb_stoch_study.py`。
