# torch+lightgbm 同进程加载两份 libomp，OMP 多线程时启动即段错误

- **问题**：2026-07-20 晚 val 窗 v12 扫描（`scripts/scan_v12_valwin.py`）连续 4 次
  启动后 4–20 秒 SIGSEGV（EXC_BAD_ACCESS at 0x8/0x10/0x30），崩溃报告的 procPath
  是 Xcode 系统 Python 3.9，一度怀疑是"没走 .venv、import 到不兼容 native 包"。
- **死胡同**：按 procPath 归因"用错解释器"是错的——本仓库 `.venv` 的基座就是
  Xcode Python 3.9.6（`pyvenv.cfg` home = /Applications/Xcode.app/.../usr/bin），
  所以 .venv 进程在 ps / 崩溃报告里的 procPath 同样显示 Xcode framework Python，
  procPath 无法区分 venv 与系统 python。要看崩溃报告 usedImages 里加载的
  site-packages 路径（本次含 venv 的 libtorch/lib_lightgbm/cv2，说明确实走了 .venv）。
- **有效路径**：崩溃栈全部在 `libomp.dylib __kmp_suspend_64 / __kmp_fork_barrier`
  的 OpenMP worker 线程上；usedImages 显示同进程加载了两份 libomp——
  lightgbm 的 `lib_lightgbm.dylib` rpath 指向 `/opt/homebrew/opt/libomp/lib`，
  而 torch 自带 `torch/lib/libomp.dylib`。双 OpenMP 运行时在多线程 fork barrier
  处互踩 → 空指针段错误。带 `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` 重启后
  不再产生 OMP worker 线程，进程稳定运行。
- **通用规则**：本机凡是同进程用到 torch + lightgbm（或任何两个各带 OpenMP 的
  native 包）的脚本，必须 `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` 启动；
  排查 macOS Python 段错误先看 .ips 的 faulting thread 栈和 usedImages，
  不要只看 procPath。
- **牵连**：`scripts/scan_v12_valwin.py`（docstring 已写明该用法）、
  `.venv/lib/python3.9/site-packages/lightgbm/lib/lib_lightgbm.dylib`（rpath →
  homebrew libomp）、`torch/lib/libomp.dylib`；崩溃报告在
  `~/Library/Logs/DiagnosticReports/Python-*.ips`。
