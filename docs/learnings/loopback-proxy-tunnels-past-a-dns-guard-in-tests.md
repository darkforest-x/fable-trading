# 测试里拦 DNS 拦不住本机代理，真实密钥会顺着代理出网

- **问题**：旧跟单项目的测试在 import 时执行 `load_dotenv()`，从工作目录读入生产 `.env`（OKX/Gate 实盘 key）。下单路由测试 mock 了交易所客户端，但"交易所是否已配置"这道门要靠真实 key 才能通过；模拟模式的开仓测试还会真的请求交易所账户和行情。也就是说，每跑一次测试，就用实盘 key 访问一次真实账户。
- **死胡同**：① 迁移时只把运行目录换成空的临时目录：9 个路由测试失败，因为 key 没了。② 补上假 key 后测试变绿，但耗时从 2 秒涨到 12 秒，说明有请求出网了。③ monkeypatch `socket.getaddrinfo`，只放行 loopback：没效果。httpx 经 `urllib.request.getproxies()` 读到了 macOS 系统代理（Shadowrocket，`127.0.0.1:1082`），只连本机代理，由代理去做 DNS 解析，所以绕过了拦截。
- **有效路径**：cProfile 显示 `httpcore connection_pool.handle_request` 被调用 3 次，还收到了 HTTP/2 帧，确认确实出了网。`scutil --proxy` 和 `env` 找到系统代理与 `HTTP(S)_PROXY`。在 conftest 里同时做到：运行目录指向临时目录、删除交易所/Telegram 相关环境变量、注入明显是假的凭据、拦截非 loopback 的 DNS 解析、删除所有 `*_PROXY` 环境变量。之后 150 个测试 1.9 秒跑完。
- **通用规则**：判断测试是否离线，看耗时和调用栈，不看是否通过。只要机器上有本机代理（系统代理或 `*_PROXY`），只拦 DNS 或外网 IP 都不够，还必须去掉代理。任何会碰钱的服务，测试进程都不能有机会读到生产 `.env`：配置模块在 import 时读文件的话，要在 conftest 里先把路径改掉，再 import。
- **牵连**：`tests/copier/conftest.py`、`yoyo/copier/config.py`（`COPIER_HOME`、`ENV_FILE`）。httpx 默认 `trust_env=True`；工作台代理与 vision 代理都显式设了 `trust_env=False`。
