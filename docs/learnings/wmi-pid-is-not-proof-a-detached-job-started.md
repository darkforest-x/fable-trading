# WMI 返回 PID 不等于 detached 任务已经启动

- **问题**：Windows `Win32_Process.Create` 返回了 PID，但远端多行 PowerShell staging 实际只执行了开头，batch、日志和训练进程都不存在；只看 PID 会把秒退误报成“训练中”。
- **死胡同**：通过 OpenSSH 把多行脚本喂给 `powershell.exe -Command -`，并把返回码 0 与 WMI 的 `PID=` 当成功证据。PowerShell 5 在该边界可能只消费首段输入，WMI 又只证明 `cmd.exe` 被创建，不证明 batch 存在或 Python 已进入训练。
- **有效路径**：把完整 PowerShell 程序以 UTF-16LE `-EncodedCommand` 一次提交；staging 完成所有文件、数据集及 SHA256 校验后输出带 run/model/batch 哈希的精确 `STAGE_OK`，Mac 端缺少该回执就禁止创建 WMI。启动后再要求日志出现 launcher、preflight、数据计数与首个 epoch，并核对 Python 子进程。
- **通用规则**：远程 detached 启动至少需要三层证据：输入落盘回执、进程创建回执、工作负载表面证据；任何单独一个 PID 都不能算“已启动”。
- **牵连**：`scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh`；PowerShell 5/OpenSSH；`STAGE_OK`；WMI PID；训练日志与 epoch。
