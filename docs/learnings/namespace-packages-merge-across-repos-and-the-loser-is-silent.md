# 隐式命名空间包会跨仓合并，输的那一方一声不吭

**日期**：2026-08-19（单仓收敛 C2）
**症状**：`tests/causality/test_gold_annotation_contract.py` 单独跑 13 passed，
全量跑 `ModuleNotFoundError: No module named 'tools.review'`。

## 现象

```
单独：  pytest tests/causality/test_gold_annotation_contract.py  → 13 passed
全量：  pytest tests                                              → 2 failed
```

两次跑的是同一个文件、同一个解释器、同一个 sys.path 头部（仓库根在 index 1）。

## 根因

`import tools.review` 的解析规则不是"找到第一个叫 tools 的目录就停"，而是：

```
按 sys.path 顺序扫描
  遇到 tools/ 但没有 __init__.py  → 记为 namespace portion，继续扫
  遇到 tools/__init__.py          → 立刻当作 regular package，停止，丢弃前面所有 portion
```

本仓 `tools/` 当时没有 `__init__.py`；`~/yoyo-trading/tools/` 有。
所以只要 sys.path 上出现过 yoyo-trading，`tools` 就解析成**另一个仓库的**包，
而那个包里没有 `review/`。

yoyo-trading 是怎么上 sys.path 的：`scripts/` 里 **35 个脚本**在 import 时执行

```python
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
```

这是 2026-08-03 `yoyo` 搬出本仓时期的跨仓桥。全量测试里只要有一个测试 import 了
这 35 个脚本中的任何一个，插入就对**整个 session 的后续所有测试**生效。
单文件跑时没人 import 它们，所以测试通过。

## 为什么这条值得记

三个性质叠在一起才致命：

1. **顺序依赖**：错误取决于哪个测试先跑，不取决于被测代码。
2. **静默**：仓库根的 `tools/` 被"看到过"，只是最终被丢弃；没有任何警告说
   "我用的是另一个仓库的同名包"。
3. **失败信息指错方向**：报的是 `No module named 'tools.review'`，
   听起来像本仓少了个文件，实际是本仓的整个 `tools` 被换掉了。

同族病：`yoyo` 自己也被 setuptools editable 安装的 meta-path finder 映射到仓外，
本地缺哪个子模块就静默落回 yoyo-trading（见
`tests/boundaries/test_yoyo_package_is_local.py` 的 docstring）。
**两处都是"跨仓解析"，都是缺失的一半静默借用别人的**。

## 处置

1. 给 `tools/` 加 `__init__.py`，变成 regular package——扫描在本仓就停，
   与 sys.path 上还有什么无关。namespace 包的"跨路径合并"特性在多仓环境里是负资产。
2. 加测试钉住：`tests/boundaries/test_yoyo_package_is_local.py::
   test_tools_resolves_to_this_repository_even_mid_session` 断言 `tools.__file__`
   非空（即不是 namespace 包）且落在本仓内。
3. 35 个跨仓桥在 C5 逐个删除——收敛之后 `yoyo` 就在本仓，桥没有理由存在。

## 可迁移的判断

- **"单独跑过、全量跑挂"永远先查全局状态污染**，不要先怀疑被测代码。
  sys.path、`sys.modules`、环境变量、工作目录，都是 session 级的。
- **任何被多个仓库共用的顶层目录名（`tools`、`utils`、`common`、`scripts`）
  都要有 `__init__.py`**。隐式命名空间包的设计目标是让一个包分布在多个路径上；
  在多仓开发机上，这个目标和"我要本仓的那个"直接冲突。
- 判断"包解析对不对"的可执行标准是 `mod.__file__` 落在仓库内，不是 import 成功。
