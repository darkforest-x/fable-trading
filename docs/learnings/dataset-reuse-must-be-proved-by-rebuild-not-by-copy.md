# "复用旧数据集"要靠逐字节重建来证明，不能靠复制文件

- **问题**：Dataset V3 要在新仓（yoyo-trading）里复用 fable-trading 的 2,688 个旧样本，
  同时只改负例。最省事的做法是把旧 PNG 复制过来——数量对得上，SHA 也当然对得上。

- **死胡同**：复制 = 用输出证明输出。它证明不了新仓的**构建链**（前缀加载、均线计算、
  窗口切片、渲染器、框算术）与旧仓一致；下一轮只要有一处静默漂移，你会以为在做单变量实验，
  实际上换了像素。旧项目已经吃过一次这个亏：w20_midbox 重建时 2,635/2,635 图片逐字节一致，
  **但 405 个样本的 split 落点全错**——一致的轴不代表全部的轴。

- **有效路径**：把每个复用样本**重新渲染一遍**，再与冻结 manifest 里的 `image_sha256` /
  `label_sha256` 比对，任何一位不同就让构建失败。先在 45 个样本上做 smoke parity，
  再在整库 2,688 个上做（结果 0 漂移）。这样"复用"变成一条可验证的断言，而且顺手证明了
  新仓的渲染链与旧仓等价——后续 R3A/R3B 的单变量声明才站得住。

- **通用规则**：跨仓/跨轮复用数据时，交付物是**重建**而不是拷贝；把 SHA 比对写进构建器
  本身（失败即中止），不要放在事后的检查脚本里。额外收益：这条流水线同时是渲染器回归测试。

- **牵连**：`yoyo/datasets/window_render.py`、`tools/smoke_parity.py`、
  `tools/build_dataset_v3.py`；
  `analysis/p3_yoyo_dataset_v3_gold_core_prereview_20260812.md`。
  相关：[reproducibility-is-per-axis-not-a-boolean.md](reproducibility-is-per-axis-not-a-boolean.md)、
  [artifacts-built-before-their-builder-landed.md](artifacts-built-before-their-builder-landed.md)。
