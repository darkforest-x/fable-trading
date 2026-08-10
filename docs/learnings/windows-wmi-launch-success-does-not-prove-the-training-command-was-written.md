# Windows WMI 返回成功，不代表训练命令真的被写入

## 场景

Mac 通过 SSH 调 Windows 3060，用 PowerShell `Invoke-CimMethod Win32_Process.Create` 启动脱离 SSH 会话的 YOLO 长训。脚本先把多行 `.cmd` 内容通过 stdin 管道传给远端 `Set-Content -Value $input`，再由 WMI 执行该文件。

## 症状

- WMI 返回 `pid=<number> ret=0`，表面像启动成功；
- 远端没有 Python 训练进程，也没有训练日志；
- `.cmd` 文件内容不是命令，而是 `System.Management.Automation.Runspaces.PipelineReader...`；
- 即使命令文件正确，上传的 `data.yaml` 仍可能保留 Mac 的 `/Users/...` 绝对路径，Windows 无法读取数据。

## 根因

PowerShell 的自动变量 `$input` 是管道枚举器，不是已经拼好的纯文本。把它作为单个 `Set-Content -Value` 参数时，某些 SSH/PowerShell 组合会写入对象的类型字符串。WMI 只负责成功创建 `cmd.exe`，不会保证 `.cmd` 内容有效，所以 `ret=0` 不是训练已启动的证据。

同时，YOLO 数据 YAML 的 `path:` 是构建机绝对路径。只复制目录而不重写该字段，会让远端训练读取不存在的 Mac 路径。

## 修复

1. 不再经 stdin 生成 `.cmd`；把完整 `cmd.exe /c "..."` 作为 WMI 的 `CommandLine` 直接传入。
2. 限制 run name 只能包含字母、数字、点、下划线和连字符，避免命令注入与引用破坏。
3. 数据解包后，在远端副本中把 `data.yaml` 的 `path:` 重写为 `C:/fable/datasets/<dataset>`；本地 YAML 保持不变。
4. 除了检查 `ret=0`，启动后必须再次核验：Python 进程存在、日志存在且已进入 dataset scan / epoch。
5. 不要根据 `project=` 参数猜 Ultralytics 产物目录。本机设置可能再套一层 `runs/detect`；启动后递归定位一次 `results.csv`，并让 status/fetch 共用同一个冻结的 `RUNS` 路径。

## 可复用原则

异步启动要分三层验收：

1. **调度层**：WMI/任务计划返回成功；
2. **进程层**：目标进程在启动后仍存活；
3. **业务层**：日志出现数据加载或首个 epoch，而不是只有 PID。

跨机器复制训练数据时，manifest/hash 可以保持平台无关，但运行时入口中的绝对路径必须在目标机显式重绑定。
