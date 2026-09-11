# A review gallery needs a renderable-record contract

- **问题**：逐笔图册很容易只生成可搜索目录，却在用户点开某些记录时缺少对应 OHLC，导致“133 笔可浏览”变成未验证的声明。
- **死胡同**：让浏览器根据序号和合约猜测图表文件名，或在文件缺失时借当前行情补图，都会掩盖来源缺口并破坏历史复盘边界。
- **有效路径**：清单中每条记录明确给出受控的相对 `charts/` 路径；构建器在复制站点前验证恰好 133 条、每个路径仍在数据根目录内、文件可解析且含非空蜡烛。页面只按该路径懒加载本地冻结 JSON。
- **通用规则**：把“可检索”和“可渲染”分开验收。批量回放页面必须在构建时证明每个目录项有同源图表载荷，不能依赖运行时 fallback。
- **牵连**：`yoyo/evaluation/static/spike_v1_review/build_site.py`、`tests/evaluation/test_spike_v1_review_site.py`；Lightweight Charts v4.2.0 作为本地 Apache-2.0 vendor，保留 NOTICE 与 LICENSE。
