# SPIKE V12.2：保留历史区域，只整理局部重复线

Owner 明确纠正 V12.1：全图只留3条不是需求，同一区域局部整理才合理，历史线应淡化保留。V12.2 已独立建文件和 TV 私有脚本；旧版均保留。本次不出 HTML。

## 显示变化与对照

| 项目 | V12.1 | V12.2 |
|---|---|---|
| 默认覆盖 | 全图最多3条 | 不同历史区域保留，资源上限40组 |
| 局部重叠 | 全局删减 | 同A锚点附近默认3组，可调1–8或关闭整理 |
| 历史颜色 | 仅少数保留 | 普通透明度74，联合40；当前候选保持较强颜色 |
| 延长 | 默认48根 | 历史默认在事件处结束，可调 |
| ABC标记 | 受全局预算限制 | 每区域最新一组，仍受近期8组标记预算 |

局部区域不是随屏幕缩放的像素区块：使用首次代表线的A时间区间、邻近2根本图K及首次事件1个本图ATR价格带，冻结后不随成员扩大。HTF的A区间使用自身周期。局部淘汰优先普通旧线，再联合旧线；先整理局部，再执行全局资源上限，避免新区域成员挤掉无关历史。绘图对象和标签同步回收。

原有同B突破聚合保持不变，继续保留首条代表几何及完整成员提示；该聚合可能包含不同A/C，不宣称几何完全去重。检测、配对、风险、成本和事件逻辑源段与V12保持一致，仅版本标题文字变化。

## 验证与复现

源提交 `4ab18ee2e0a9cd72836b730e4fdad447e18a84ec` 先于原生运行；源SHA见 source_receipt.json。完整复现命令：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v12_pairing_reference.py tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v12_pine_contract.py tests/evaluation/test_spike_v12_1_pine_contract.py tests/evaluation/test_spike_v12_2_pine_contract.py tests/evaluation/test_spike_v10_4.py tests/evaluation/test_spike_v11_box.py
shasum -a 256 yoyo/evaluation/pine/spike_burst_v12.pine yoyo/evaluation/pine/spike_burst_v12_1.pine yoyo/evaluation/pine/spike_burst_v12_2.pine
```

55项通过（1.37s），Luna Max只读复核无阻断问题。TV新建私有“SPIKE V12.2 · 历史淡线”，revision1，2026-09-20 04:39:48北京时间编译0错误，已有second变量遮蔽警告1项。

原生OKX BTC15m宽图约09-02至09-20：开启→关闭→恢复开启局部整理。关闭时密集扇形线增加，恢复后局部密度下降，不同历史区域仍显示且总线数大于3。04:44:30保存布局。只保留V12.2图表实例，旧私有脚本未删除。

非收益实验：没有新候选/正类/val样本池，AUC、置换p、毛净收益及随机入场收益不适用。等价对照为源逻辑一致性和同一行情视图开关往返，不宣称视觉主观满意度已获owner确认。

## 风险与诚实声明

- 历史绘图仍受40组资源上限约束，并非无限历史；局部锚点归组是明确规则，未保证与人工划区完全一致。
- 本次原生只验证BTC15m宽图，无完整事件导出、跨币覆盖或新收益回测。
- 截图在会话中原生显示，未保存到磁盘，不以JSON观察记录冒充像素证据。
- V12.1功能检查通过但产品假设被owner否定；旧报告保留，修正记入新学习笔记。
- 不调整交易参数、训练资格、生产资格或实盘执行。

下一步仅根据owner实际图面反馈决定是否再调局部分组；如需变更继续新小版本与新TV脚本。

Notion：[V12.2记录](https://app.notion.com/p/3e08856479af8137a727db68846a5eae)。
