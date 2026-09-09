# Spike：3060 Windows 看板与本机 TradingView

2026-09-09 Owner 要求 3060 能访问 Spike，并打开 3060 自己的 TradingView。

## 使用

在 **3060 桌面双击 `Spike`**，或在它的浏览器访问：

`http://127.0.0.1:8766/#signals`

整张币种卡片打开 **Windows TradingView** 的对应 OKX 合约与周期，使用已保存的
“综合过滤”布局。卡片中的“页内预览”继续在页面看 K 线，“网页版”仍是明确的浏览器备用入口。
Mac 打开同一地址时使用原来的 Mac TradingView 桥接。

监控数据与 Bark 仍由 Mac 产生，Windows 只是显示客户端，没有启动第二份模型、扫描或推送。
两台机器需开机联网；3060 需登录 Windows 桌面。没有公网服务或新的防火墙开放规则。

## 已部署结构

```text
Windows 浏览器 127.0.0.1:8766
  ├─ 白名单 GET → Windows 127.0.0.1:8767 → SSH → Mac 127.0.0.1:8766
  └─ 同源开图 POST → Windows 登录会话 → TradingView.exe + 保存布局/币种/周期

Mac 浏览器 127.0.0.1:8766 → 原 Mac 服务与 AppleScript
```

| 项目 | 当前值 |
|---|---|
| 已核实 Windows | `WIN-ZZC` / `Administrator` / `192.168.1.2` |
| Windows 客户端目录 | `C:\fable\spike-client` |
| Windows Python | `C:\fable\.venv\Scripts\pythonw.exe`，现有 Python 3.9，无新依赖 |
| Windows 登录任务 | `SpikeDesktopClient`，Interactive / Limited，登录启动、失败一分钟后重启 |
| Windows 日志 | `%LOCALAPPDATA%\Spike\client.log` |
| Windows 布局配置 | `%LOCALAPPDATA%\Spike\tradingview-layout.txt` |
| Mac 隧道任务 | `com.spike.3060-tunnel`，KeepAlive，SSH 断连检测及自动重连 |
| Mac 隧道日志 | `~/Library/Logs/Spike/WindowsClient/tunnel-error.log` |
| 布局 | `https://cn.tradingview.com/chart/AlGc61US/` |
| Windows TV | `TradingView.Desktop` 3.4.1.8194，动态读取已安装包路径 |

`192.168.1.3` 是旧 DHCP 地址，本次连接拒绝；`.2` 已用原 SSH 主机密钥验证并读到 WIN-ZZC。
若将来 DHCP 改变，先重新确认目标主机与密钥，再修改本任务 plist 的最后一个 SSH 参数并重新加载；
不要关闭主机密钥检查，也不要因为地址失效就重启 Mac 监控。

## 实现与验收

- 网关只代理页面、静态文件、行情和信号 GET，不转发开图 POST、Cookie 或凭据。
  Host、Origin、动作头、原始路径、JSON 字段和长度均校验，拒绝跨站请求、重定向与任意上游地址。
- 开图程序校验合约和五个周期，将标准 HTTPS URL 绑定到具体保存布局，作为单个 argv 传给已安装 TV。
  3.4.1 的 Windows startup / second-instance 源码均将最后一个参数交给 `openForwardedUrl`。
  未使用猜测的 `tradingview://` 查询参数，也未改剪贴板、键盘、账户或订单。
- GUI 进程通过 ShellExecute 启动，避免 TradingView 日志污染 RPC 标准流。
  返回值始终叫 `requested`，不把进程创建等同于最终渲染成功。
- Windows 真实 API → 图表正文截图已核验 BTC-USDT-SWAP 30m、ETH-USDT-SWAP 4H、
  SOL-USDT-SWAP 1Dutc；正文对应 30、4小时、1天，OKX 与综合过滤布局一致。
  单测覆盖其余周期、币本位、USDC、无效请求、失败/超时、并发与 Mac 入口回归。
- 283 项 Python 定向测试、56 项前端测试通过。Windows 实图与网关 API 证据在
  `output/qa/spike_3060_20260909/`，不把桥接成功解释为策略盈利验证。
- Mac `started_at_ms=1788954302965` 保持，没有为安装客户端重启扫描服务。
  日线与原有周期、Bark/YOLO 两阶段、TG 关闭口径保持。扫描中 health 的 market_ready
  可能暂为 false；须结合 scan.status/completed/errors 判断，不因单个布尔值反复重启。

## 复现部署

只同步以下显示层文件，**不复制整个仓库、数据、模型或配置密钥**。已有用户任务若同名但描述不同，
安装器拒绝覆盖；重复部署会短暂重启此显示客户端，不会重启 Mac 扫描。

Mac 仓库根目录：

```sh
ssh Administrator@192.168.1.2 'New-Item -ItemType Directory -Force C:\fable\spike-client\yoyo\monitor | Out-Null'
scp yoyo/__init__.py Administrator@192.168.1.2:/C:/fable/spike-client/yoyo/__init__.py
scp yoyo/monitor/__init__.py yoyo/monitor/tradingview.py yoyo/monitor/windows_tradingview.py yoyo/monitor/windows_tradingview.ps1 yoyo/monitor/desktop_client.py Administrator@192.168.1.2:/C:/fable/spike-client/yoyo/monitor/
scp scripts/windows/install_spike_client.ps1 Administrator@192.168.1.2:/C:/fable/spike-client/install_spike_client.ps1
ssh Administrator@192.168.1.2 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\fable\spike-client\install_spike_client.ps1 -LayoutUrl https://cn.tradingview.com/chart/AlGc61US/'
.venv/bin/python -m yoyo.monitor.desktop_tunnel Administrator@192.168.1.2
.venv/bin/python -m pytest tests/monitor/test_desktop_client.py tests/monitor/test_windows_tradingview.py tests/monitor/test_tradingview.py -q
node --test tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

Windows PowerShell 只读检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/desktop
Invoke-RestMethod http://127.0.0.1:8766/api/health
Get-ScheduledTask SpikeDesktopClient | Select-Object TaskName, State
```

第一项应为 `target=windows / source=mac / transport=ssh-loopback`。
若第一项成功但第二项 502，检查 Mac 隧道与两机网络；若两项正常而开图失败，
检查 Windows 登录会话、TV 安装和保存布局。仅为开图验证创建的 `SpikeClientQA` 临时任务验收后移除。
不输出原始 launchctl 环境（可能含其他凭据）。

## 依据与限制

TradingView 官方说明 Windows 支持网站应用关联；浏览器点击 HTTP(S) 链接仍可能停留在浏览器，
因此这里使用本机桥接。[TradingView 说明](https://www.tradingview.com/support/solutions/43000708221-how-in-app-link-handling-works-in-tradingview-desktop/)、
[Microsoft 网站应用关联说明](https://learn.microsoft.com/en-us/windows/apps/develop/launch/web-to-app-linking)。
具体布局加 symbol/interval 的传递以本次已安装版本实测为依据；应用升级后需重新抽查实图。
当前原生入口会按 TV 行为打开图表标签，未实现自动合并/关闭既有标签。
