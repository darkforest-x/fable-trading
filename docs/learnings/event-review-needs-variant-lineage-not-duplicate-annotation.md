# 按事件省去重复标注，必须保留逐图坐标契约

- **问题**：Owner希望重标当前YOLO数据，而当前32,000张训练图来自4,172个事件，每个事件有7～8个位置变体；240张只是另行抽取的校准样本。
- **死胡同**：把小批审核包称为全部数据会误导工作量；把每个变体都交给人重新画会重复劳动。直接复制一个归一化框也不成立，因为不同窗口重新缩放了纵轴。manifest继承的旧路径/旧偏移同样不一定描述当前像素。
- **有效路径**：以当前event_id分组，选可追溯的原始代表PNG供人画框，保留所有当前窗口时间、图片SHA和ChartTransform；更早变体单独作参考。40根未来图物理隔离，仅挂只读参考Image，RectangleLabels只绑定原图。后续回写必须经bar/price变换并检查各变体包含关系，不能自动抄框。
- **通用规则**：先说清图片数、事件组数和抽样数；去重的是人的重复操作，不能丢掉训练变体的坐标和split身份。
- **牵连**：`yoyo/datasets/grade_a_manual_pack.py`、`configs/labelstudio/grade_a_manual_future40.xml`、`experiments/active/exp-15m-grade-a-labelstudio-manual-20260907-v1/PLAN.md`。
