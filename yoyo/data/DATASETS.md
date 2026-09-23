# 本地币圈数据集

统一入口：`http://127.0.0.1:8766/#datasets`，文件入口：`data/crypto/`。

- `market/`：集中存放离线行情；原路径保留兼容软链接，冻结清单无需改写。
- `vision/`、`features/`、`research/`、`archives/`：分类入口，指向各自版本。图像、标签、事件谱系和 train/val/test 不合并。
- `_catalog/`：可重建 SQLite 元数据索引、整理日志和后台索引状态。页面只读索引，不加载行情到内存。

外部只读 `data/kline_cache` 保留原位。账户凭据、执行账本、实时监控运行目录不纳入本次整理。新数据仍由原授权采集流程产生；工作台索引和读取接口不会联网下载。

```bash
.venv/bin/python -m yoyo.data.dataset_catalog index
```

数据集详情显示 ID、币种、周期和源文件。回测按明确数据集读取 `[start,end)`，时间须对齐 K 线周期；此接口每块 50,000 行，仅把所选区间拼成 DataFrame，默认最多 2,000,000 根。大型研究按币逐个处理。

```python
from yoyo.data.dataset_catalog import read_market_data

frame = read_market_data(
    dataset_id="从数据集页面复制实际 ID",
    symbol="ETHUSDT", timeframe="5m", exchange="binance",
    start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z",
)
```

缺源、缺口、未收盘、跨来源或冲突 K 线会报错，不自动拼凑或下载。历史版本保留；目录存在和可读取不等于训练/生产准入。现有 V12.8 回测仍复用同一冻结快照，成本、规则和清单未改动。

整理命令仅用于已核实没有写者的本地目录：`python -m yoyo.data.dataset_maintenance move <明确路径...>`。移动日志先于 rename 落盘；中断后以相同参数重跑恢复兼容入口。`link-views` 建立分类入口；`deduplicate` 在 macOS/APFS 上对同名同大小且 SHA256 完全一致的文件共享底层块，保留不同 inode 和独立修改行为。它不按图像相似度、标签或收益删除样本。该命令在索引完成后运行，清理回执可核对被替换的冗余文件。

页面大小是文件逻辑字节总量；APFS 写时复制、快照和版本引用会让它不同于磁盘实际占用。去重回执记录已处理的重复字节，不承诺等量即时可用磁盘空间。
