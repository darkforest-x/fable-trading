# 行数命名文件并发更新时必须按时间键重新定位

- **问题**：方向 manifest 建完后，OKX updater 把 `..._<rows>.csv` 原子重命名；物化阶段
  继续读取旧 Path，`load_series` 静默跳过 OSError 并退化成较短缓存，最终 signal_i 越界。
- **死胡同**：认为增量只追加，所以整数 signal_i 永远稳定。索引本身虽稳定，读取到的文件
  集合却可能因为旧路径失效而不完整。
- **有效路径**：每个币种物化前重新枚举当前文件，要求 manifest 的全部 signal_time 都
  存在；最多刷新三次，并按 signal_time 映射到当前 index 后渲染。缺失时直接失败，不产
  部分数据集。
- **通用规则**：遇到原子重命名的数据源，跨阶段不要长期持有 Path 或行号；重新发现文件，
  用不可变业务键定位，并把“完整键集合存在”作为发布前置条件。
- **牵连**：`src/data/update_okx.py`、`src/data/loader.py`、
  `src/detection/build_direction_dataset.py`；q80 更新与离线图片生成可安全并发。
