# MA 形态负例纠正 v6：数据通过，3060准备开训

Owner 2026-09-22授权纠正负例并重新执行A/B离线训练。旧v5把7824个收益非赢家变成空标签，已停止并保留失败结果。本轮正例沿用原1506训练/175验证/160测试事件，负例必须独立满足全可见窗非密集条件；尚未有新训练结果。

计划：`experiments/active/exp-ma-morphology-negatives-20260922-v3/plan.json` 与 `PLAN.md`。数据目标目录 `datasets/ma_launch_owner1500_morph_v6`，先导另存。旧收益非赢家隔离，不能作为形态验收背景或自动反标为正例。

## 复现入口

先提交builder和plan，再运行：

```bash
.venv/bin/python -m pytest -q tests/test_ma_morphology_background.py tests/test_ma_morphology_redo.py
.venv/bin/python -m yoyo.datasets.ma_morphology_redo build --out experiments/active/exp-ma-morphology-negatives-20260922-v3/pilot20 --pilot 20
.venv/bin/python -m yoyo.datasets.ma_morphology_redo audit --out experiments/active/exp-ma-morphology-negatives-20260922-v3/pilot20
```

## 对照与风险

| 项目 | v4 | 被否决v5 | 新v6计划 |
|---|---|---|---|
| 训练正事件 | 1506 | 1506 | 1506原图原框 |
| 负例依据 | 缺失 | SL/TIMEOUT | 全可见窗形态证明 |
| 验证/测试负例 | 收益非赢家 | 同v4 | 同源同时间块重选背景 |
| 训练 | A/B各40轮 | 中止A8/B0 | 数据通过后重训 |

当前为非方向性标签语义与构建审计，收益/AUC/置换p/匹配随机入场收益不适用，也没有编造新模型指标。等效否证检查：改变core+5之后的OHLC不能改变筛选；可见边缘密集须被拒；同一形态候选改为TP/SL/TIMEOUT/UNKNOWN都仍受保护。未来检测验收与交易评分分别记录。

## 风险与诚实声明

正例是3R精选的规则形态，负例是保守数值证明的非密集背景，均非新增Owner逐样本金标；病例/背景评估不能证明所有形态召回或盈利泛化。难负例尚待审核，不强行凑比例。新训练尚未开始，原模型不部署、不promote。


## 2026-09-22 全量数据验收

全量使用896个已有OHLC源文件，未另换数据源。1841原正事件和1841新背景配对成功，shortage=0。A train1506正+1506负；B train3012正+3012负（各事件两视图）；共享val175正+175负、test160正+160负。物理PNG共9706个，PNG/TXT共19412个逐文件SHA、尺寸、标签及保护区审计通过。保留所有原正例字节，整体Owner逐样本确认仍为false。

- manifest SHA：`ebe8b1bc17923f14fddf16d32af391cf80517a7fba8a4849783d0316bc65caf5`
- ledger SHA：`3650046c10984394bb7e9f42f438260a7ce2955d48dee21452d3e3098b9c0ca2`
- 27组最接近阈值的分层边界样本（每个split×周期×配对方向一组）原OHLC重算通过，所有背景视图重渲染字节一致；配对正例被背景筛选接受数为0。7页对照图已实际查看，红框仅用于对照页，未写入训练图。
- 真实样本时间：训练首次可见2020-02-07、末标签2025-12-29；验证2026-01-01至2026-04-30；测试2026-05-01至2026-08-31。全正例1841个cluster、跨split复用0。背景完整最宽窗+12h安全区间不跨切点。
- 新增27项针对性测试通过。仓库边界/因果/一致性检查468通过、8失败，与旧v5日志失败集合完全一致：历史归档路径1项、其他artifact元数据缺source_commit5项、L1历史迁移SHA2项。未弱化测试，未声称全仓通过。
- RTX3060已实测在线：torch2.8.0+cu126 / ultralytics8.4.89 / numpy2.0.2 / pandas2.3.3。旧全收益候选池val1305/test989共2294张GPU侧逐图SHA一致；只用于推理，不读取其形态空标签。

审计与对照图：`experiments/active/exp-ma-morphology-negatives-20260922-v3/visual_review/`。便于浏览的真实训练图链接在`datasets/ma_launch_owner1500_morph_v6/review_links/A/`与`B/`，按train/val/test及positives/backgrounds分目录；这些链接不是额外样本。

原正例形态存在差异，部分框含早期方向运动，原规则标签不是新的人工金标。保守背景使形态评估偏容易，不拿此数据的高mAP单独证明边界识别能力；收益评估仍保留失败与随机对照。

当前状态：完成冻结，正在传输到3060，尚无本轮训练结果。后续训练、两套评估和结果回传使用普通Python进程衔接；不通过反复唤醒模型报告未变化的状态。应用目标卡仍paused，工具不支持resume，已提示Owner界面恢复；该限制不阻止本轮执行。
