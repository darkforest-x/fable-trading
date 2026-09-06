# 完整对照组容量不能用总匹配边数除以三

## 问题

原小时入场251母样本只有154组三控制。需要区分精确匹配供给不足和贪心
分配损失，但每个母样本必须完整获得3个全球不复用的控制。

## 死胡同

最大化匹配边数再除以3不等于最大化完整组数：边可以分散到多组的1/2个
控制里。相同分层键也不保证完整二部图，母风险转移可能使合法边不同。
这不是一个已经用真实行情证明的贪心缺陷；先要重建完整图。

## 有效路径

对母设二元y，对合法边设二元x，约束每母sum(x)=3*y、每候选时间sum(x)<=1，
最大化sum(y)。独立验证整数解、目标/对偶证书和连通分量上界，并用小图
穷举真值及贪心反例测试。正式求解前先保存通过旧版全字段parity的图，
超时只记录未解决，不能将部分可行解当成容量上限。

## 通用规则

业务价值按完整组计数时，优化目标也必须按完整组计数。求解最优性、
数据支持与交易盈利是三个不同命题；某组未被一个最优解选中不证明其永远不可选。

## 牵连

- yoyo/evaluation/hourly_impulse_matching_capacity.py
- tests/test_hourly_impulse_matching_capacity.py
- experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/PROJECT_PLAN.md
- SciPy1.13.1官方MILP状态/证书：https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
