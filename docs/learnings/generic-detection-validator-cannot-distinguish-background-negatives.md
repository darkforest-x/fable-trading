# 通用检测校验器无法自行区分合法背景负样本

- **问题**：Datumaro 对一个含显式背景负样本的 YOLO 数据集执行 detection validation 时，把全部空标签图片列为 `items_missing_annotation`。在本项目里，这些图片可能正是按 manifest 构造的合法负样本，不能据此判为坏数据。
- **死胡同**：直接把通用校验报告的 warning 数量当作数据集错误数。工具只看到“图片没有框”，看不到本项目的负样本来源、金标禁入区、同币同时间块和排除规则，因此无法判断空标签是缺失还是设计结果。
- **有效路径**：先用 Datumaro 做格式、类别、框值和分布的只读筛查，再把空标签项与数据集 manifest / build receipt 联结；只有 manifest 期望有正框但标签为空时，才升级为缺标问题。合法背景样本归类为 `expected_by_contract`，不自动删除或补框。
- **通用规则**：任何第三方检测数据校验器报出 missing annotation 时，第一步先确认该数据集是否允许背景负样本，并用项目契约解释报告；通用 warning 只能生成复核队列，不能直接改动金标。
- **牵连**：`data.yaml`、正负样本 manifest、build receipt、原 Label Studio 金标几何；Datumaro 1.12.0 的 detection validator。该工具也不能替代时间切分、无前视、跨 split 重复和金标谱系检查。
