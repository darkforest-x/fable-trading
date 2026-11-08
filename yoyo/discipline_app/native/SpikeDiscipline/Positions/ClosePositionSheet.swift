import SwiftUI

/// 平仓 (ClosePosition.tsx): a percentage of the current remaining size, rounded to the contract step.
/// Live closes persist their identity before sending (AccountStore.close); a timeout is read back, never resent.
struct ClosePositionSheet: View {
    @Environment(AccountStore.self) private var account
    @Environment(\.dismiss) private var dismiss
    let position: Position
    @State private var percent: Double = 50
    @State private var busy = false
    @State private var error: String?
    @State private var lastTick = 50

    var body: some View {
        let state = account.state
        let current = state?.positions.first { $0.id == position.id } ?? position
        let amount = Trading.positionAmount(remaining: current.remaining, entry: current.entry, quote: state?.quotes[current.symbol],
                                            online: account.online, serverTime: state?.serverTime ?? 0).value
        let instrument = state?.instruments[current.symbol]
        let step = Double(instrument?.step ?? "") ?? .nan, minimum = Double(instrument?.minimum ?? "") ?? 0
        let remaining = Double(current.remaining) ?? 0, pct = Int(percent)
        let quantity = pct == 100 ? remaining : step > 0 ? floor((remaining * Double(pct) / 100 + step * 1e-9) / step) * step : .nan
        let valid = quantity > 0 && (pct == 100 || (quantity >= minimum && remaining - quantity >= minimum - step * 1e-8))
        let closing = amount.flatMap { quantity.isFinite && remaining > 0 ? $0 * quantity / remaining : nil }

        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        CoinLogo(coin: Fmt.coin(current.symbol), size: 30)
                        Text(Fmt.coin(current.symbol)).font(.headline)
                        Spacer()
                        Pill(text: "\(current.side == "long" ? "多" : "空") · \(current.leverage)x", color: current.side == "long" ? .up : .down)
                    }
                    HStack {
                        Text("当前剩余持仓").foregroundStyle(.secondary)
                        Spacer()
                        Text(Fmt.amount(amount) + " USDT").font(.headline.monospacedDigit())
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("平仓比例").font(.subheadline.weight(.semibold))
                            Spacer()
                            Text("\(pct)").font(.scaled(size: 34, weight: .bold, design: .rounded).monospacedDigit()).contentTransition(.numericText(value: percent))
                            Text("%").font(.headline).foregroundStyle(.secondary)
                        }
                        Slider(value: $percent, in: 1...100, step: 1)
                            .disabled(busy)
                            .onChange(of: percent) { _, value in
                                let tick = Int(value) / 25
                                if tick != lastTick / 25 || Int(value) == 100 || Int(value) == 1 { Haptics.select() }
                                lastTick = Int(value)
                            }
                        HStack(spacing: 8) {
                            ForEach([25, 50, 75, 100], id: \.self) { value in
                                Button { withAnimation(.snappy) { percent = Double(value) }; value == 100 ? Haptics.open() : Haptics.press() } label: {
                                    Text("\(value)%").font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity).frame(height: 34)
                                }
                                .buttonStyle(.pill(pct == value ? .tinted(.accentColor) : .soft, height: 34))
                                .disabled(busy)
                            }
                        }
                    }
                    .padding(16).glassCard()
                    HStack {
                        Metric(title: "本次平仓 · USDT", value: Fmt.amount(closing))
                        Metric(title: "平仓后剩余 · USDT", value: Fmt.amount(amount.flatMap { a in closing.map { max(0, a - $0) } }))
                    }
                    .padding(16).glassCard()
                    Text((!valid ? "低于最小平仓数量，请调整比例或选择 100%。" : pct == 100 ? "平掉当前全部剩余仓位。" : "剩余仓位继续执行保护规则。") + " 按合约步长取整。")
                        .font(.footnote).foregroundStyle(valid ? Color.secondary : Color.down)
                    if let error { Text(error).font(.footnote.weight(.medium)).foregroundStyle(Color.down) }
                }
                .padding(18)
            }
            .safeAreaInset(edge: .bottom) {
                Button {
                    Task { await submit() }
                } label: {
                    HStack(spacing: 8) {
                        if busy { ProgressView().tint(.white); Text("正在提交…") } else { Text(Int(percent) == 100 ? "确认全部平仓" : "确认平仓 \(Int(percent))%") }
                    }
                    .font(.body.weight(.bold)).frame(maxWidth: .infinity).frame(height: 50)
                }
                .buttonStyle(.pill(.solid(.down), height: 50))
                .disabled(busy || !account.online || !valid)
                .padding(.horizontal, 18).padding(.bottom, 8)
            }
            .scrollContentBackground(.hidden)
            .background(Color.canvas)
            .navigationTitle("平仓")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() }.disabled(busy) }.sharedBackgroundVisibility(.hidden)
            }
        }
        .interactiveDismissDisabled(busy)
    }

    private func submit() async {
        guard !busy else { return }
        busy = true; error = nil; Haptics.tap()
        defer { busy = false }
        do {
            try await account.close(position, percent: Int(percent))
            dismiss()
        } catch {
            self.error = error.localizedDescription; Haptics.warning()
        }
    }
}
