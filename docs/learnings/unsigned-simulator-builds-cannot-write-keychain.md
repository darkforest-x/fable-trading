# 不签名的模拟器包写不了 Keychain：表现为"无法保存本机授权"

- **问题**：iPhone 模拟器验收时，App 已连上 fixture（`/health` 和 `/v1/state` 都成功），设置页仍弹出"本次操作未完成 · 无法保存本机授权"，之后每一页都是未连接状态。
- **死胡同**：起初怀疑 ATS 拦了 `http://127.0.0.1` 或者 fixture 令牌不对。但请求已经到达服务端，失败点在连接成功之后的 `rememberSession`，也就是原生 DeviceSession 插件写 Keychain 那一步。为了图快用 `CODE_SIGNING_ALLOWED=NO` 构建的包，没有钥匙串访问组的 entitlement。
- **有效路径**：模拟器包改用 ad-hoc 签名：`CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM= PROVISIONING_PROFILE_SPECIFIER=`，不需要开发者账号。重装后 Keychain 写入正常，连接一次即可持久。
- **通用规则**：凡是 App 用到 Keychain、App Group 等依赖 entitlement 的能力，模拟器验收包至少要 ad-hoc 签名。看到"保存授权失败"这类错误，先查签名（`codesign -dv` 应显示 `Signature=adhoc`），不要先怀疑网络。
- **牵连**：`yoyo/discipline_app/mobile/ios/App`（DeviceSession 插件）、`xcodebuild … -sdk iphonesimulator`。真机包走 Personal Team 签名，不受影响。相关：[iOS WebKit 中文字体栈](ios-webkit-cjk-needs-pingfang-before-apple-system.md)。
