# 衍生品接口收到时间不等于结算时间或同步快照

- **问题**：轮动观察器需要给现货候选附加最新资金费率和未平仓量背景，但多个当前接口各有自己的时钟，且并非每个字段都描述已结算事件。
- **死胡同**：未采用的捷径包括：把 `lastFundingRate` 当成已经核验的结算账本；把 `fundingInfo` 未列出的币默认填成 8 小时；把先后读取的 OI 与 mark price 乘积当成交易所同时上报的美元持仓量。这些都会把字段名或缺失信息升级为没有依据的事实。
- **有效路径**：核对 Binance 官方三个接口，保留最新上报费率及未知结算时间；只有资金设置接口明确返回该币时才记录间隔。逐源记录 API event time 与实际 receipt time；未来或过旧的源遮蔽数值，跨源时间差过大时不计算乘积。允许的乘积标明 derived USDT 名义值及近似美元假设，并明确单个 OI 不能推断方向、变化或现货流入。
- **通用规则**：接行情字段前先分别回答三个问题：它是什么经济量、单位是什么、哪一个时间能证明它当时可用。字段缺失必须保留缺失，收到时间不能替代事件时间，多个接口都“最新”不能证明彼此同步。
- **牵连**：`yoyo/rotation/derivatives.py`、`tests/test_rotation_derivatives.py`。官方依据为 Binance USD-M [Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) 中 Mark Price、Open Interest、Get Funding Rate Info 三节；本轮只读取文档并用注入响应测试，没有真实行情 HTTP 请求。
