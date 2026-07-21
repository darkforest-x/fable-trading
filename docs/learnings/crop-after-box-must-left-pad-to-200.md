# 切后文必须左补满 200，禁止短窗拉伸

- **问题**：Owner 要「GT 框右缘之后的 K 线全删、保留金标密集框」做训练分布；原金标框多在 200 窗中段，直接截断只剩几十根。
- **死胡同**：
  1. H-TIP `true_tip`「整窗滑成 tip 开火」——几何对了无后文，但框语义被重锚成启动 tip，Owner 否决。
  2. 截断后按剩余根数重渲（crop_after_box）——无后文且框仍是金标，但柱宽被拉胖，与实盘 200 窗不一致，训推会歪。
  3. 左补公式对了但 stem 当窗起点——框被搬到右缘，罩住的已是别的 bar（见 [stem-index-is-window-end-not-start.md](stem-index-is-window-end-not-start.md)）。
- **有效路径**：cut = 框右缘 bar；窗口固定 `[cut-199, cut]`（历史不足则 skip）；标签按原金标的 bar 跨度 + 价格上下界重映射（新窗价轴装不下则回退到同批 bar 的 high/low）。框靠右是右对齐的副作用，不是 tip 开火重锚。stem 按窗末解，并用存档 PNG MAD 消歧。
- **通用规则**：删后文时先问「根数是否仍等于推理窗」；不等于就左补历史或丢弃，绝不拉伸短序列铺满画布。回算窗前先钉死 stem→global 约定。
- **牵连**：`scripts/build_crop_pad200_dataset.py`；对照错误路径 `scripts/build_htip_dataset.py` / `true_tip_rerender`；预览 `analysis/output/pad200_try/fixed_compare_*`。
