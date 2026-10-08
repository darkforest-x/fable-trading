# 跟单抓到的消息正文混着译文和被回复的消息，意图只能从作者原话判断

- **问题**：Tareeq 发 “Longed met at 0.455 sl; 0.4375 (0.5% risk, bybit)”，系统判成“平仓 / 已有交易止损结果”，没有开仓。核对 10/07–10/08 的 112 条消息，又发现 John 两单把入场价当成止损、Michele 一段评论被判成 BTC 平仓、“3:1 盈亏比”被当成币种 “3” 的平仓、“NEAR: Stops moved to 4H＜4.2” 被改成 MUBARAK 止损 4。
- **死胡同**：先怀疑提示词不够清楚，想加规则教模型区分开仓和战报。但模型其实读到了被污染的输入：浏览器翻译插件把译文接在 31 个减号后面（“sl;” 被译成“已达止损”），Discord 的回复/转发预览也作为新的一行（“@WG Bot 1970-01-01 08:00BTC: Closed in profits (100%)”）进了正文。只改提示词，模型仍会被这些文字误导。
- **有效路径**：入库文本先拆开（`yoyo/copier/ai/text.py`）：减号规则之前是作者原话；之后的译文丢弃；带“作者 日期 时间”抬头的行是引用，只进上下文。意图、市价/限价判断和频道上下文都只用原话。固定格式另走本地规则，不交给模型：“Longed X (at) P sl S” 直接解析出入场 P、止损 S；币种必须以字母开头；“Stops moved to 4H<4.2” 解析为条件止损 4.2；“Limit order cancelled” 一律撤单。
- **通用规则**：网页抓取的文本，先确认哪些字是作者写的，再谈意图识别。查误判时把原始入库文本（不是界面摘要）和解析出的字段逐条对照；先在历史消息上离线重放解析器，确认改动只影响该改的条目。
- **牵连**：`yoyo/copier/ai/deepseek_client.py`、`yoyo/copier/exchange.py`（市价判断）、`yoyo/copier/router/action_router.py`（频道上下文）、`tools/discord_copier_extension/content.js`（`getMessageContent` 读取全部 messageContent 节点）、`tests/copier/test_parser.py`。相关：[跟单回报的百分比](trade-tracker-reports-describe-the-authors-trade-not-ours.md)。
