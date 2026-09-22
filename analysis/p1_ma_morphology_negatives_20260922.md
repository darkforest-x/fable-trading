# MA 形态负例纠正 v6：准备与先导阶段

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
