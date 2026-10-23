# 触感反馈必须能静默降级，不能改变操作结果

- **问题**：App 和网页需要一致的轻触感，但原生桥、浏览器接口和实际震动硬件不是同一种能力。
- **死胡同**：把存在的浏览器函数或成功的原生 Promise 当成手机实际震动证据不成立；给每个拖动像素发震动会挤占桥接，并且打开按钮和抽屉挂载可能重复触发。
- **有效路径**：原生 App 继续使用 Capacitor impact/notification；支持的移动网页才调用短时 Vibration API。用户可关闭反馈，影响当前会话并持久化偏好。相邻操作脉冲限频，结果通知单独处理；隐藏页面静音，异常吞掉，不等待反馈再执行动作。滚动、每帧位移、行情轮询不触发反馈。
- **通用规则**：可选硬件反馈属于表现层，调用成功不能证明物理效果或交易成交。浏览器拦截验收只证明触发与禁用逻辑；真机触感单独验收。
- **牵连**：`mobile/src/hapticDispatch.ts`、`feedback.ts` 和 `yoyo/monitor/static/haptics.js`。来源：[Capacitor Haptics v8](https://capacitorjs.com/docs/apis/haptics)、[W3C Vibration](https://www.w3.org/TR/vibration/)。iPhone Safari 无标准网页震动支持，本次没有用隐藏控件模拟该能力。
