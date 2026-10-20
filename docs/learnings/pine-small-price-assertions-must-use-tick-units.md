# Assert small Pine prices in tick units

- **问题**：The first V2.4 native unit fixture reported correct-looking PEPE and ETH outputs as failures because it compared absolute errors against 1e-18 and 1e-10.
- **死胡同**：A smaller absolute tolerance looks stricter, but Pine comparison operators round float operands to nine fractional digits; a positive tolerance below that precision can compare as zero. Even an exact zero error can then fail a strict less-than assertion.
- **有效路径**：Compare `abs((actual - expected) / tick) < 0.000001`, with hand-calculated expected stops and R levels. The same production helpers then passed the native fixture, including deliberate reproduction of the old invalid PEPE buffer. Production calculations were not altered to satisfy the assertion.
- **通用规则**：For tiny-priced instruments, normalize tolerances into ticks before comparison; verify language comparison semantics rather than assuming Python-style float behavior.
- **牵连**：`tests/evaluation/monday_range_v2_4_native_checks.pine`; native PASS evidence in `output/pine/monday_range_v2_4_native_units.txt`. Official semantics: https://www.tradingview.com/pine-script-docs/language/type-system/ .
