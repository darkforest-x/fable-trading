# PEP 604 联合注解在 pydantic/FastAPI 运行时求值处会炸 3.9，future import 救不了

- **问题**：`python3 -m pytest tests/` 在本机 Python 3.9 下收集期报
  `TypeError: Unable to evaluate type annotation 'int | None'`，两个 ops 测试文件
  一 import `src.webapp.server` 就挂，只能 `--ignore` 绕开。
- **死胡同**：第一反应是给缺 `from __future__ import annotations` 的模块补 future
  import。扫完发现全仓库含 PEP 604 的模块**都已经有** future import——它只把注解
  推迟成字符串，但 pydantic 在 BaseModel 类创建时、FastAPI 在路由注册时会
  `eval` 这些字符串，`'int | None'` 在 3.9 上照样 `TypeError`（pydantic 报错里
  建议装 `eval_type_backport`，但本仓库不引新依赖）。
- **有效路径**：只找**运行时被求值**的注解，普通函数签名不用动。全仓库 grep
  `BaseModel|get_type_hints|TypedDict`，命中只有 `src/webapp/server.py` 两处：
  pydantic 字段 `max_symbols: int | None` 和路由签名 `body: ScoutMtfRunBody | None`，
  改成 `typing.Optional[...]` 即可。TypedDict（forward_types.py / frozen.py）在有
  future import 且无人调 `get_type_hints` 时不会在类创建时求值，无需改。
- **通用规则**：3.9 下遇到 "Unable to evaluate type annotation"，先分两类：
  ①仅装饰用途的注解 → future import 够了；②被框架运行时 eval 的注解
  （pydantic 字段、FastAPI 路由参数/返回、显式 `get_type_hints`）→ 必须写
  `Optional`/`Union`。定位第②类用 `grep -rn 'BaseModel|get_type_hints'`，
  不要按文件挨个改。`dict[str, Any]` 这类 PEP 585 内建泛型下标 3.9 原生支持，不用动。
- **牵连**：`src/webapp/server.py`（仅此一个文件改动）；本机 Python 3.9.6 +
  pydantic v2；VPS 端 Python 版本更新所以线上从未暴露；约束：不新增依赖
  （否则 `eval_type_backport` 一行就能解）。
