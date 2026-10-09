# 大段 Pine 源码交付：页内 fetch + 合成 paste/copy 事件，回读一致后再保存

- **问题**：SPIKE V13.2 源码约 185 KB、3400 行，要写进 TradingView 网页 Pine 编辑器，另存为新的私有脚本。浏览器扩展里没有可靠的系统剪贴板，逐字输入又太长。
- **死胡同**：
  - 以前在桌面编辑器里大段粘贴没生效；逐字输入或 AX setValue 会弄乱缩进和字符，还可能触发图表快捷键（见 [large-editor-pastes-need-source-roundtrip-before-saving.md](large-editor-pastes-need-source-roundtrip-before-saving.md)、[native-code-editor-paste-needs-explicit-text-and-focus-proof.md](native-code-editor-paste-needs-explicit-text-and-focus-proof.md)）。
  - 把 20 万字符塞进工具调用参数，既贵又容易抄错一个字。
  - 网页上拿不到 `window.monaco`，不能直接 setValue。
- **有效路径**：
  1. 先提交并推送源码。仓库是公开的，在 TradingView 页面里 `fetch` raw.githubusercontent.com 上固定 commit 的文件，用 `crypto.subtle` 算 sha256，与本地一致才继续。
  2. 新建空白指标（或打开要更新的脚本），点进编辑区按 cmd+a，对 `.monaco-editor textarea` 派发 `new ClipboardEvent('paste', {clipboardData: dt})`，`dt` 里放 text/plain 源码。
  3. 再按 cmd+a，派发合成的 `copy` 事件，读出 Monaco 写回 `dt` 的文本，与源码逐字符比对。两次都完全一致（184,681 / 185,181 字符）才保存。新脚本点 Save；已上图的脚本从脚本菜单选「Save script」，会存成新版本。
- **"编译通过"不等于"能上图"**：第一版编译只剩 V13.1 原有的 `second` 重名警告，owner 加到图上却报运行时错误 RE10140（74 个 plot，上限 64）。V13.1 本身已贴着 64 的上限，新加的 2 个 plotshape 和 1 个 data-window plot 用了输入颜色，额外计数，合计多出 10 个。去掉 plot 以后第 2 版仍然报 67 个，正好是剩下的 3 个 alertcondition：**alertcondition 也计入 plot 上限**。最终改法：新提示全部用 label/line，提醒只用 `alert()`（不计入 plot），owner 建提醒时选「Any alert() function call」。测试里同时禁止 plot 类调用和 alertcondition。
- **通用规则**：
  - 长源码从页内取，不从对话里抄，用 hash 证明来源；写入编辑器后先回读、确认逐字一致再保存；旧版本另存，不覆盖。
  - 往接近上限的 Pine 脚本里加东西之前，先确认 plot 预算（plot 类调用和 alertcondition 都要算），优先用 line、label 和 alert()。
  - 保存、编译、上图、渲染四个状态分开记：后台标签页（`visibilityState=hidden`）不渲染画布，"Added to chart" 的日志也不能证明图例里真有这个实例（见 [pine-add-log-does-not-prove-chart-insertion.md](pine-add-log-does-not-prove-chart-insertion.md)）。布局出现未保存标记时不要点保存。
- **牵连**：`yoyo/evaluation/spike_v132_delivery.py`、`yoyo/evaluation/pine/spike_v132_distance_layer.pine`、`tests/evaluation/test_spike_v132_delivery.py`；TradingView 私有脚本「SPIKE V13.2 · 回踩再突破」第 1 版（74 个 plot）、第 2 版（67 个 plot）、第 3 版（与 V13.1 相同）；布局 AlGc61US 未保存。
