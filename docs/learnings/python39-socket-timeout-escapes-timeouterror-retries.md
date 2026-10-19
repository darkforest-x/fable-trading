# Python 3.9 的 socket.timeout 不是 TimeoutError，重试逻辑会漏掉它

- **问题**：给 VIDYA H 下载 20 个山寨币的 Binance 15m 归档（约 1,300 个文件）。跑了 8 分钟后，整批任务因为一次 `socket.timeout: The read operation timed out` 中断。`yoyo/data/binance_um_archives._request_bytes` 写了 5 次重试，但这次没有重试就直接抛出。
- **原因**：`_request_bytes` 只捕获 `urllib.error.URLError` 和 `TimeoutError`。从 Python 3.10 起，`socket.timeout` 才是 `TimeoutError` 的别名；本仓 venv 是 3.9（与 CI 一致），读响应体（`response.read()`）时超时抛的是 `socket.timeout`，它是 `OSError` 的子类，但不是 `TimeoutError`，也不是 `URLError`。连接阶段的超时会被 urllib 包成 `URLError`，所以平时看不出问题，只有大批量下载、网络抖动时才会暴露。
- **死胡同**：只降低并发。并发降低后失败变少了，但单次读超时仍会让整批任务退出。
- **有效路径**：在调用方再包一层重试，捕获 `socket.timeout`、`ConnectionError`，以及 helper 抛出的传输类 `BinanceArchiveError`（"request failed" / "HTTP"）；校验和与解析错误一律不重试。已经下载、并且校验和对得上的 ZIP 会被直接复用，所以重跑等于断点续传。并发降到 5。
- **通用规则**：在 Python 3.9 上写网络重试时，`except` 里要显式列出 `socket.timeout`（或直接用 `OSError`），不要假设 `TimeoutError` 能兜住读超时。重试只针对传输失败，数据校验失败必须立刻报错。
- **牵连**：`experiments/active/exp-vidya-h-altcoins-20261012-v1/fetch.py`（`retry`）。共享 helper `yoyo/data/binance_um_archives.py:_request_bytes` 本身还没修。
- **同次的另一个 3.9 坑**：用 `importlib.util.module_from_spec` 加载含 `@dataclass` 的脚本时，要先把模块放进 `sys.modules[spec.name]` 再 `exec_module`。否则 3.9 的 dataclasses 解析注解时找不到模块，会报 `'NoneType' object has no attribute '__dict__'`。
