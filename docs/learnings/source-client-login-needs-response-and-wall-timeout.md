# 数据源“端口连通”不等于协议登录可用

- **问题**：2026-09-09 为独立 A 股日线回测探测 BaoStock 历史数据。旧版 0.8.9 的 TCP 10030 端口连接成功，登录却始终没有返回，探测 Python 进程占用约一个 CPU 核。
- **死胡同**：仅验证 DNS、TCP connect 和 socket 超时。旧客户端的 `recv()` 循环没有处理对端关闭后返回的空字节；这种 EOF 不触发超时，因而连接成功也可能陷入无限空转，不能据此判断数据源正常或等待更久。
- **有效路径**：查看官方当前 PyPI 包和客户端常量，发现 0.9.3 已从 `www.baostock.com` 改到 `public-api.baostock.com`。先 dry-run，再将确定版本安装进独立 `/tmp/spike-ashare-bs093`，不改模型环境。使用完整协议验证：登录成功、历史日期证券池返回、日线字段返回、除权日前后数据返回，才宣布可用。每次调用连同分页消费包在进程墙钟超时内，防止 EOF 空转。
- **通用规则**：数据源可用性必须由语义正确的协议响应确认；TCP 可连接只是最低层事实。旧客户端无响应时先核对官方版本、端点和 EOF 行为，再扩大重试或更换来源；网络超时之外还需要整个调用的墙钟截止。
- **牵连**：`yoyo/evaluation/ashare_data.py` 的 `baostock_session()`、`query_timeout()`、`result_frame()`；官方来源 `https://pypi.org/project/baostock/0.9.3/`。旧探测进程已终止，主 venv 的依赖未改动。
