## 问题

同一笔固定 TP/SL 交易在标签、回放和 forward 路径中各自实现障碍扫描，导致做空收益公式、同 bar 双触发、跳空和 horizon 末端行为只能靠约定俗成保持一致。

## 失败尝试

保留多份扫描循环，再用少量“典型样本”测试比对。典型样本没有覆盖精确触价、同 bar 双触发、跳空和未走满 horizon，重复实现仍会在边界条件分叉。

## 有效做法

建立一个纯函数障碍裁决器，把 side、entry、TP/SL、horizon、同 bar 策略、跳空策略、收益口径和 partial 策略全部作为显式输入；标签与 forward 只做参数适配。用同一组边界样本同时验证 canonical、label 和 forward 输出。

## 可推广原则

只要一个结果会被训练标签、离线回放和线上观察共同消费，就应有一个唯一、显式且可测试的语义源。相同公式的复制不是 parity，只有共享同一裁决器才是。

## 本次涉及

- `src/judgment/outcomes.py`
- `src/judgment/labeling.py`
- `src/judgment/forward_scan.py`
- `tests/test_canonical_outcomes.py`

