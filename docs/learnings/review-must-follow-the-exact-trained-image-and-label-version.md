# 审核必须跟随实际训练图和标签版本

- **问题**：Owner要求审核最新8000张正图；既有Label Studio完整项目只有close均线版空图，最新模型实际用了HL2均线版。
- **死胡同**：只核对1043源事件相同，就给旧图加框或照搬另一版的标签。HL2虽然保持源事件、split和水平核心，却改变图像均线与部分纵向框；另有7–8个前后文变体，各自百分比坐标不同。
- **有效路径**：审核题逐字节复制实际模型版本的代表PNG，读取其实际YOLO文本生成可编辑预框；逐题保存全部变体ID、标签SHA和坐标变换。原未来参考也按同一HL2公式独立生成，先冻结主图/框再读取未来，最后核对主图和原标签没有变化。
- **通用规则**：事件身份、图像版本、标签版本、坐标系必须四者一起联结，才能说“正在审核实际训练标签”。一个事件只审一次，不能把一题的归一化框直接复制给所有裁图。
- **牵连**：`yoyo/datasets/grade_a_hl2_review_pack.py`；`yoyo/datasets/ma_launch_owner_grade_a_hl2.py`；`yoyo/datasets/grade_a_manual_pack.py`；本周整理计划`experiments/active/exp-yolo-dataset-consolidation-20260908-v1/PLAN.md`。复制与回放证明工程一致性，不代表Owner已确认形态，也不代表模型准确率提高。
