# 小窗检测框上全景图前必须先恢复绝对时间与价格

- **问题**：YOLO 的 `cx/cy/w/h` 属于各自 W18–25、1280×742 输入；全景图是 110 根、1880×780，
  横纵范围和价格缩放都不同。直接乘全景宽高会让同一个框跑到错误的 K 线和价格区间。
- **死胡同**：把小窗归一化坐标当成与整日画布共享的坐标系。08-27 的 43 个事件若这样画，框中心
  中位绝对偏差为 26.82 根 K（约 402 分钟）；只凭肉眼调整统一 delta 又会制造位置 shortcut。
- **有效路径**：用原输入 `ChartTransform` 逆变换四个像素边界，先恢复绝对小数 bar 索引和绝对
  price，再用全景 `ChartTransform` 正向投影；最后把结果反投回原输入，要求四边误差不超过 1px。
  同图保留模型实际输入 inset，给 owner 一眼核对两种视图确实是同一个框。
- **通用规则**：任何跨窗口、跨分辨率或跨价格范围的框展示，都必须走
  `source pixels → domain coordinates → target pixels`，不能直接复用 normalized xywh；验收必须有
  可量化的逆向闭环和一个故意错误的负对照。
- **牵连**：`yoyo/layers/l1_detection/render.py` 的 `ChartTransform`、
  `scripts/render_15m_ma_launch_owner_yolo_20260827_fullcontext.py`、
  `scripts/verify_15m_ma_launch_owner_yolo_20260827_fullcontext.py`。
