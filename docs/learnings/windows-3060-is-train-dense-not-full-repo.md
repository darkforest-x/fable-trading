# Windows 3060 是精简 GPU 箱，开训用 train_dense.py 不是 src.detection.train

- **问题**：v14 交接/`sync_v14` 打印的 WMI 一行写成 `-m src.detection.train`；真机 `C:/fable` 无 git、无 `src/`，只有根目录 `train_dense.py` + `.venv`。
- **死胡同**：按文档 `git pull` 再跑 `.ps1`——盒子本来就不是完整仓；硬调 `src.detection.train` 会立刻 ModuleNotFound。
- **有效路径**：与 H-TS / v9 一致——WMI `Create` 起 `C:\fable\train_dense.py --dataset ... --model ...`（SAFE_AUG / FINETUNE_OPT 已在该文件里）；Mac 只 scp 数据集+基座。
- **通用规则**：开训前先 `Test-Path C:/fable/train_dense.py` 与 `Test-Path C:/fable/src/detection/train.py`；有前者用前者。
- **牵连**：`scripts/sync_v14_to_windows.sh`、`analysis/p_v14_windows_train.md`、`scripts/train_owner_hts.sh`、远端 `train_dense.py`
