# USB 能看到 iPhone 不代表 CoreDevice 调试连接可用

- **问题**：USB 与 Instruments 设备清单能看到已信任的 iPhone，但 devicectl 显示 unavailable，安装报 1011，重新配对也报连接失败。
- **死胡同**：重复读取 device info 只得到缓存设备属性，不证明当前已建立调试隧道；反复要求用户换线或解锁不能修复 Mac 端卡住的调试进程。
- **有效路径**：先分别确认 USB、Instruments 与 CoreDevice 的实际状态。核实 CoreDeviceService 进程路径和用户归属后，仅重启该用户调试进程；本次随后出现 connecting、取得 tunnel、启用 DDI，0.8.5 安装成功。业务服务独立运行，未重启全局 USB 服务或修改手机信任关系。
- **通用规则**：设备枚举、调试隧道、安装成功、版本读回是四个不同证据层级，不能互相替代。精确重启已核实的故障进程后再验证，禁止宽泛 killall。
- **牵连**：`xcrun devicectl`、`xcrun xctrace list devices`、`ioreg -p IOUSB`、用户级 `CoreDeviceService`。这是一次实测恢复路径，并不证明所有 unavailable 都由同一原因造成。
