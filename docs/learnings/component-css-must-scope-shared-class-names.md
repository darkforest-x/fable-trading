# 同一模块的两个组件共用类名时，样式必须限定到各自的根节点

- **问题**：2026-11-06 Owner 截图：报警设置面板里“推送已连接 · 实时”那一行压在规则卡上面。
- **死胡同**：看起来像设置面板自身的布局或间距问题。如果去调面板的 margin/padding，并不能解决根因。
- **有效路径**：全屏报警的底部状态条和设置面板的状态行都叫 `.sa-status`。全屏报警那条规则 `.sa-status{position:absolute;left:50%;transform:…;white-space:nowrap}` 没加 `#spike-alarm-dialog` 前缀，又写在样式表后面，于是把设置面板的状态行也变成了绝对定位，结果脱离文档流、压到下面的内容上。把这组规则（包括手机端媒体查询里的那条）限定到 `#spike-alarm-dialog .sa-status` 即可，设置面板的样式不用改。
- **通用规则**：同一个 JS 模块生成多个组件（停靠按钮、设置面板、全屏弹窗、回看弹窗）时，组件内的通用类名（status、bar、row、chart）一律带上组件根 ID 作前缀，尤其是 `position`、`transform`、`display` 这类会改变布局的规则。出现覆盖问题时，先用 `getComputedStyle` 看是哪条规则生效，再改。
- **牵连**：`yoyo/monitor/static/spike-alarm.css`。
