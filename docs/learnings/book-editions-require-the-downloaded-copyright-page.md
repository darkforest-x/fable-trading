# 书目首版年份不能替代实际下载文件的版本

- **问题**：Notion #56 扩展威科夫书籍时，馆藏目录与前言提供 1922 年，而实际取得的《How I Trade and Invest in Stocks and Bonds》不是首版。
- **死胡同**：先把书目首版年份用于扫描件说明；随后 OCR 又把版权页读成 1926。仅比对书名、作者、可下载状态或提取文本，都不足以认定版本。
- **有效路径**：检查实际 PDF 第 12 页的渲染图，确认印刷字样为 Copyright 1925 / Fifth Edition。书目始刊年保留为 1922，阅读版本标为 1925 第五版；前端、Notion 和原件 manifest 一起修正，SHA 不变。Tom Hougaard 的同名早期文章也独立于 2022 年出版书登记。
- **通用规则**：下载后先对照封面、版权页、目录与末页；始刊年、取得版本、扫描页数、完整原文可用性和实际研读进度分开记录。OCR 与图像冲突时回到扫描图核对，不能把成功下载冒充逐章通读。
- **牵连**：`yoyo/monitor/static/trading-books.json`、`data/reading/task56/technical-sources/manifest.json`、Notion #56 及对应资料页。`wyckoff-invest-1922` 是作品标识，当前扫描件明确为 1925 第五版；历史本地文件名保留，版本由 manifest 说明。
