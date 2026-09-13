# 判断 gzip 空表不能探测首条记录

- **问题**：部分无交易流只有精简表头。为区分空表和异常的非空精简表，表头后调用 `read(1)` 会在任何时间边界检查前返回首条记录的一个字节。
- **死胡同**：认为“只读一个字节、没有解析完整行”就不算物化。若该流第一条记录已进入受保护窗口，这个探针仍违反先验边界合同。
- **有效路径**：生成器每个文件只写一个 gzip member；先只返回表头，再从 gzip footer 的 ISIZE 比较未压缩总长度与表头长度。相等才认定 header-only；精简 schema 且 ISIZE 更大直接 fail closed，不读取记录 body。
- **通用规则**：边界未知的数据体不能用于证明自身为空。优先使用可信收据、容器元数据或 footer；若没有独立证据，就拒绝该输入。
- **牵连**：`yoyo/evaluation/spike_market_breadth_study.py::_trade_header_and_is_empty`、精简 `trades.csv.gz`、gzip 单 member 生成合同。
