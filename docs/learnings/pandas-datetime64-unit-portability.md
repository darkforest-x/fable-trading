# pandas datetime64 的时间单位不可移植——epoch 换算别用 astype(int64)

- **问题**：看板信号页在本机正常、部署到 VPS 后 K 线"消失"。API 数据看似正常，
  实际蜡烛时间戳全变成了 1970 年附近的小数值。
- **死胡同**：先后怀疑了前端竞态、图表库尺寸、浏览器缓存、数据文件损坏——
  每个都"修"了一轮（其中部分确实是真问题），但主症状不退。
- **有效路径**：在真实浏览器里打印两侧时间基准，发现蜡烛时间恰好差 1000 倍。
  根因：`open_time.astype("int64") // 10**9` 假设 datetime64 是纳秒精度；
  VPS 上的 pandas 版本把它解析成了微秒精度（datetime64[us]），int64 变成微秒数，
  除以 1e9 后差 1000 倍。修复：`(ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)`
  ——Timedelta 除法是单位安全的。
- **通用规则**：任何跨机器运行的代码里，datetime → epoch 一律用 Timedelta 除法或
  `.timestamp()`，永远不要 `astype(int64)` 再手动除；两台机器"同一份代码不同表现"时，
  第一步打印双方的**数据本身**而不是调逻辑。
- **牵连**：`src/webapp/server.py` chart 接口；本机 pandas 与 VPS pandas 版本不同。
