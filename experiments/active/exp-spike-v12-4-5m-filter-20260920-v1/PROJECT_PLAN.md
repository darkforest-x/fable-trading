# SPIKE V12.4：5m加入已完成15m EMA120过滤

2026-09-20。Owner原话：“你先优化好5min得逻辑吧，加上过滤，保存到tv”。根据已完成六线研究，选择历史描述性表现较好的EMA120作为本次固定功能配置；这不是通过统计门的最优线，也不是SMMA的新回测。

- 唯一行为改动：5m原有V9最终准入追加已完成15m EMA120方向门，默认开；确认收盘严格线上才多，线下才空，相等/未知拒绝。关开关或其他周期跳过。
- 15m仍按V12.3使用已完成1h SMA60；原始反向信号退出、止损、追踪、成本、趋势线历史、既有框后配突破不改变。
- 15m原生OHLCV有效连续1200根，EMA由首个close播种、alpha2/121，缺口重置。security表达式内部偏移[1]并用lookahead_on；已知上级收盘须等于time(15)。5m图显示淡紫色方向线及当前门状态。
- 独立文件spike_burst_v12_4.pine及新TV私有脚本，保留V12.3。仅保存指标；不创建交易报警或订单，不部署VPS/ACTIVE。
- 验收：新旧源码还原对照、现有5m因果/EMA/缺口/反向退出及V12行为检查、独立只读复核、TV编译与默认开关/方向线/15m保留核对。原生观察完成前不宣称交付成功。
- 本轮不新增市场实验或搜索，不报告新收益。前次研究及其失败结果保留在analysis/p1_spike_v9_5m_15m_ma_20260920.md。

复现命令：
```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v12_4_pine_contract.py tests/evaluation/test_spike_v9_5m_15m_ma.py tests/evaluation/test_spike_h1_sma60_pine_contract.py tests/evaluation/test_spike_v9_htf_sma.py tests/evaluation/test_spike_v12_2_pine_contract.py tests/evaluation/test_spike_v12_pairing_reference.py tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v11_box.py
```

training_eligible=false；production_eligible=false。用户授权保存指标不等于模型promote或自动执行许可。
