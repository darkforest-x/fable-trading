# 超长 Unicode 模板要用真实 CLI 路径验源码编码

- **问题**：一个 Python builder 内嵌了数百行中文 HTML/JavaScript raw string；`python3 -m py_compile`、单元测试和 `runpy.run_path` 全通过，但当前 Xcode Python 3.9 用真实命令 `python script.py` 时在模板深处报 `Non-UTF-8 code`。
- **死胡同**：重复检查 `file -I`、十六进制字节和 `tokenize.detect_encoding` 只证明文件本身是合法 UTF-8；继续把 `py_compile` 当作入口验收也抓不到直接脚本加载路径的差异。
- **有效路径**：在 shebang 后显式加入 `# -*- coding: utf-8 -*-`，并用最终交付的完整 CLI 入口复测。编码声明消除了直接加载路径的歧义；产物必须随后从已提交的新 builder 重新生成，不能沿用绕过入口生成的旧回执。
- **通用规则**：脚本只要内嵌大段非 ASCII 模板，就同时做两种检查：静态 `py_compile` 和用户真正会运行的 `python script.py --help` / 完整命令；两者任一失败都不能交付。显式 UTF-8 cookie 是低成本保险，但不能替代真实入口测试。
- **牵连**：`scripts/build_15m_candidate_boundary_review.py`、artifact-builder 先提交后生成纪律、build receipt 中的 `builder_commit`。
