import SwiftUI

/// 设置与账户 · 通知与报警 (Stitch notify-settings / settings-unified, native grouped glass list).
struct SettingsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    @State private var token = ""
    @State private var barkBusy = false

    var body: some View {
        @Bindable var notices = notices
        NavigationStack {
            Form {
                if session.phase == .connected {
                    Section("交易账户") {
                        LabeledContent("账户", value: session.connection?.accountId == "primary" ? "主账户" : (session.connection?.accountId ?? "—"))
                        LabeledContent("模式", value: session.connection?.mode == "okx_live" ? "OKX 实盘" : "模拟")
                        LabeledContent("服务", value: session.health.map { "\($0.version) · 已连接" } ?? "已连接")
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
                        Button("清除本机授权", role: .destructive) { session.forget() }
                    } footer: { Text("服务端继续管理已有持仓；清除后需重新填写令牌。") }
                } else {
                    Section {
                        SecureField("服务访问令牌", text: $token).textContentType(.password).autocorrectionDisabled()
                        Button {
                            Task { do { try await session.connect(token: token); token = "" } catch { session.error = error.localizedDescription } }
                        } label: { if session.busy { ProgressView() } else { Text("连接") } }
                        .disabled(session.busy || token.count < 32)
                    } header: { Text("设备授权") } footer: {
                        Text(session.error ?? "首次连接后，令牌只保存在本机钥匙串。令牌位于 Mac 的 ~/Library/Application Support/SpikeDiscipline/access-token。")
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(AmbientBackground())
            .navigationTitle("设置与账户")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
    }

    private func setBark(_ value: Bool) async {
        guard let c = session.connection else { return }
        barkBusy = true
        defer { barkBusy = false }
        do { notices.data = try await BarkSwitch.set(value, connection: c) } catch { notices.error = error.localizedDescription }
    }
}
