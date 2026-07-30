# 行数入文件名的数据目录部署时必须镜像删除

- **问题**：本地 456 个 15m 文件部署后，VPS 累积到 1,133 个；每个序列有 2–5 个旧版本。
- **死胡同**：只看 loader 的 456 个去重后 series 会误以为部署正常，掩盖磁盘膨胀和看板 file count 失真。
- **有效路径**：直接按去掉行数后缀的序列键统计远端重复，再把 `ma206/` 与 `kline_fetched/` 分别用 `rsync --delete` 镜像；重部署后 456/456 且重复为 0。
- **通用规则**：只要生产者通过重命名表达版本，部署就必须删除目的端旧名；验收同时报物理文件数、逻辑键数和重复键数。
- **牵连**：`src/data/update_okx.py` 的 `{rows}` 文件名、`scripts/deploy_vps.sh`、VPS `/opt/fable-trading/data/kline_fetched`。
