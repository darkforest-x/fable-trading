# V38 交付 QA

## 结论：Share with caveats

V38 的保存结果与 canonical HTML 结构可交付，并须明确说明：**只完成结构验证，浏览器视觉与交互 QA 未完成；本轮没有新的盈利结论。**

本次检查时间：2026-09-07 11:21:17 UTC。只读取 V38 已保存的报告、摘要、复核及构建收据、canonical artifact 和 HTML；没有重新读取源 OHLC、其他实验收益或运行真实回测。未修改报告、artifact、注册表、HANDOFF 或实盘配置，未提交。

## 交付物与来源

- Canonical HTML：`analysis/html/p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.html`；403,868 bytes；SHA256 `ff033fee4e42ea5266a9fbfab1f72705e9f16078c6dbccae444d4bcbfe008bc8`。
- Canonical artifact：本目录 `artifact.json`；SHA256 `46aedc1a6c00d87457be16e36d54a02f07ae265a0d6e65bf0501161d9e31d0c6`。
- 审计执行提交：`ab872f00685f85909d0311108dd4855a64262b11`；报告构建收据提交：`a766b0ec52389192f97ef780e5d3da2964cdc0e8`。二者对应不同阶段，不能混称同一次构建。
- `artifact_build_receipt.json` 中 artifact、报告 MD、report_data、notebook、summary 的五个哈希均与当前保存文件一致。
- HTML 解压后的 payload，其 `surface`、`manifest`、`snapshot`、`sources` 与 canonical artifact 完全相等；额外 package metadata 声明为只读 portable HTML。

## 验证层次与证据边界

| 检查 | 结果 | 证据与限制 |
|---|---|---|
| 状态机、runner、关联回归与层边界 | 494 pytest 通过 | 既有 `PREFLIGHT.md` 记录；不是全仓全绿，本次未重跑 |
| 报告生成专项测试 | 48 pytest 通过 | 主线提供的本次执行结果；本 QA 不冒称独立重跑 |
| 保存结果独立复核 | 733 一致性断言通过 | `REVIEW.md`；不是 733 个新增 pytest，不与 494/48 相加 |
| Canonical artifact 验证 | `ok=true` | 主线 `validate_artifact` 执行结果：1 dataset、6 sources、snapshot ready |
| Portable 流程 | validation passed；package passed | 主线执行结果；verification 为 `structural_only` |
| 本次独立结构与哈希检查 | 18 项通过 | 标准库 HTMLParser、gzip/base64、JSON 与 SHA256；不执行浏览器 JS |
| 浏览器视觉、手机、主题和来源交互 | 未验证 | Chromium headless shell 未安装；未安装浏览器、未生成截图，`viewports=[]` |

上述测试和断言属于不同层次，不能合并成一个“总测试数”，也不能代替策略收益验收。主线 portable 工具结果按主线提供的信息记录；本 QA 没有另存或伪造一份原始工具输出。

## 独立 HTML 结构检查

18 项检查包含五个构建哈希、内嵌 artifact 一致性、ready 状态、六个来源 ID 的唯一性与两处来源清单一致性、snapshot 计数与 summary 一致性、完整 HTML/主容器、DOM ID 无重复、内嵌 runtime 可解压、script/link/img 没有外部 src/href 依赖、viewport 声明、light/dark 声明、fallback 来源区/来源宿主，以及关键风险说明保留。

实际结构为 1 dataset、6 sources、1 chart；manifest 共 12 blocks（11 个 markdown blocks 含标题、1 个 chart block）。HTML fallback 有 11 个 section（10 个正文节与 Sources）、1 个 figure 和 1 个数据 table。两份 gzip-base64 template 均可解压，其中 runtime 为 894,207 bytes。存在这些结构不证明浏览器已成功运行增强 renderer。

关键提示在无需执行 JS 的正文中仍存在：尚未证明更赚钱、确认率不是收益或 alpha、不能把原 36 组缩成 9 组，以及不能把截止未确认请求记成现金收益 0。内嵌图表数据包含全部六行状态计数；本次没有验证实际坐标轴、图例、文字裁切、对比度或手机横向溢出。

## 数字与分析结论复核

此前的独立保存输出复核保留全部 171 个请求（63 case、108 control）、36 个原三对照组、27 个未匹配 case、946 行 trace 和 171 个前缀记录。原 matched 36 个 case 为 19 确认、8 等待触损、9 截止待定；全四成员确认只有 9 组，不据此缩小比较分母。

报告中的全体确认率 60.32% 对 60.19% 不是原 36 组的配对效应，也不是盈利优势。15 个 case 等待期触损不能称为“少亏 15 单”。trace 中 39 个 `unknown/not_observed` 是触损优先后未再检查颜色，不是 39 个缺失数据事件；最终 unknown 事件为 0。

前缀复现使用同一冻结 SMA 实现；未独立重写均线公式，也未验证真实盘口发布延迟与成交。旧版本经济数字、44 个不同 K1 等历史来源声明不在本次交付结构复核的独立验证范围内。

## 未完成项与必须保留的限制

- `sourceDialog=not_verified`、`sourceInteraction=not_verified`、`viewports=[]`；不声称来源弹窗、来源点击、桌面/手机截图、明暗主题切换或浏览器控制台通过。
- 浏览器不可用是视觉/交互完整验收的阻断，不是已发现的数字错误；当前交付等级只能为带限制分享，不能写成完整视觉 QA 通过。
- notebook 收据记录三个代码单元使用 `stdlib_sequential_exec`，`jupyter_kernel=false`；不能宣称 Jupyter 内核验证。
- 本轮是非经济入场时钟审计；不报告新胜率、PF、收益曲线或收益显著性，不宣称可部署或已找到最优参数。

若 canonical HTML 或 artifact 改动，需要重核哈希与结构；后续浏览器可用时，另补真实视口、主题和来源交互检查。不得为补 QA 覆盖原研究结果。
