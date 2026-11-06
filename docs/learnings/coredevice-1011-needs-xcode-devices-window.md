# CoreDevice 1011：先让 Xcode 设备窗口把手机连上，再用 devicectl 安装

- **问题**：纪律 App 0.8.10–1.0.0 多次装机失败，`devicectl device install` 报 `CoreDeviceError 1011`（找不到设备），`list devices` 显示 unavailable。此时 USB 上能看到 iPhone（`ioreg` 有 "iPhone"），配对状态正常，开发者模式也已打开。
- **死胡同**：反复轮询 `devicectl list devices`、解锁后重插数据线、只把 Xcode 打开，都不会建立 tunnel（`tunnelState: unavailable`，`ddiServicesAvailable: false`）。`devicectl device info lockState` 同样报 1011。日志里也看不出原因。
- **有效路径**：请 owner 在 Xcode 按 ⇧⌘2 打开 Devices and Simulators 并选中这台手机。窗口显示 Connected 后，`devicectl` 立刻显示 connected，安装成功。安装成功不等于能启动：首次启动报 "profile has not been explicitly trusted"，需要在手机的"设置 → 通用 → VPN 与设备管理"里信任个人开发者证书。免费证书过期或旧 App 被移除后，这个信任会失效。
- **通用规则**：装机遇到 1011 时，不要反复重试，直接请 owner 打开 Xcode 设备窗口选中手机。装完用 `devicectl device info apps` 回读版本号，再用 `process launch` 验证能启动；启动被拦就请 owner 信任证书。不要去杀 remoted 等系统进程。
- **牵连**：`releases/build-*.json` 里历次 1011 失败记录；Personal Team 签名约 7 天过期。相关：[不签名的模拟器包写不了 Keychain](unsigned-simulator-builds-cannot-write-keychain.md)。
