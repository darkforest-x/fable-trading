# 时间戳序列化差异要规范化类型，不能删掉时钟列来过验收

- **问题**：逐列比较旧CSV与新重放时，`transition_armed_at` 显示完全相同仍失败。
- **死胡同**：只检查 pandas datetime dtype 不够；同一列包含 Timestamp 和 NaT，合并后可能是 object，CSV另一侧则是字符串。把时钟字段移出parity或放宽数值容差会隐藏真实时序错误。
- **有效路径**：仅对非空值全部为Timestamp的object列显式转UTC，CSV一侧同转；全列仍逐项核对，补混合object/NaT与+1ns必须失败的合成测试。失败运行保留原始receipt和请求产物，不覆盖后假装首次成功。
- **通用规则**：先区分表示类型差异和真实值差异；规范化只改变表示，验收覆盖和时间精度不能因此缩水。
- **牵连**：`yoyo/evaluation/hourly_impulse_realign_research.py::assert_saved_columns`、对应测试、V6 `ATTEMPTS.md`。没有改变信号、止损、成本、对照或时间范围。
