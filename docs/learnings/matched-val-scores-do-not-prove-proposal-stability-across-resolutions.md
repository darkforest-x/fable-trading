# 相近 val mAP 不能证明不同分辨率的 proposal 稳定

- **问题**：同一 32,000 张图、同一 seed 和训练配方下，`imgsz=960` 与 `imgsz=1280` 的 YOLO best checkpoint 在静态 val 上只差 0.881pp mAP50-95；是否可把更高的一臂当作同一形态检测器的改进版。
- **死胡同**：只比较 val mAP、固定阈值后的总框数，或把 1280 多出的 episode 直接称作“召回提升”。这些量都看不到两臂是否在同一个市场时刻、同一个核心区间和同一个方向上作出提案；分数尺度也不能跨模型当作同一概率。
- **有效路径**：固定同一份行情字节、窗口、几何过滤、NMS、episode 合并和高分辨率文档，只替换原生分辨率 checkpoint。再以同币种、核心区间重叠（允许一根 K 的边界偏移）做一对一 episode 联结，并同时检查固定阈值与各自 Top-N。该对照中 960 有 30 个、1280 有 41 个，但只联结 19 / 52 个（Jaccard 0.365），还有一例同核心右端的方向翻转；Top20 联结反而只有 7 个。
- **通用规则**：涉及 `imgsz`、渲染分辨率或预处理变体时，val mAP 只能说明各自在验证标签上的拟合。任何“更高分辨率更好”的下游结论前，先在冻结的同源窗口上报告 proposal identity、方向一致性、事件去重后的 Jaccard 和 Top-N 重合；若这些不稳定，禁止用触发数或 mAP 单独选模型。
- **牵连**：`scripts/scan_15m_ma_launch_owner_grade_a8000_hot3d.py`、`exp-15m-ma-launch-owner-grade-a8000-hot3d-20260829-v1`、`exp-15m-ma-launch-owner-grade-a8000-hot3d-1280-20260830-v1`、`analysis/p1_15m_ma_launch_owner_grade_a8000_hot3d_1280_20260830.md`、`imgsz=960/1280`、`conf=0.25`、NMS `0.70`；与 [源图宽度不决定 YOLO imgsz](source-pixel-width-does-not-select-yolo-imgsz.md) 互补。
