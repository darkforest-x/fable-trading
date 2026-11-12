import SwiftUI

struct ProtectionSelection: Identifiable {
    let position: Position
    let owner: Connection
    var id: String { position.id }
}

struct ProtectionPriceRow: View {
    let title: String
    let detail: String
    let placeholder: String
    @Binding var value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(title).font(.subheadline.weight(.semibold))
                Spacer()
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                TextField(placeholder, text: $value)
                    .font(.title3.weight(.semibold).monospacedDigit())
                    .keyboardType(.decimalPad).accessibilityLabel(title)
                Text("USDT").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14).frame(minHeight: 44)
            .background(Color.field, in: .rect(cornerRadius: 14))
        }
    }
}

struct ProtectionSheet: View {
    @Environment(AccountStore.self) private var account
    @Environment(\.dismiss) private var dismiss
    let selection: ProtectionSelection
    @State private var draft: ProtectionDraft
    @State private var busy = false
    @State private var error: String?
    @State private var needsReadback = false
    @FocusState private var focused: Bool

    init(selection: ProtectionSelection) {
        self.selection = selection
        _draft = State(initialValue: ProtectionDraft(selection.position))
    }

    private var current: Position? {
        guard account.connection == selection.owner else { return nil }
        return account.state?.positions.first { $0.id == selection.id }
    }
    private var tick: String? { account.state?.instruments[selection.position.symbol]?.tick }
    private var last: String? { account.state?.quotes[selection.position.symbol]?.last }

    private var issue: String? {
        guard let current else { return "当前持仓已结束或账户已切换" }
        guard account.online, account.quoteFresh(current.symbol) else { return "请等待最新行情" }
        guard current.protectionEditable == true else { return "持仓保护正在核对，请稍后再试" }
        return draft.issue(from: selection.position, current: current, last: last, tick: tick)
    }

    private var canSave: Bool {
        let changes = draft.changes(from: selection.position)
        return (changes.stop != nil || changes.targets.contains { $0 != nil }) && issue == nil && !needsReadback && !busy
    }

    var body: some View {
        let position = selection.position
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        CoinLogo(coin: Fmt.coin(position.symbol), size: 28)
                        Text(Fmt.coin(position.symbol)).font(.headline)
                        Pill(text: position.side == "long" ? "多" : "空", color: position.side == "long" ? .up : .down)
                        Spacer()
                        VStack(alignment: .trailing) {
                            Text("最新价").font(.caption).foregroundStyle(.secondary)
                            Text(Fmt.price(last, tick: tick)).font(.subheadline.weight(.semibold).monospacedDigit())
                        }
                    }
                    ProtectionPriceRow(title: "当前止损", detail: "仅可收紧", placeholder: "输入触发价", value: $draft.stop)
                    ForEach(draft.targets.indices, id: \.self) { index in
                        let flags = current?.takeProfitEditable ?? []
                        let editable = flags.indices.contains(index) && flags[index]
                        let stages = position.policy.takeProfits
                        let portion = index == stages.count - 1 ? "平余仓" : (stages.indices.contains(index) ? "平 \(Fmt.plainPercent(stages[index].last))%" : "")
                        ProtectionPriceRow(title: "止盈 \(index + 1)", detail: editable ? portion : "此档不可修改",
                                           placeholder: "—", value: $draft.targets[index])
                            .disabled(!editable)
                    }
                }
                .focused($focused)
                .disabled(busy || needsReadback)
                .padding(18)
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 8) {
                    if let message = error ?? issue {
                        Text(message).font(.footnote).foregroundStyle(Color.down)
                    }
                    HStack(spacing: 10) {
                        Button("取消") { dismiss() }.buttonStyle(.pill(.soft, height: 46)).disabled(busy)
                        Button {
                            Task { await save() }
                        } label: {
                            HStack {
                                if busy { ProgressView().tint(.white) }
                                Text(busy ? "正在核对…" : "保存修改")
                            }.frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.pill(.solid(.accentColor), height: 46))
                        .disabled(!canSave)
                    }
                }
                .padding(.horizontal, 18).padding(.vertical, 6).background(Color.canvas)
            }
            .background(Color.canvas)
            .navigationTitle("调整止盈止损").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItemGroup(placement: .keyboard) { Spacer(); Button("完成") { focused = false } } }
        }
        .interactiveDismissDisabled(busy)
        .onChange(of: draft.stop) { _, _ in error = nil }
        .onChange(of: draft.targets) { _, _ in error = nil }
    }

    private func save() async {
        guard canSave else { return }
        busy = true; error = nil; focused = false; Haptics.tap()
        defer { busy = false }
        let changes = draft.changes(from: selection.position)
        do {
            let receipt = try await account.updateProtection(selection.position, stop: changes.stop, targets: changes.targets,
                                                              requestId: Trading.newRequestId(), for: selection.owner)
            if receipt.status == "partial" {
                needsReadback = true
                error = "\(receipt.labels(receipt.updated))已更新，\(receipt.labels(receipt.rejected))未修改。请关闭后核对价格。"
            } else {
                dismiss()
            }
        } catch let failure as APIError where (400..<500).contains(failure.status) {
            error = failure.localizedDescription
            await account.refresh()
        } catch {
            needsReadback = true
            self.error = "修改结果待核对，请关闭后查看持仓的最新保护价格。"
            await account.refresh()
        }
    }
}
