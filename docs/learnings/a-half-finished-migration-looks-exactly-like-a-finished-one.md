# 迁一半和迁完了，长得一模一样

**日期**：2026-08-19（五仓收敛 C1）
**结论**：把包分成两个仓之后，"本地缺的模块静默落回另一个仓"是默认行为。
**字节校验、因果测试、边界测试会全绿，同时 import 着一个即将归档的仓库。**

## 现场

2026-08-03 `yoyo` 包从 fable-trading 搬到 yoyo-trading，用 setuptools editable 安装接回来。
一年后要把它迁回来。开工前先量了一下依赖规模：

```
fable-trading 里 63 个文件 import yoyo.*
yoyo 解析到 /Users/zhangzc/yoyo-trading
```

即：**ACTIVE 仓跑不起来，除非另一个仓在磁盘上。** 这本身已经够糟。
但真正危险的是它**怎么失败**。

## 机制

setuptools 的 editable 安装装的是一个 **meta path finder**：

```python
MAPPING = {'yoyo': '/Users/zhangzc/yoyo-trading/yoyo'}
def install():
    sys.meta_path.append(_EditableFinder)     # 注意是 append
```

`sys.meta_path` 的顺序因此是：

```
[BuiltinImporter, FrozenImporter, PathFinder, _EditableFinder]
```

`PathFinder`（走 `sys.path`，能看见本地 `yoyo/`）排在 `_EditableFinder` **前面**。
所以：

| 情况 | 解析结果 |
|---|---|
| 本地 `yoyo/` **有**这个子模块 | 用本地的 ✅ |
| 本地 `yoyo/` **缺**这个子模块 | PathFinder 找不到 → `_EditableFinder` 接手 → **落回 yoyo-trading，一声不吭** |

第二行是要命的那行。`_EditableFinder.find_spec` 对 `yoyo.data` 这种子模块的处理是
「父包在 MAPPING 里就去仓外找」，与父包实际解析到哪无关。

## 后果：验收指标全部失去意义

假设只迁了 `yoyo/contracts/`：

- `import yoyo` → 本地 ✅
- `import yoyo.contracts.costs` → 本地 ✅
- `import yoyo.data.loader` → **仓外** ❌，但没有任何提示

于是：
- **字节校验**：迁进来的那些文件确实字节一致 ✅
- **因果测试**：跑的是仓外的代码，但它也是对的 ✅
- **层边界测试**：AST 扫的是本地文件，本地没有的层就不扫 ✅
- **全测试套件**：绿 ✅

**一个迁了 10% 的仓库和一个迁了 100% 的仓库，所有指标完全相同。**

## 处置

1. **整包一次迁完**，不分阶段。55 个 `.py` 一次性迁回，55/55 字节一致。
2. **加 provenance 守门测试**，两条互补：
   - `import yoyo` 之后断言 `Path(yoyo.__file__).is_relative_to(REPO)`
   - AST 扫全仓，**每一个被 import 的 `yoyo.X.Y` 都必须在 `./yoyo/` 下有对应文件**
     （不 import，所以需要 torch/lightgbm 的模块也覆盖得到）
3. 负例验证：把 `yoyo/contracts/costs.py` 挪走再跑，测试立刻指名道姓报出
   哪个模块会落回仓外、被哪 4 个文件 import。

## 可迁移的判断

- **"import 成功"不是包解析正确的证据。可执行的标准是 `mod.__file__` 落在仓内。**
  多仓开发机上，这两件事分离得比想象中容易。
- **任何"渐进式迁移"计划，先问一句：迁了一半的时候，指标看起来是什么样？**
  如果和迁完了一样，那这个计划没有进度信号，只有一个你以为在往前走的错觉。
  这时要么一次迁完，要么先造出能区分两种状态的信号。
- **fallback 是可用性特性，也是可观测性缺陷。** editable 安装的落回设计得很贴心；
  代价是它把"缺失"变成了"静默借用"。凡是有 fallback 的解析机制
  （meta path finder、namespace package、`PATH`、DNS search domain、
  config 层叠），都要单独问一句：**用的是哪一份，怎么证明？**
- 同族病见 `docs/learnings/namespace-packages-merge-across-repos-and-the-loser-is-silent.md`
  ——同一天在同一个仓里，`tools` 也是靠跨路径合并被另一个仓抢走的，
  只是那次症状是「单独跑绿、全量跑红」，比静默借用还算幸运。
