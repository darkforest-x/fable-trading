# 延迟确认必须同步移动成交、风险和对照的时钟

- **问题**：原启动箭头在一根K，模型或高周期在后面另一根才确认；沿用原箭头的价格和月份会夸大确认优势。
- **死胡同**：只增加wait_bars，却仍按原箭头ATR、价格或月份匹配，无法衡量等待代价；按signal_i标记成交也会让多个anchor共享确认时刻时重复记账。
- **有效路径**：保留anchor和actual decision两个身份，用actual收盘后的下一open、actual ATR、actual收盘所属月配对；event_id独立而实际单仓只成交一次。
- **通用规则**：增加确认步骤时先联查特征可知时刻、可成交时刻、匹配对照时刻，再看收益；不能只改信号标签。
- **牵连**：yoyo/evaluation/mainstream_execution.py；tests/test_mainstream_execution.py；固定2ATR与0.2%成本，不修改线上规则。
