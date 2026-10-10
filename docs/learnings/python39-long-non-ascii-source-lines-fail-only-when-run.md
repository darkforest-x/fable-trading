# Python 3.9 里含中文的超长源码行，编译能过、直接运行却报「Non-UTF-8 code」

- **问题**：15m 回测网页脚本里有一行约 1100 字符、含中文的 HTML 结论模板。`py_compile` 通过，`python 脚本.py` 却报 `SyntaxError: Non-UTF-8 code starting with '\xe7' ... on line 645`；用 `bytes.decode('utf-8')` 检查整个文件，完全合法。
- **死胡同**：以为是写文件时编码不对，或者 f-string 嵌套引号写错了，于是去查 `write_text` 的默认编码、反复检查文件字节。文件本身没有问题，这些检查都只是确认「它是合法的」。
- **有效路径**：`py_compile` 走 importlib 的 `decode_source`，一次解码整个文件；直接运行脚本则由 C 分词器按缓冲块读文件。3.9 的分词器在超长行里遇到被缓冲边界切开的多字节字符时会误报。用 `awk 'length($0) > 400'` 找到超长行，按中文句号把它拆成多行（HTML 文本里的换行不影响显示），问题就消失了。
- **通用规则**：在 3.9 下，含中文的源码行控制在几百字符以内；长段 HTML 或文案拆成多行，或者放进单独的模板文件。遇到「Non-UTF-8」但文件解码正常时，先查有没有超长行，不要去改编码。
- **牵连**：`experiments/active/exp-spike-v129-eth15m-20261010-v1/build_report.py`（提交 `15m report: split the long conclusion line`）、`.venv` 的 Python 3.9。
