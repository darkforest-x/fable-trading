# 独立手机 App 复用域名时，应保留独立入口与认证边界

- **问题**：纪律 App 的局域网测试 CA 在 Safari 可以访问 `/health`，但原生请求仍报证书错误。Owner 要求改用已有 `spikeok.com`，并保留现有模块。
- **死胡同**：Safari 成功不能证明原生 App 的系统信任已经正常；重启 App 后仍失败。旧网站的公网网关另有免登录、请求头转换规则，不能因为共用域名就把新 App 接口直接混进去。
- **有效路径**：给 `discipline.spikeok.com` 建独立 named tunnel、凭据、配置和 LaunchAgent，仅转发精确主机的 `/health`、`/v1/*`；保留服务端 Bearer 认证和正常 TLS 校验。公网实测健康 200、无令牌/错误令牌 401、有效令牌读取模拟状态 200、其余路径 404；原网站隧道和模拟引擎 PID 未变。Owner 随后确认 iPhone 原生 App 连接成功并进入合约页面。
- **通用规则**：域名可复用，认证与服务生命周期需单独验证。服务端探针、Safari 和原生 App 是不同验收层，必须分别记录；换用公共证书成功不等于查明旧 CA 被拒绝的根因。
- **牵连**：`yoyo/discipline_app/scripts/public_service.py`、`yoyo/discipline_app/README.md`。运行凭据只保存在用户 Library，服务仍依赖 Mac 在线；保持 paper 模式，没有调用交易写接口。[Cloudflare 配置说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/)。
