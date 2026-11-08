# 依赖锁死的服务要发 APNs：用系统 openssl 签 JWT、系统 curl 走 HTTP/2

- **问题**：纪律 App 要做原生推送（owner 2026-11-08「后台提醒还靠 Bark」）。服务跑在独立 venv 里，`requirements.txt` 锁死只有 fastapi / uvicorn / httpx。APNs 要求 ES256 签名的 JWT 和 HTTP/2，但这个环境里没有 `cryptography`、`PyJWT`，也没有 `h2`。
- **死胡同**：
  - 往服务 venv 里装 `cryptography` 和 `httpx[http2]`：会改动锁定的依赖，影响跨机部署契约；而推送只是旁路功能，不值得。
  - 直接用 `openssl dgst -sign` 的输出当签名：openssl 输出的是 DER 编码的 ECDSA 签名，JOSE 要的是 64 字节的 r‖s，APNs 会返回 `InvalidProviderToken`。
  - 把 `authorization: bearer <jwt>` 直接写进 curl 命令行：同一台机器上用 `ps` 就能看到。
- **有效路径**：
  1. 用 `openssl dgst -sha256 -sign AuthKey.p8` 对 `header.claims` 签名，再自己解析 DER，把 r、s 去掉前导 0 后各补齐到 32 字节。
  2. JWT 缓存 40 分钟（APNs 只接受 20–60 分钟内的）。
  3. 发送用 `curl --http2 -H @<0600 临时头文件> --data-binary @-`，发完删掉头文件。
  4. 没有 `push/apns.json` 和 `.p8` 时整个模块保持空闲；第一次运行只把游标移到当前时间，不补发积压的事件。
  5. 测试里用 openssl 现场生成一把 P-256 测试钥，验证签名长度、JWT 不出现在命令行、410 设备被剔除。
- **通用规则**：锁死依赖的服务要接新协议时，先看系统自带工具能不能完成（openssl、curl、security），再考虑加依赖。凡是要传给子进程的密钥或令牌，一律通过 0600 文件或 stdin 传，不放在 argv 里。
- **牵连**：`yoyo/discipline_app/push.py`、`api.py`（`/v1/push/*`）、`tests/discipline_app/test_push.py`。APNs 密钥由 owner 在 Apple Developer 后台创建，放在服务运行目录 `push/` 下，永不入库。
