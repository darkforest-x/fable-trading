import SwiftUI

/// 设置与账户 · 通知与报警 · 体验 (Stitch notify-settings / settings-unified, native grouped glass list).
/// No token field (owner 2026-11-07): this personal build connects with the Mac's existing authorization.
struct SettingsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    @AppStorage("appearance") private var appearance = "system"
    @State private var haptics = Haptics.enabled
    @State private var barkBusy = false
    @AppStorage("spike-confirm-trades") private var confirmTrades = false
    @AppStorage("touch.highScore") private var highScore = 0.5

    var body: some View {
        @Bindable var notices = notices
        NavigationStack {
            Form {
                Section("连接") {
                    LabeledContent("状态") {
                        HStack(spacing: 6) {
                            Circle().fill(session.phase == .connected ? Color.up : Color.amber).frame(width: 8, height: 8)
                            Text(session.phase == .connected ? "已连接" : session.phase == .restoring ? "连接中" : "未连接")
                        }
                    }
                    LabeledContent("账户", value: session.connection == nil ? "—" : (session.connection?.accountId == "primary" ? "主账户" : session.connection!.accountId))
                    LabeledContent("模式", value: session.connection?.mode == "okx_live" ? "OKX 实盘" : session.connection == nil ? "—" : "模拟")
                    if let health = session.health { LabeledContent("服务版本", value: health.version) }
                    if let error = session.error { Text(error).font(.footnote).foregroundStyle(Color.amber) }
                    Button("重新连接") { Haptics.tap(); session.reconnect() }
                }
                if session.connection != nil { AccountSection() }
                Section {
                    Toggle(isOn: $confirmTrades) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("开仓二次确认")
                            Text("开仓可跳过二次确认；平仓选择比例后提交，风控仍执行。").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                } header: { Text("交易操作") }
                Section {
                    HStack {
                        Text("高分阈值")
                        Spacer()
                        Text(String(format: "%.2f", highScore)).font(.body.monospacedDigit().weight(.semibold)).foregroundStyle(Color.accentColor)
                    }
                    Slider(value: $highScore, in: 0.3...0.8, step: 0.05)
                } header: { Text("触价评分") } footer: {
                    Text("评分达到阈值的触价在列表中高亮。服务端的“通过评分”指 live-wick6x3 模拟账户的入场门槛（触价 5m 收盘评分 ≥ 0.5）。")
                }
                Section {
                    Toggle("App 内报警弹窗", isOn: $notices.prefs.popup)
                    Toggle("报警声音", isOn: $notices.prefs.sound)
                    Toggle("1.618 触价提醒", isOn: $notices.prefs.fib)
                } header: { Text("通知与报警") } footer: {
                    Text("App 打开时顶部提醒并震动。1.618 只提醒已推送的触价（评分 >\(Fmt.price(notices.data?.channels.fib.minScore ?? 0.4)) 或 live- 账户开仓）。")
                }
                Section {
                    LabeledContent("全市场插针报警") {
                        Text(notices.data.map { $0.channels.alarm.bark ? "已开启 · 本机配置" : "未配置" } ?? "—")
                            .foregroundStyle(notices.data?.channels.alarm.bark == true ? Color.up : .secondary)
                    }
                    Toggle(isOn: Binding(get: { notices.data?.channels.fib.bark ?? false }, set: { value in Task { await setBark(value) } })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("1.618 触价信号")
                            Text(notices.data.map { $0.channels.fib.barkConfigured ? "已推送 \($0.channels.fib.barkSent) 条" : "本机尚未配置 Bark" } ?? "连接后读取")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .disabled(barkBusy || notices.data?.channels.fib.barkConfigured != true)
                } header: { Text("Bark 推送 · App 关闭时") } footer: {
                    Text("后台推送经 Bark 送达；确认报警仍在监控工作台操作。")
                }
                Section {
                    Toggle(isOn: $haptics) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("触感反馈")
                            Text("点按、切换页面与分段、开关、刷新与报警").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Picker("外观", selection: $appearance) { Text("跟随系统").tag("system"); Text("浅色").tag("light"); Text("深色").tag("dark") }
                } header: { Text("体验设置") }
            }
            .onChange(of: haptics) { _, value in Haptics.enabled = value; Haptics.tap() }
            .onChange(of: notices.prefs) { _, _ in Haptics.select() }
            .onChange(of: appearance) { _, _ in Haptics.select() }
            .onChange(of: confirmTrades) { _, _ in Haptics.select() }
            .onChange(of: highScore) { _, _ in Haptics.select() }
            .scrollContentBackground(.hidden)
            .background(AmbientBackground())
            .navigationTitle("设置与账户")
            .toolbarTitleDisplayMode(.inlineLarge)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        }
    }

    private func setBark(_ value: Bool) async {
        guard let c = session.connection else { return }
        barkBusy = true
        defer { barkBusy = false }
        do { notices.data = try await BarkSwitch.set(value, connection: c); Haptics.success() }
        catch { notices.error = error.localizedDescription; Haptics.warning() }
    }
}
