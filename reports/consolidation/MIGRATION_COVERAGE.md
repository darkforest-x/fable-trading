# 迁移覆盖率审计

生成于 `2026-08-19T17:29:30+00:00`

每个来源仓的**每一个 tracked 文件**必须落进恰好一个桶。
落不进任何桶的就是缺口——按定义，不靠谁想起来。

| 来源仓 | tracked | 已迁移 | 按类排除 | **未归类** |
|---|---|---|---|---|
| `darkforest-x/darkforest-one` | 68 | 9 | 59 | **0** |
| `darkforest-x/yolo-xx` | 150,810 | 22 | 150,788 | **0** |
| `darkforest-x/yoyo-trading` | 15,416 | 109 | 15,307 | **0** |
| `darkforest-x/yoyo-eth` | 1,305 | 25 | 1,280 | **0** |

## 排除类别与理由

### `rendered_images` — 吸收 12,106 个文件

Rendered chart frames and review galleries. Task book 3.6 keeps them out of git; they are regenerable from the bars plus the renderer, and the archived repository keeps the originals at a known commit. Mirrored onto this machine at archive/consolidated/ since 2026-08-20, so archiving the sources does not remove them locally.

### `model_weights` — 吸收 30 个文件

Weights and cached tensors. Registered in artifacts/registry.yaml by SHA-256 and storage_uri, never copied -- one physical file keeps one identity. The bytes are mirrored at archive/consolidated/ too.

### `market_data_csv` — 吸收 747 个文件

Raw OHLCV pulled from OKX. Not research output: re-fetchable, and this repository already carries its own data/kline_fetched/ as the single writer (CLAUDE.md live-trading rule 9). Mirrored at archive/consolidated/ for reference; never read by the live path.

### `training_runs` — 吸收 43 个文件

Training run directories -- checkpoints, epoch logs, per-run scan output. The conclusions are in the migrated reports; the runs are the workings, mirrored at archive/consolidated/ and not in git.

### `dataset_image_indexes` — 吸收 153,745 个文件

YOLO label sidecars and image indexes, meaningless without the pixels they index. Both are mirrored together at archive/consolidated/.

### `competing_governance` — 吸收 27 个文件

Each source repository's own governance and packaging. Task book 8.4 forbids migrating competing statements of current truth -- one repository means one HANDOFF.

### `source_code_superseded` — 吸收 733 个文件

Source and research trees whose migrated subset is recorded in the ledger. What is not in the ledger from these trees was superseded by this repository's own implementation, or is an intermediate product of a migrated report. Listed per repository below so the residue stays visible.

### `tooling_dotfiles` — 吸收 3 个文件

Editor and toolchain pins belonging to the source repository. darkforest-one's .python-version reads 3.11, which is the constraint that made its pydantic config REFERENCE_ONLY rather than portable -- the fact is recorded in experiments/historical/darkforest_one/README.md, so the file itself adds nothing here.

### `noise` — 吸收 0 个文件

Finder metadata, bytecode caches and run logs. Not authored, not read by anything, and regenerated on the next run -- migrating them would only add files that differ between machines for reasons nobody chose.

