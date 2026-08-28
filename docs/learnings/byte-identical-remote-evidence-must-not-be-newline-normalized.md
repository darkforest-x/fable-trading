# 远端证据的字节哈希优先于换行风格统一

- **问题**：Windows 训练机取回的 YAML、CSV、JSON 与 JSONL 使用 CRLF；Git 的 whitespace 检查会把每行末尾的 `CR` 报为 trailing whitespace，但回执已经用远端 SHA-256 锁定了这些原始字节。
- **死胡同**：把文件机械转换为 LF 虽能让 `git diff --check` 通过，却会改变 SHA，导致“远端文件、取回文件、评估回执”三者不再逐字节一致；只在报告里保留哈希而不归档文件，也会削弱恢复能力。
- **有效路径**：对已锁哈希的少数远端证据使用精确路径的 Git `binary` 属性，关闭文本差异和换行转换，同时继续用独立脚本解析其内容并核对 SHA 与大小。
- **通用规则**：先判断文件是不是已进入外部证据链；若是，禁止为了格式检查改写字节，改用窄范围 `binary` 属性。只有尚未锁哈希的本地产物才可统一换行。
- **牵连**：`.gitattributes`、训练回执中的 fetched-file SHA、冻结验证 receipt/JSONL、`git diff --check`，以及 Windows→macOS 的 `scp` 取回链路。
