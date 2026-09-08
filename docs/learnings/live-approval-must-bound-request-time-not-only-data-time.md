# 实时授权必须同时约束数据时点和请求时点

- **问题**：授权范围检查只比较观察cutoff；采集过程中授权过期仍可能继续发请求。
- **死胡同**：仅限制cutoff距离当前不超过5分钟，仍会留下最多5分钟过期读取窗口。
- **有效路径**：同时校验cutoff和wall clock在有效期内；节流等待后、真实HTTP之前再校验；最终派生快照写入前再验证。
- **通用规则**：有排队或重试的授权门，必须贴近副作用发生时重新检查。
- **牵连**：yoyo/contracts/rotation.py、rotation/providers.py、pipeline.py、衍生品和OKX provider。
