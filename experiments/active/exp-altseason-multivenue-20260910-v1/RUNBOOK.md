# 冻结研究复现入口

工作目录 `/Users/zhangzc/fable-trading`。使用仓库现有 `.venv`，不升级依赖。源行情、事件、曲线、图片均保存在本实验目录；它们不进入git，校验身份见采集清单、逐市场coverage及最终报告manifest。首次数据评分前相关builder已分别提交。

## 本轮实际执行的公开行情采集

```bash
.venv/bin/python -m yoyo.data.altseason_sources --venue binance --output experiments/active/exp-altseason-multivenue-20260910-v1/data
.venv/bin/python -m yoyo.data.altseason_gate_catalog --output experiments/active/exp-altseason-multivenue-20260910-v1/data
.venv/bin/python -m yoyo.data.altseason_sources --venue gate --output experiments/active/exp-altseason-multivenue-20260910-v1/data
.venv/bin/python -m yoyo.data.altseason_sources --venue okx --output experiments/active/exp-altseason-multivenue-20260910-v1/data
```

Gate正式采集前需按上列命令用已提交bootstrap建立冻结目录。原正常目录探针没有limit参数，原collector使用limit=1000收到400；bootstrap保留该事实，以同一交易所的有效无参数接口取完整目录。不能直接删除目录再让原collector重复错误参数。

输入时间口径：2026-05-01 00:00 UTC至2026-09-09 00:00 UTC，用前段预热；评估仅2026-07-10起。默认参数已在source与PROJECT_PLAN冻结。重跑采集会面对交易所可修订的数据，正式复现优先核对既有原始响应及规范化CSV的SHA，不得将更新数据覆盖原版本后仍称逐字节复现。

## 从已冻结数据到完整研究

```bash
.venv/bin/python -m yoyo.evaluation.altseason_dataset --data experiments/active/exp-altseason-multivenue-20260910-v1/data --results experiments/active/exp-altseason-multivenue-20260910-v1/results --venue binance --workers 2
.venv/bin/python -m yoyo.evaluation.altseason_dataset --data experiments/active/exp-altseason-multivenue-20260910-v1/data --results experiments/active/exp-altseason-multivenue-20260910-v1/results --venue gate --workers 2
.venv/bin/python -m yoyo.evaluation.altseason_dataset --data experiments/active/exp-altseason-multivenue-20260910-v1/data --results experiments/active/exp-altseason-multivenue-20260910-v1/results --venue okx --workers 2
.venv/bin/python -m yoyo.evaluation.altseason_research
.venv/bin/python -m yoyo.evaluation.altseason_costs
.venv/bin/python -m yoyo.evaluation.altseason_report
```

dataset先检查builder与提交字节一致，再检查输入manifest SHA；已存在且来源、配置、产物SHA完全一致的市场直接复用。来源缺小时会拆连续段，重新预热，不补价格。aggregator要求所有已成功获取市场都有coverage，错误不可静默忽略。report仅核对账本、画图并立即转独立HTML，不重评分策略。

## 资金费与测试

资金费各币选择清单与原始CLI保存在 `data/funding/*_symbol_selection.json`、`funding_launch_receipt.json`。币种按冻结目录/非空OHLC选，不按结果挑选。Binance请求中途403后已停止；保留partial/blocked manifest，不能为复现而绕过拒绝访问或将未知费率补零。OKX/Gate读取实际历史费率，时间边界和代理价格限制见成本报告。

```bash
.venv/bin/python -m pytest -q tests/test_altseason_sources.py tests/test_altseason_gate_catalog.py tests/test_altseason_funding.py tests/test_altseason_engine.py tests/test_altseason_dataset.py tests/test_altseason_portfolio.py tests/test_altseason_research.py tests/test_altseason_costs.py
```

全部为离线研究。没有启用新的线上信号，没有通知测试，没有改仓位、API凭据、ACTIVE或订单。
