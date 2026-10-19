# 后台刷新行情能消除接口等待，但不能延长报价有效期

- **问题**：模拟开单总览只读采样出现约 8 ms 至 2.5 s 的波动。请求线程在缓存到期后同步访问 OKX，并持有其他读取者共用的锁；后台撮合还反复构建包含全部历史的页面快照。
- **死胡同**：直接增加缓存有效期会让旧报价更久地参与成交；仅替换成 Redis 仍会保留同步回源等待。只改页面轮询也不充分，撮合线程仍调用完整历史快照。只让前端成交后异步刷新，则还需防止此前发出的旧轮询响应覆盖成交回执。
- **有效路径**：同一长期 HTTPX 客户端服务独立的行情、标记价、合约目录和档位刷新器，读接口仅取已发布快照。目录与风险复用同一次合约信息响应，选币时预热风险档位。读取时继续核验原始 source_ms/received_ms，失败刷新不修改旧时间戳。当前账户持仓、撮合风险检查与历史分页使用不同查询；历史页仍完整汇总每个仓位的全部成交。成交回执可附带当前账户快照，前端立即释放 busy，并以操作版本号拒绝旧轮询覆盖；估算接口不生成可执行报价 ID、不落库。
- **通用规则**：缓存刷新周期、报价可用期限、数据库持久化职责分别定义。性能测试必须覆盖刷新阻塞、行情过期、上游失败、重复提交、迟到响应和历史分页完整性；不要用放宽资金或风险校验换取响应速度。
- **牵连**：`yoyo/research_workspace/paper_desk_market.py`、`paper_desk_api.py`、`paper_desk_risk.py`、`paper_desk.py`、`paper_desk_limits.py`、`paper_desk_analytics.py`、`yoyo/monitor/static/paper-desk.js`、`tests/research_workspace/test_paper_desk_performance.py`、`tests/monitor/frontend_paper_desk.test.cjs`。连接池依据：https://www.python-httpx.org/advanced/clients/ 。
