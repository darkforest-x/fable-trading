# A missing list entry does not prove generation is unfinished

- **问题**：Stitch 编辑请求超时后，列表与项目读取没有新画板，我反复断言“新稿没出来”；Owner 网页截图显示请求已完成。
- **死胡同**：重复读取相同索引，把“接口未列出”升级成“产物不存在”。此前新开的空白网页也不能代表 Owner 已加载的项目画布。
- **有效路径**：读取 Owner 实际网页中对应标题的 DOM，取得画板 ID `f5d3f95247be479983f598ecb45c032b`；直接 `get_screen` 成功返回该画板，随后下载到设计和 HTML。没有重新生成。
- **通用规则**：异步生成超时只说明本次等待超时。列表缺项只报告为“列表中未发现”，交叉核实实际项目画布和资源直读；不能据此要求豁免、停止已授权工作或给出确定的失败结论。接口索引差异的具体原因仍未查明。
- **牵连**：Stitch 项目 `8978791379095391159`；`yoyo/discipline_app/design/glass-0.9.1/`；不把真实交易数据上传为设计素材。

## 2026-10-25 后续原因核查

- 原始 06:17:42 UTC 结果为178张，原始文本与结构化结果均缺少本次画板，也缺少05:50前已成功下载的巡检画板 `57a4f7f2e33e46b3b0ecf390afe4f2b5`；不是模型标题过滤把新稿删掉。项目成员列表同样不完整，源画板直读只证明旧源仍在。
- Owner 打开网页后，项目更新时间变为06:19:30 UTC；本次复核列表为185张，新增7张包括之前已完成的巡检画板和App新稿。按新ID直读、列表与项目成员现在一致。精确生成完成时间没有服务端日志，不能拿项目更新时间当完成时间。
- [Google开发者论坛的确认](https://discuss.ai.google.dev/t/list-screens-returns-empty-after-generate-screen-from-text-until-project-is-opened-in-browser/123348)解释：生成后端与MCP索引同步不完整，已登录网页可触发最终提交。[官方SDK问题#149](https://github.com/google-labs-code/stitch-sdk/issues/149)的2026-10-22复现也同时出现 `get_screen` 成功、两个列表缺项、打开网页后补齐。本次证据与此机制一致；未声称检查过Google内部实现。
- 错误处理比接口缺陷多了一层：我没有验证已知完成项是否在列表内、把空白内框当作网页核验、只反复查询旧源ID，并且未及时查已知问题。AGENTS.md 已增加具体恢复步骤。原始对照摘要见 `yoyo/discipline_app/design/glass-0.9.1/stitch-index-diagnostic.json`。
