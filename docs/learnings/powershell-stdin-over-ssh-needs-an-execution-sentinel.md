# SSH 下 PowerShell 标准输入成功退出不等于整段脚本已执行

- **问题**：通过 Windows OpenSSH 调用 `powershell.exe -Command -` 发送多行 staging 脚本时，SSH 返回码为 0，但远端只出现上传的 `incoming_*`，目标数据集、批处理和日志均不存在；随后 WMI 又成功返回 PID，使“已开训”的假象更可信。
- **死胡同**：只检查 SSH/WMI 的进程返回码。前者只能证明 PowerShell 进程正常退出，后者只能证明 `cmd.exe` 被创建，二者都不能证明多行脚本执行完整或目标 batch 存在。
- **有效路径**：将完整 stdin 以 UTF-16LE 编码成无换行的 PowerShell `-EncodedCommand`；staging 最后一行输出包含 run、模型 SHA 和 batch SHA 的唯一 sentinel，本地必须逐行精确匹配该 sentinel，匹配失败则绝不进入 WMI。重复执行只允许复用 SHA 完全相同的已落地文件。
- **通用规则**：跨 shell、跨操作系统执行多行远端脚本时，门控条件必须是远端业务状态的不可伪造回执，而非 transport/process 的零退出码。
- **牵连**：`scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh`；旧的 WMI PID 103004 立即退出且没有训练产物，不代表一次模型训练实验。
