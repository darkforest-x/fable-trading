# GUI 子进程继承 stdout 会污染桥接回执

- **问题**：Windows 已打开 BTC30m，但 Spike 收到 503，原生脚本退出码明明为 0。
- **死胡同**：把失败继续归因于权限或 URL。实际管道收到 `requested` 之后还有
  TradingView 的 `Initializing LoggerService`，严格回执校验因此失败。仅放宽字符串比较还可能在冷启动时
  等待长期运行的 GUI 关闭管道，不能解决句柄生命周期。
- **有效路径**：在拥有交互桌面的 Windows 本机桥接中，以 `ProcessStartInfo.UseShellExecute=true`
  启动已安装 Appx 包的 TradingView.exe；URL 仍经双层完整匹配校验，是单个 HTTPS 参数。
  GUI 子进程与脚本回执管道分离，真实输出恢复为单独 `requested`，随后 ETH4H 请求与实图一致。
- **通用规则**：启动器与长寿命 GUI 不应共享用于 RPC 回执的标准流。先检查退出码、原始字节和句柄继承，
  不要通过容忍未知输出掩盖生命周期问题。
- **牵连**：`yoyo/monitor/windows_tradingview.ps1`、`windows_tradingview.py`；Windows TradingView 3.4.1.8194。
