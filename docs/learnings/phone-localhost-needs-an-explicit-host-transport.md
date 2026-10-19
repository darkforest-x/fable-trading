# Phone localhost needs an explicit transport to the host service

- **问题**：Mac 浏览器可访问回环模拟服务，并不证明原生手机 App 能连接；iPhone 的 `127.0.0.1` 是手机自身。
- **死胡同**：直接复用浏览器地址，或只完成签名安装就宣布真机联调成功。放开所有 HTTP 或关闭证书校验也不是修复连接边界的方法。
- **有效路径**：保持回环执行服务不变，用独立存储的 Caddy 在指定私有 IPv4 上提供 HTTPS 反向代理；API 仍要求原令牌。公开证书下载口只允许一个证书路径，不提供 API 或私钥。用指定 CA 验证 TLS、确认缺失令牌返回 401、其他下载路径返回 404，且系统未信任 CA 时连接被拒绝。手机安装和信任测试 CA 由 owner 明确授权并手动完成，真机端状态另行核验。
- **通用规则**：先分清客户端的 localhost、传输路径、TLS 信任与服务认证；任何一个未通过，都不能以另外一个成功替代端到端验收。
- **牵连**：`yoyo/discipline_app/scripts/local_https.py`、iOS `Info.plist` 的本地网络用途说明；[Apple 测试证书信任](https://support.apple.com/en-us/102390)、[Caddy 配置](https://caddyserver.com/docs/caddyfile/options)。
