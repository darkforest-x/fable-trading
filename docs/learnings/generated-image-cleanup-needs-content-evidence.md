# 派生图片的清理依据必须落到内容，不能只看路径和生成记录

- **问题**：2026-09-14 清理旧 YOLO 产物时，两套派生数据集均保留了源数据集和构建脚本，但这不足以证明整个图片目录都可以按原样恢复。
- **死胡同**：把“输入路径仍存在”和 receipt 中的可重建声明当成结论。crossbox 的构建脚本会重编码正例图片，逐文件比对发现 7,958 张图片与源文件 SHA-256 不同。另一个 raw-detections 预览集的权重仍匹配记录，但当前 renderer 的 SHA 已变化；仅有当前输入并不能证明可复现旧图片。
- **有效路径**：以保留的 canonical 数据集为锚，按同一相对路径逐张计算 SHA-256，并绑定文件身份、大小和时间戳；删除前检查打开文件引用和身份稳定性。最终删除 crossbox 的 24,000 张以及 outcome 的 30,944 张精确副本，保留不同字节的图片、全部标签、manifest 和构建记录。清理标记明确说明派生目录图片不完整，使用前需从原图恢复。差异只表明文件字节不同，不能据此推断标签或视觉语义不同。
- **通用规则**：删除副本需要“保留对象与目标逐字节相同”的证据；删除可重建产物需要生成器版本、输入版本和生成参数的证据。路径存在、目录名称与历史 receipt 都只能提供线索。核验不需要重跑训练或读取行情、标签、评估结果。
- **牵连**：`scripts/build_15m_ma_launch_grade_a_crossbox.py`、`scripts/build_15m_ma_launch_grade_a_outcome_labels.py`；逐图证据及删除记录位于本机 `~/.local/state/disk-cleanup/20260914/yolo-derived-duplicate-images-{preflight,cleanup}.json`。原始记录未改写；跨文件恢复后的时间戳或哈希变化需要重新核验。相关规则见 [文件内容与身份要绑定](temp-cleanup-proof-must-bind-content-and-file-identity.md)。
