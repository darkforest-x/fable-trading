# v13 pad200 train 抽查 20 张

> **审计更新（2026-07-22）**：Owner 反馈「框不对」后查出 **数据 bug**——bulk 关 MAD、盲用 `end_incl`，把 `okx_*`（实为 start 窗）切错。  
> 纠正对照请看：`../v13_train_sample20_corrected/index.html`  
> 报告：`analysis/p_pad200_cut_audit.md`。本目录仍是 **当时数据集原样**（含错窗），勿当「已修好」。

## 怎么打开
- 浏览器打开：`analysis/output/v13_train_sample20/index.html`
- 或直接看：`annotated/`（GT 叠框）与 `raw/`（原图）

## 选样意图
| bucket | 张数意图 | 看什么 |
|---|---|---|
| typical_tip | 多数 | 框是否贴右、MA/K 线是否正常、黑底 |
| mid_leak | ~2 | pad200 本应极少中段右缘——若出现是否构建漏网 |
| narrow / wide / flat / tall | 各少量 | 极端框是否仍合理 |
| multi | 1 | 多框是否互相打架 |
| background | ~3 | 空标背景是否真的无框、渲染是否空白 |

详见 `manifest.json` 与 `analysis/p_v13_why_bad_train.md`。
