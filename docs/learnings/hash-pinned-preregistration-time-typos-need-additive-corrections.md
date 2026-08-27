# 已锁哈希的预注册时间写错后只能追加校正

- **问题**：数据集构建完成后发现预注册的手写 `created_at` 和报告日期后缀晚于真实墙钟；选择、切分和渲染没有读取这些字段，但 plan/build receipt 已经锁定整份预注册 SHA。
- **死胡同**：直接把原 JSON 时间改成正确值会让构建回执引用一个磁盘上已不存在的配置哈希；反过来假装时间没错，又会让实验时间线与 Git 提交、文件 mtime 冲突。
- **有效路径**：不改已生成产物所引用的冻结字节，新增同目录校正回执，列出错误字段、原配置 SHA、Git commit time、产物 mtime 和“对数据/标签/holdout 无影响”的边界；后续报告引用校正后的权威时间线。
- **通用规则**：预注册中任何墙钟字段都应由程序在提交前注入并校验不晚于当前时间；一旦产物已锁整份配置 SHA，元数据勘误必须追加而不能重写历史字节。
- **牵连**：`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-v1/preregistration.json`、`results/provenance_time_correction.json`、plan/build receipt 的 `preregistration_sha256`、实验报告日期。
