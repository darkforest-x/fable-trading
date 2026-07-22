# pad200 MAD bulk 在 16GB 上要 resume+watchdog

- **问题**：MAD-on 全量重建 `dense_owner_v14_pad200` 时，单次 Python 进程会在跑了一两百正样本后被系统杀掉（jetsam/低内存），日志无 traceback，像「假死」。
- **死胡同**：只靠 `nohup`/`disown` 仍会被 IDE shell 生命周期带走；关 MAD 能跑完但会毒化 okx_*（v13 教训）。
- **有效路径**：`--resume` 幂等续跑 + 外层 watchdog 循环直到写出 `pad200_summary.json`；进程内每样本 `gc.collect()`；OMP/MKL 线程=1。用 Cursor harness `block_until_ms=0` 挂后台比裸 nohup 稳。
- **通用规则**：16GB + 每样本多帧重渲 = 默认按「可被杀」设计；完成标志只认 summary，不认「进程还在」。
- **牵连**：`scripts/build_crop_pad200_dataset.py`；`scripts/watch_v14_pad200_build.sh`；`logs/build_v14_pad200.log`；姊妹坑 [pad200-mad-gate-off-corrupts-okx-start-stems.md](pad200-mad-gate-off-corrupts-okx-start-stems.md)。
