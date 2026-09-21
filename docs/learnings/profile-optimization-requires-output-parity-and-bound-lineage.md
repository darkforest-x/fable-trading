# Profile 优化必须同时证明输出一致和运行绑定

- **问题**：局部切片可以减少 profile 提取时对长行情前缀的重复数组计算，但只比较少数标量或局部耗时，无法证明矿工输出、队列谱系和后续入组仍然相同。
- **死胡同**：把 adapter 的局部调用耗时从约0.584秒降至约0.179秒当作端到端提速，会遗漏读取、特征、扫描、NMS、进程与写入成本；只验证函数返回也无法发现 receipt、master 或 source 目录绑定漂移。
- **有效路径**：先在180个冻结真实 profile 上逐 metric、逐 sequence 精确比对，再在一个完整1m source 上要求 raw candidates、endpoint、NMS、scores、rejections、strict 和 calibration 七个 artifact 逐字节相同。把 adapter、driver、原矿工规则、parity builder/selection 和两类 receipt SHA 写入 V2 contract；新实现只能以独立 source 目录和显式 completed-child migration 接入。
- **通用规则**：性能适配器在进入有状态队列前，先定义完整可观察输出集合并做精确 parity，再把实现和证据作为 receipt/master 的一部分绑定；局部基准只能描述局部调用，不能外推为管道吞吐或容量结论。
- **牵连**：`yoyo/datasets/ma_profit_profile_window.py`、`yoyo/datasets/ma_profit_window_miner.py`、`experiments/active/exp-ma-profit3r-20260922-v1/profile_window_contract_v2.json`、`profile_window_parity_receipt.json`、`profile_window_fullsource_parity_receipt.json`；当前全源样本为0GUSDT 1m，516 weak、262 NMS、2 strict，尚未 admission 或训练。
