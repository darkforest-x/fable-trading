# 16GB 上 YOLO tip 重渲必须 batch=1 + 分片进程 + 每币落盘

- **问题**：tip 子集 rerender 在约 60/257 币处反复死掉；日志有进度但 CSV 几乎空，
  16GB 机器 Jetsam；残留几十张 PNG。Owner 要求 workers=1、RSS 压在 ~4GB。
- **死胡同**：① 只设 `OMP_NUM_THREADS=1` 但按币把最多 39 张图一次 `predict`——
  峰值仍可能爆。② 全程单进程 + 结束才写 CSV——被杀等于零进度。③ `nohup` 挂在
  Cursor 沙箱 shell 里——父 shell 结束会收割子进程，看起来像「随机死」。
- **有效路径**：predict **batch=1 / workers=0**；每币写完立刻 checkpoint CSV；
  PNG 用完即删（per-pid tmp）；外层 bash 每 N 币新开 Python（`--max-symbols`）
  隔离 SIGSEGV；整段驱动必须前台长跑或真正 daemon，不能依赖会被收割的后台。
- **通用规则**：本机 16GB + ultralytics 重渲 → 默认 batch=1、可续跑落盘、分片进程；
  跑前先看是否有别的重 YOLO；RSS 采样写入旁路日志，>7.5GB 硬杀。
- **牵连**：`scripts/tip_subset_backtest.py`、`scripts/run_tip_subset_rerender_chunked.sh`、
  `analysis/p_tip_subset_val.md`；与
  `docs/learnings/lightgbm-import-before-ultralytics-predict-segfaults.md` 互补
  （那边是双 libomp，这边是内存/生命周期）。
