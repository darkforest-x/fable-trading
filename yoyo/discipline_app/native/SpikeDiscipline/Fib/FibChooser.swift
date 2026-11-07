import SwiftUI

/// 最新触价 list (FibChooser.tsx): search, 全部/上沿/下沿/收藏, star and hide (with undo).
/// Favorites and hiding stay on this phone; hiding expires at the NY week boundary.
struct FibChooser: View {
    @Environment(FibStore.self) private var fib
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @State private var filter = Filter.all
    @State private var hidden: String?

    enum Filter: String, CaseIterable, Identifiable {
        case all, upper, lower, favorites
        var id: String { rawValue }
        var title: String { switch self { case .all: "全部"; case .upper: "上沿"; case .lower: "下沿"; case .favorites: "收藏" } }
    }

    private var visible: [FibContract] {
        let query = search.trimmingCharacters(in: .whitespaces).uppercased()
        return fib.rows.filter { row in
            guard let side = row.latestTouch?.side else { return false }
            let pass = switch filter {
            case .all: true
            case .favorites: fib.isFavorite(row.symbol)
            case .upper: side == .upper
            case .lower: side == .lower
            }
            return pass && (query.isEmpty || row.symbol.contains(query))
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("触价筛选", selection: $filter.animation(.snappy)) {
                        ForEach(Filter.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    .onChange(of: filter) { _, _ in Haptics.select() }
                }
                Section {
                    ForEach(visible) { row in rowView(row) }
                    if visible.isEmpty {
                        Text(!search.isEmpty ? "没有匹配的合约" : filter == .favorites ? "暂无收藏，点星标即可收藏" : "暂无触价")
                            .font(.subheadline).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.vertical, 18)
                    }
                } header: {
                    Text("\(fib.current?.week ?? "") 纽约周 · 最新触价在前")
                } footer: {
                    Text("点击切换图表 · 左滑隐藏 · 收藏与隐藏只保存在本机")
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(AmbientBackground())
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "搜索已触价合约")
            .textInputAutocapitalization(.characters)
            .navigationTitle("最新触价")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Text("\(fib.rows.count) 个").font(.subheadline.weight(.semibold).monospacedDigit()).foregroundStyle(.secondary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(role: .close) { Haptics.tap(); dismiss() }
                }
            }
            .overlay(alignment: .bottom) {
                if let hidden {
                    HStack(spacing: 12) {
                        Text("已隐藏 \(Fmt.coin(hidden))").font(.subheadline.weight(.medium))
                        Button("撤销") { Haptics.tap(); withAnimation(.snappy) { fib.restore(hidden); self.hidden = nil } }
                            .font(.subheadline.weight(.bold))
                    }
                    .padding(.horizontal, 18).padding(.vertical, 12)
                    .glassEffect(.regular, in: .capsule)
                    .padding(.bottom, 14)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: hidden) { try? await Task.sleep(for: .seconds(5)); withAnimation(.snappy) { self.hidden = nil } }
                }
            }
        }
    }

    private func rowView(_ row: FibContract) -> some View {
        let coin = Fmt.coin(row.symbol), touch = row.latestTouch!, favorite = fib.isFavorite(row.symbol)
        let selected = row.symbol == fib.selected?.symbol
        return HStack(spacing: 12) {
            Button {
                Haptics.select(); fib.symbol = row.symbol; dismiss()
            } label: {
                HStack(spacing: 12) {
                    CoinLogo(coin: coin, size: 34)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 1) {
                            Text(coin).font(.body.weight(.semibold))
                            Text("/USDT").font(.caption).foregroundStyle(.secondary)
                        }
                        Text(FibView.touchTime(touch.occurredMs)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 6)
                    VStack(alignment: .trailing, spacing: 3) {
                        Pill(text: Fmt.edge(touch.side), color: touch.side == .upper ? .down : .up)
                        Text(Self.price(row.price)).font(.subheadline.weight(.semibold).monospacedDigit())
                    }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("查看 \(coin) K线")
            .accessibilityAddTraits(selected ? .isSelected : [])
            Button { Haptics.tap(); withAnimation(.snappy) { fib.toggleFavorite(row.symbol) } } label: {
                Image(systemName: favorite ? "star.fill" : "star").font(.body.weight(.semibold))
                    .foregroundStyle(favorite ? Color.amber : .secondary)
                    .symbolEffect(.bounce, value: favorite)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(favorite ? "取消收藏" : "收藏") \(coin)")
        }
        .listRowBackground(selected ? Color.accentColor.opacity(0.10) : Color.white.opacity(0.55))
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Haptics.press(); withAnimation(.snappy) { fib.hide(row.symbol); hidden = row.symbol }
            } label: { Label("隐藏", systemImage: "eye.slash") }
        }
    }

    static func price(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        if value >= 1000 { return value.formatted(.number.precision(.fractionLength(0...1))) }
        return value.formatted(.number.precision(.significantDigits(1...5)).grouping(.never))
    }
}
