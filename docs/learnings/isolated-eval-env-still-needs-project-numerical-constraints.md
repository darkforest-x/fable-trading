# 独立评估环境仍要继承项目数值版本约束

- **问题**：`requirements-eval.txt` 在空白 Python 3.11 venv 中默认解析到 NumPy 2.4.6、pandas 3.0.5、scikit-learn 1.9.0；虽不会污染主 venv，却会让标签质量结果与项目既有数值栈失去可比性。
- **死胡同**：只做到“独立 venv”解决的是依赖污染，不解决版本漂移；把两件事混成一个布尔判断，会得到安全但不可复现的评估环境。
- **有效路径**：先 dry-run 查看完整解析，再以 `requirements-eval.txt` 声明评估工具、以 `constraints-ci.txt` 约束两边共享的 NumPy / pandas / scipy / scikit-learn 版本；最终只新增 Cleanlab 等评估专用包。
- **通用规则**：评估环境需要同时满足两道门：物理隔离保护主训练契约，数值约束保护跨环境可比性。缺一都不算可复现。
- **牵连**：`requirements-eval.txt`、`constraints-ci.txt`、Cleanlab 2.9.0、Python 3.11；未改 Mac/3060/CI 的主依赖契约。
