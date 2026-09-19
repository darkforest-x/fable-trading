# Entry admission must preserve raw opposite exits

- **问题**：An owner-selected higher-timeframe direction filter must reject new entries without suppressing an opposite confirmation that closes an existing reference.
- **死胡同**：Filtering the upstream raw event conflates entry permission and exit evidence. Rechecking every later breakout would additionally change the established box-first pairing contract, although only entry admission was requested.
- **有效路径**：Freeze raw direction before the new gate; read the previous completed HTF average with its offset inside request.security, validate its clock, and combine the result only with final entry admission. Keep exits on raw direction. Independently version both Pine consumers, compare the remaining full source against each parent, then compile and inspect both native scripts.
- **通用规则**：For any new filter, trace raw event, admission, position lifecycle and downstream notifications separately. An entry gate is neither an exit rule nor a perpetual revalidation of an existing position's context.
- **牵连**：SPIKE V12.3/V9.1; chart-open confirmed H1 SMA60 on15m; existing box-first joints may still follow an earlier admitted frame. Native H1 versus complete5m-aggregated data parity remains a separate limitation.
