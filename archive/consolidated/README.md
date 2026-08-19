# 四个归档来源仓的大体积产物

> 20.5 GB / 30 万个文件。**整棵树不进 git**（`.gitignore` 的
> `archive/consolidated/**`），只有本文件和 `MANIFEST.json` 例外。
> 生成方式：`python3 tools/consolidation/mirror_bulk_artifacts.py`

## 这是什么

2026-08-20 owner 指示「全部迁移过来，改 gitignore，大文件不提交」之后，
把 `darkforest-one` / `yolo-xx` / `yoyo-trading` / `yoyo-eth` 四个来源仓里
**没有进入策展迁移**的产物整体克隆到本仓，这样归档来源仓之后本机仍然完整。

用 APFS 写时复制（`cp -c`）克隆：磁盘增量约为 **0**，且**源仓保持完好**——
这一点是必需的，`migration_ledger.jsonl` 里多条 `REFERENCE_ONLY` 记录
按 commit + SHA 指回那些仓。

## 这不是什么

**不是策展来源。** 有价值的文本已经按 provenance 逐份迁到正式位置并记进台账：

| 内容 | 正式位置 |
|---|---|
| 金标行、盲审裁决、解盲答案、争议裁决 | `datasets/annotations/` |
| Dataset manifest | `datasets/manifests/` |
| 四仓的结论报告（26 份） | `experiments/historical/` |
| 逐文件迁移记录（165 条） | `reports/consolidation/migration_ledger.jsonl` |

**要引用某个结论，引用上面那些，不要引用这棵树。**
这里的同名文件是未经策展的原始副本，两者一旦分叉，台账里那份才是准的。

## 布局

```
archive/consolidated/
├── yolo-xx/{datasets,reports,data,runs,weights,build}/
├── yoyo-trading/{datasets,runs,reviews}/
├── yoyo-eth/{reports,artifacts}/
└── darkforest-one/data/
```

原仓内的相对路径原样保留，所以旧报告里写的路径能直接对上。

## 没有克隆进来的

yolo-xx 与本仓重复的 4 个数据集（约 1.6 GB）：
`dense_owner_short_star_tip_v10`、`dense_owner_side_short_tip_v3`、
`eth_3m_short_pilot_v1`、`eth_short_tip_label2000`。

逐文件核对过：文件名集合与大小一致，抽样 12 个文件哈希全同，
**唯一差异是 `data.yaml` 里烘进去的仓库路径前缀**，本仓那份才是对的。

## 注意

- 这棵树是**只读备份**。要改数据集就在 `datasets/` 下按正常流程建新的 `dataset_id`，
  不要原地改这里的文件。
- 磁盘上它与源仓共享数据块（写时复制）。**改动其中一份会分裂出真实副本并占用空间。**
- 源仓归档但不删除。这棵树是本机冗余，不是唯一副本。
