# 审计适配器的字段错误不能算成原标注错误

- **问题**：把未确认的YOLO几何建议交给Datumaro检查时，合法框也出现UndefinedAttribute。原因是适配器给框增加了role，却没有在类别元数据中声明这个属性。
- **死胡同**：直接把通用工具的errors数量当成源标签错误，或删除role标记使报告变绿。前者误报，后者丢掉“建议框并非人工Gold”的身份。
- **有效路径**：在LabelCategories明确声明role，把逐框ID保留为item属性；先用实际SDK运行已知重复图、不同图和刻意坏框的合成对照。合法6框0 error，负宽坏框被识别，所有身份完整保留后，才检查真实批次。
- **通用规则**：数据审计先验证适配器能保留原语义、识别已知错误，再解释真实报告。工具告警分成适配错误、数据契约允许项和待人工复核项，不能自动当作删样本依据。
- **牵连**：`yoyo/datasets/owner_box_quality_tools.py`、`tests/test_owner_box_quality_tools.py`；Datumaro 1.12.0、CleanVision 0.3.7；参照[空背景与缺标不可混称](generic-detection-validator-cannot-distinguish-background-negatives.md)。
