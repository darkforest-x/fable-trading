# 打开 TradingView App 不代表跳到了信号图表

- **问题**：Owner 的 Bark 历史信号链接进入网页，希望点击打开 iPhone TradingView；必须区分打开 App 和定位币种、周期。
- **死胡同**：搜索所得 `tradingview://chart?symbol=...` 没有官方 iOS 路由依据；桌面桥接、Bark 支持 scheme 和社区“打开成功”都不能证明手机会解析币种。不能猜一个地址直接替换线上精确图表链接。
- **有效路径**：2026-09-09 实读 [TradingView AASA](https://www.tradingview.com/apple-app-site-association)：`/chart/?symbol=?*` 明确 `exclude=true`，无 symbol 的 `/chart/` 才被包含。核官方 Bark 固定提交 `8c7973fa7a7791c766f70bfe044d6044e16a3045` 的 `Common/Client.swift`：HTTP 链接先尝试 Universal Link，失败回普通打开。采用声明支持的 App 入口，正文保留原精确网页图表，通知 copy 提供币种；明确手机端尚待实点、未验证自动切币种。
- **通用规则**：先验证目的 App 的关联域名及具体路由，再核发送方的点击代码。对“唤起 App / 精确页面 / 参数 / 用户设备实测”分别给证据，不能合为一个“跳转成功”。系统通知和历史消息是不同入口。不要承诺普通点击自动复制：Bark 通知复制按钮支持 copy，历史记录不保存该字段。
- **牵连**：`yoyo/monitor/bark.py`、`tests/monitor/test_bark.py`、`docs/ops/SPIKE_MODEL_CONFIRMATION.md`；系统 Universal Link 偏好和手机安装版本不受 Mac 服务控制。原信号、通知阶段、Telegram 禁用、历史回执均保持。
