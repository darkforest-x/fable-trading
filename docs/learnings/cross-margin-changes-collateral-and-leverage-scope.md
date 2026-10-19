# 逐仓切全仓必须同时改变保证金口径和杠杆作用域

- **问题**：Owner 将 OKX 执行配置改为全仓；原实现按逐仓读取可用余额，并按持仓方向设置杠杆。
- **死胡同**：只把订单 `tdMode` 改成 `cross` 会保留错误的余额口径；全仓双向合约的杠杆并非每个方向独立，调低杠杆还会增加已有仓位占用，原定仓快照随即过期。
- **有效路径**：按 OKX 合约账户官方口径读取币种级 `availEq` 和 `imr`；共同校验多空持仓档位，用不带 `posSide` 的合约级杠杆设置并回读，再读取账户保证金和持仓所有权后决定是否发入场请求。止损核验与退出订单也必须使用同一配置。离线注入保证金缩减，确认只记录跳过、不发送入场订单。
- **通用规则**：保证金模式是账户与订单契约，不是页面标签；先列出可用资金、风险作用域、杠杆作用域和退出流程，再修改配置。程序资金分配不等于交易所隔离账户损失。
- **牵连**：`yoyo/okx_execution/{config,risk,engine,dashboard}.py`；125 U、2% 计划价格风险保持不变。仅更新未启动的本地配置；真实执行仍由 Owner 启动。来源：[OKX account configuration](https://www.okx.com/docs-v5/trick_en/)、[single-currency cross margin](https://www.okx.com/help/iii-single-currency-margin-cross-margin-trading)。
