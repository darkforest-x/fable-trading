# 迁移数据的保留裁决必须回到原始视觉证据

- **问题**：统一成 W10/Core4/Confirm1 后的图片可以验证渲染与因果窗口，却不能回答原始 Owner 框、旧审核图或自动负例本身是否应当保留；把它们称作“原图审核”会把迁移假设偷偷带进裁决。
- **死胡同**：只对迁移后的 W10 图做分层盲审。即使 Gold、图片和 SHA 全部一一对应，也只能证明派生快照没有漂移，不能证明把旧窗口压成固定 4 根核心是对的。
- **有效路径**：以最终 `gold_id` 为 join key，沿 `source_dataset + source_record_id` 回到每条记录当时真实使用的 Owner 长图、easy-negative source render、V3.2 图或语义审核图；历史大文件只从当前仓的 `archive/consolidated` 按数据集资产名定位，不把已归档兄弟仓重新接回运行路径；先要求 100% 路径存在并重算图片 SHA，再生成只做 KEEP/REMOVE/UNCERTAIN 的独立审核包。
- **通用规则**：凡是数据经过重裁、重框、窗口压缩或协议迁移，语义保留/删除审核第一步必须回到原始视觉证据；派生图只能作为对照，不能冒充原图。
- **牵连**：`yoyo/datasets/fixed_w10_original_review.py`、`datasets/fixed_w10_core4_confirm1_v1/review/original_source_triage_v1/`、fixed-W10 Gold 谱系、后续新版本 manifest。
