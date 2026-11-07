import SwiftUI

/// 交易账户 (AccountSettings.tsx): current account, switch, and 添加 OKX 账户 on a secondary screen.
/// Credentials go over HTTPS to the owner's Mac and are never stored on the phone or shown again.
struct AccountSection: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @State private var accounts: [AccountSummary] = []
    @State private var switching: String?
    @State private var error: String?

    var body: some View {
        Section {
            if accounts.isEmpty {
                HStack { ProgressView().controlSize(.small); Text("正在读取账户").foregroundStyle(.secondary) }
            }
            ForEach(accounts) { item in
                let current = item.id == session.connection?.accountId
                Button {
                    Task { await select(item) }
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name).foregroundStyle(.primary)
                            Text(item.environment == "okx_live" ? "OKX 实盘" : "模拟账户").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if switching == item.id { ProgressView().controlSize(.small) }
                        else if current { Image(systemName: "checkmark").fontWeight(.semibold).foregroundStyle(Color.accentColor) }
                    }
                }
                .disabled(current || switching != nil || account.pendingEntry != nil || account.closeAttempt != nil)
                .accessibilityAddTraits(current ? .isSelected : [])
            }
            if session.connection?.mode == "okx_live" {
                NavigationLink("添加 OKX 账户") { AddAccountForm { accounts.append($0) } }
            }
            if let error { Text(error).font(.footnote).foregroundStyle(Color.down) }
        } header: { Text("交易账户") } footer: {
            Text("切换只改变当前查看与下单的账户。各账户的风控记录和持仓保护独立保留。")
        }
        .task(id: session.connection?.token.hashValue) { await load() }
    }

    private func load() async {
        do { accounts = try await account.accounts().items; error = nil }
        catch { self.error = error.localizedDescription }
    }

    private func select(_ item: AccountSummary) async {
        guard switching == nil, account.pendingEntry == nil else { return }
        switching = item.id; Haptics.tap()
        defer { switching = nil }
        do { try await session.switchAccount(item.id); Haptics.success() }
        catch { self.error = error.localizedDescription; Haptics.warning() }
    }
}

struct AddAccountForm: View {
    @Environment(AccountStore.self) private var account
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    var onAdded: (AccountSummary) -> Void
    @State private var name = ""
    @State private var key = ""
    @State private var secret = ""
    @State private var passphrase = ""
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                TextField("账户名称", text: $name).textInputAutocapitalization(.never)
                SecureField("API Key", text: $key)
                SecureField("Secret Key", text: $secret)
                SecureField("Passphrase", text: $passphrase)
            } header: { Text("实盘 · USDT 永续合约") } footer: {
                Text("使用读取、交易权限，不要开启提币权限。验证只读取账户；不会下单或更改 OKX 设置。凭据通过 HTTPS 交给你的 Mac 私有保存；不会保存在手机或显示在账户列表。")
            }
            .autocorrectionDisabled()
            if let error { Text(error).foregroundStyle(Color.down) }
            Button {
                Task { await save() }
            } label: {
                HStack { if saving { ProgressView() }; Text(saving ? "正在验证…" : "验证并保存").fontWeight(.semibold) }
            }
            .disabled(saving || name.isEmpty || key.isEmpty || secret.isEmpty || passphrase.isEmpty || name.count > 30)
        }
        .scrollContentBackground(.hidden)
        .background(AmbientBackground())
        .navigationTitle("添加 OKX 账户")
        .onDisappear { key = ""; secret = ""; passphrase = "" }
    }

    private func save() async {
        guard let connection = session.connection, !saving else { return }
        saving = true; error = nil; Haptics.tap()
        defer { saving = false }
        do {
            let added: AccountSummary = try await API.post("/v1/accounts", body: ["name": name, "key": key, "secret": secret, "passphrase": passphrase], connection: connection)
            key = ""; secret = ""; passphrase = ""
            onAdded(added); Haptics.success(); dismiss()
        } catch {
            self.error = error.localizedDescription; Haptics.warning()
        }
    }
}
