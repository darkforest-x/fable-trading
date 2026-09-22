# SSH响应解码失败不等于远端任务没有执行

- **问题**：负例重训的普通detached启动返回PID后消失，未写任务回执。改用本仓已有WMI方式后，本机又收到UnicodeDecodeError，但远端实际已经启动。
- **死胡同**：只看Popen返回PID会把未启动当已启动；只看SSH工具报错又会把已经启动当失败，盲目重试可能同时训练两份。该次WMI返回值为0，错误来自本机按UTF-8解码非UTF-8 stderr，并非WMI执行失败。
- **有效路径**：先用实际PID、空日志、缺失job/training回执确认第一次调度确实未进入业务。随后用SHA确认的cmd文件和完整EncodedCommand运行旧项目已验证的WMI路径。解码异常后只读远端wmi_launch/job_receipt，核对进程持续存在；取回已成功的回执，不重发启动。
- **通用规则**：跨机操作的传输/解码异常属于结果未知。下一步先恢复权威远端状态；只有明确未执行，才允许重新派发。训练成功启动至少需要存活进程、业务日志，随后再以epoch进度证明。
- **牵连**：`experiments/active/exp-ma-profit3r-negatives-20260922-v2/run_supervised.py`、`recovery_launch.json`、`remote_launch.json`。模型、数据、评估代码与参数的冻结SHA未改；未宣称新训练已完成。WMI来源见[既有启动教训](windows-wmi-launch-success-does-not-prove-the-training-command-was-written.md)。
