# 检测复核图必须保留模型原始四维框

- **问题**：同一批 YOLO 推理在复核图上出现多个框，而且框的位置、尤其纵向范围与模型实际输出不一致；用户因而无法判断是模型错检还是展示层画错。
- **死胡同**：只保留预测框的横向中心和宽度，再用核心 K 线的 high/low 重建纵向边界；同时把多个滑窗命中去重后全部叠到 96 根日图上。这样既丢失 `cy/h`，又把“窗口级预测”伪装成“日图上的多个独立判断”，即使推理像素正确，复核证据仍然不可信。
- **有效路径**：在推理出口逐框保存 `cx/cy/w/h`、模型输入尺寸、窗口根数和输入像素哈希；复核面只展示实际送进模型的 1280×742 窗口，并直接投影这四个坐标。另以重叠决策区间聚成 episode，每个币日只取最早 episode 代表；离线重新渲染输入并逐像素重画 overlay，要求 100% 一致。
- **通用规则**：展示检测结果时第一步先确认原始四维预测坐标是否完整落盘。原始框、语义核心区和事件聚合是三种不同事实，必须分字段、分图层保存；任何用 K 线或均线重新推导的框只能标成辅助解释，不能冒充模型框。
- **牵连**：`scripts/scan_15m_ma_launch_t3_daily_movers.py`、`scripts/scan_15m_ma_launch_owner_yolo_recent5d_rawbox.py`、`scripts/verify_15m_ma_launch_owner_yolo_recent5d_rawbox.py`；与 [候选语义不定义框几何](candidate-selection-semantics-do-not-define-box-geometry.md) 和 [源 PNG 与运行时 YOLO 张量是不同产物](source-png-and-runtime-yolo-tensor-are-different-artifacts.md) 配套使用。
