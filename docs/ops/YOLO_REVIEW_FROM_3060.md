# 在 3060 Windows 电脑上审核 YOLO 预框

2026-09-08，Owner 要求在自己的 3060 电脑操作人工审核。已配置桌面入口，使用 Mac 上同一个 Label Studio 实例及原数据库，不迁移或复制标注库。

## 使用

1. 两台电脑接同一局域网，Mac 保持开机且 Label Studio 服务运行。
2. Windows 桌面双击 **YOLO 人工审核**。
3. 首次使用原 Label Studio 账号登录，打开项目 77；继续调整预框并提交，右侧未来 40 根可见。

入口：[项目 77 完整列表](http://192.168.1.4:8081/projects/77/data?tab=44)。当前 Default44 是完整列表，可用 Label All Tasks。若之后按周计划切到有过滤的统一待审队列，需使用 Label Tasks As Displayed；本次没有改队列或导入数据。

桌面文件：`C:\Users\Administrator\Desktop\YOLO 人工审核.url`。首次浏览器登录仍由 Owner 完成；远端验收的临时登录会话没有复制进浏览器。账号沿用现有配置，未创建新用户或更改密码，凭据没有写入快捷方式或本文。

## 实测证据与边界

| 项目 | 2026-09-08 结果 |
| --- | --- |
| Windows 身份 | 通过既有 known_hosts 严格校验的 SSH；WIN-ZZC，RTX 3060 12 GB |
| 当日地址 | Windows `192.168.1.2`，Mac `192.168.1.4`；DHCP 地址不保证永久不变 |
| Windows 到 Mac 的登录页 | HTTP 200，保持 LAN 同源地址，没有跳回 127.0.0.1 |
| Windows 到项目 API | 使用既有账号认证成功；项目 77，2,513 任务 / 2,513 预框 / 20 份人工答案 |
| 主图与 future40 | 从 Windows 实际 GET 均 200、image/png，逐字节 SHA 与已知安全样本一致 |
| 桌面快捷方式 | 写入并读回核对一致，未覆盖不同内容的旧快捷方式 |
| 服务与数据变更 | 无重启、无迁移、无新项目、无标注写入；未运行训练 |

只测预先核对的 pre-holdout 样本 `d94b586fcc04caa4585e49d8`，审核未来截止 `2025-08-11T22:00:00Z`。主图 SHA `f08ddd6cbcf0d028188207e949fbd32a1bb2262423d4eae2d4bfce1f0413549e`；未来图 SHA `b2ac773ff6818e88fdc317fdda19a7cde20e6ededf83bd12095fcc98feb06c9f`。完整机器收据保存在本机忽略目录 `output/offline_tasks/yolo_3060_review_access_20260908.json`。HTTP 与资产验证不等于已经由 Owner 在 Windows 浏览器完成拖框提交，未代替 Owner 提交任何答案。

当前 native Label Studio 使用 `label_studio_data/label_studio.sqlite3`，Python 进程监听 IPv4 全接口；另有 Docker IPv6 监听同端口。因此快捷方式使用明确 IPv4 地址，避免模糊解析连到另一实例。所有审核图片为同源相对 URL，不需要改图像路径、CSRF 设置或防火墙。

不能为这次访问重跑旧 `scripts/start_label_studio_review.sh`：它包含旧 val 包准备、Docker down/up 和初始化流程。若未来打不开，先核实 Mac 是否开机、当前 IPv4 地址和正确 native 实例；不要新建项目或重导入整个数据集来“修复”。

本轮只确认 3060 管理连接与审核访问可用，没有核定训练空闲时段、跨机版本或新训练资格。周计划的训练启动检查仍需另行完成。
