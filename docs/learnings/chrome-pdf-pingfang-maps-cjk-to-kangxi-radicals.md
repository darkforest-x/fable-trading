# Chrome 打印 PDF 用苹方时，部分汉字的文本层会变成康熙部首码位

- **问题**：用 headless Chrome `--print-to-pdf` 生成中文小册（字体 PingFang SC），页面看起来正常，但 PDF 文本层里"小、一、方、入、人、大、长"等字被映射成康熙部首 / CJK 部首补充码位（⼩ U+2F29、⼀ U+2F00、⽅ U+2F45…）。后果：在 PDF 里搜"小"搜不到、复制出来是怪字，基于 pypdf 文本定位章节页码的脚本 63 项里只命中 36 项。
- **死胡同**：
  - 换成 Hiragino Sans GB、Heiti SC、Songti SC 也不行，只是被替换的字不同（推测是同一字形被多个码位共享，Chrome 写 ToUnicode 时挑了部首码位）；Noto / Source Han 本机未安装，回退到苹方，同样失败。
  - 只在匹配侧做 NFKC 能让脚本找到页码，但交付给人的 PDF 仍然不可搜索——治标。
- **有效路径**：写一行测试 HTML，同一句话分别用 7 种字体渲染，打印成 PDF 后用 pypdf 抽回来逐行比对。只有 **STHeiti（华文黑体）** 完整往返正确。于是 `@media print` 下正文与内联 SVG 统一改用 STHeiti，屏幕版继续用苹方；匹配文本时再加一道 `unicodedata.normalize('NFKC', …)` 兜底。改后 63/63 定位成功、两次排版页码一致。
- **通用规则**：凡是要交付"可搜索/可复制"的中文 PDF，先做一行多字体往返测试（渲染 → 抽取 → 与原文比对），再定字体；不要以截图看起来正常为准。任何对 PDF 抽取文本的匹配都先做 NFKC。
- **牵连**：macOS + Chrome 154 实测；字体回退顺序会影响结果，`font-family` 首位必须是确实安装的 STHeiti。相关产物：`~/Documents/Codex/2026-10-10/bang/work/handbook/build.py`（订单流学习小册的两遍排版脚本）。
