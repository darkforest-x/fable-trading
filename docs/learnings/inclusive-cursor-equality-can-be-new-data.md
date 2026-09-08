# 包含式请求边界上的等值记录可以是真正的新数据

- **问题**：原生1H采集的UNI/USELESS taker在保留期边缘被报作“无法后退”。原始页显示前页最老07-10 19:00，下一请求end=18:00，响应恰好一条18:00；记录实际更新，却触发`oldest>=cursor`。
- **死胡同**：此前已将下一游标改为`oldest-period`，解决包含边界和服务器毫秒取整，但推进检验仍借用了排他端点的严格小于条件。把请求上界当作上次已见的最老记录，混淆了两个不同时间点。见[inclusive游标](inclusive-api-cursors-need-strict-progress.md)及[有效时间精度](rubik-cursor-progress-needs-effective-time-resolution.md)。
- **有效路径**：OI/taker当前响应允许`oldest==end`，下一请求仍减完整周期，真正重复页的最老记录大于这个新边界，继续拒绝。Funding的排他after仍要求严格小于。回归覆盖OI/taker×1H/4H单条等值新页，修复前4例均失败；另保留三类端点的重复页拒绝用例。
- **通用规则**：分别核对“当前响应允许落在哪个边界”和“下一请求是否严格推进”。包含式端点的合法等值数据不等于重复数据；分页单测应包含只返回一条的新边缘页，不能只有满页和空页。
- **牵连**：`yoyo/data/okx_altcoin_snapshot.py`、`tests/test_okx_altcoin_snapshot.py`。未重拉或修改`native1h_snapshot`；原2个错误回执保持原样，近期112流的覆盖与全量原始页重放验收另记`experiments/active/exp-imacd-altcoin-trends-20260909-v1/NATIVE_1H_COLLECTION.md`。本修复未改变已完成实验的输入或收益结果。
