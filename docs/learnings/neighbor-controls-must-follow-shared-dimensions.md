# 共用组件的尺寸变化必须传递给相邻控件

- **问题**：SPIKE 菜单统一为112px后，手机报警铃铛还固定在left92px，遮住32px菜单文字。上一轮只比对跨页面菜单本身，漏掉相邻控件的碰撞。
- **死胡同**：单独确认菜单尺寸、透明度和手机截图存在，不能证明相邻点击区域互不覆盖；把铃铛再改成一个独立常数也会在下一次尺寸变更时复发。
- **有效路径**：菜单实际宽度与铃铛偏移共用CSS变量；铃铛left＝菜单left＋菜单width＋10px，设置面板反向消除同一偏移。真实浏览器320/390/430px均测得10px净间距、设置面板x12–292；安全区24px时两按钮一起下移。
- **通用规则**：修改固定定位组件的几何尺寸时，搜索引用旧位置的邻居。验收除截图外，直接测量边界框交集、弹层边界及点击/退出焦点，不用堆高z-index掩盖碰撞。
- **牵连**：`spike-navigation.css`、`spike-alarm.css`；保持共享玻璃与现有通知行为。Chrome安全区测试通过[官方CDP环境覆盖](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/#method-setSafeAreaInsetsOverride)执行，不代替iPhone Safari实机验收。
