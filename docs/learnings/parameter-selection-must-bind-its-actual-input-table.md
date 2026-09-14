# 参数选择必须绑定实际读取的表

- **问题**：资金模拟的原始行情和特征缓存有SHA，但参数选择直接读取派生development CSV。CSV被替换或陈旧时，源码与配置干净仍不足以证明选择血缘。
- **死胡同**：只检查原始文件哈希、缓存哈希与selection里的配置哈希，漏掉了真正进入选择函数的中间表。只读复核在首次开发选择之后、任何验证或holdout评估之前发现此缺口。
- **有效路径**：从已绑定缓存与同一窗口规则重新生成development表，逐字节核对输入CSV，再将其哈希写进selection。合成测试把0.2收益改成20时必须拒绝。首次选择另存initial收据，校验后重新选择，要求政策与开发数值完全一致；不因血缘修复改变研究参数。
- **通用规则**：参数或报告的来源声明必须覆盖实际读取的每一条派生输入边，不能跳过CSV这一层。
- **牵连**：`yoyo/evaluation/spike_eth_martingale_study.py:verified_development_csv`、对应合成篡改测试及`experiments/active/exp-spike-eth-martingale-20260914-v1/selection.json`。该修复只涉及证据链，不读取holdout、不改变V8、费用或仓位规则。
