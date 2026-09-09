# 跨设备看板的开图动作必须在访问设备执行

- **问题**：Spike 在 Mac 扫描，Owner 要求 3060 也能访问并打开 Windows TradingView。
  原 `/api/tradingview/open` 无论谁调用都执行 Mac AppleScript；只改监听地址会打开错误设备。
- **死胡同**：网页普通 HTTPS 链接不能保证进入 Windows 桌面应用，猜测自定义 scheme 又可能丢币种周期。
  同时，SSH 后台会话的启动成功不能证明登录桌面的图表正确。
- **有效路径**：Mac 保持扫描与推送唯一进程；SSH 将 Mac 8766 转到 Windows loopback 8767，
  Windows 独立 loopback 8766 网关仅代理白名单 GET，在交互用户会话本地处理开图 POST。
  具体保存布局加币种和周期作为 HTTPS argv 交给已安装 TV 包。真实 Windows 图表核验 BTC30m、ETH4H。
  网关校验原始请求路径，避免 BaseHTTPRequestHandler 对前导 `//` 的归一化扩大路由范围。
- **通用规则**：先区分数据服务在哪、用户在哪、GUI 会话在哪。原生动作与只读数据请求应明确分流；
  API 回执只是 requested，验收看真实图表正文中的交易所、币种与周期。
- **牵连**：`yoyo/monitor/desktop_client.py`、`desktop_tunnel.py`、`windows_tradingview.py`、
  `scripts/windows/install_spike_client.ps1`；Windows 必须登录，两机必须联网；DHCP 地址改变需重新确认主机。
