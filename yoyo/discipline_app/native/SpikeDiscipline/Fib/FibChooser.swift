import SwiftUI

/// 最新触价 (owner 2026-11-08): BTC and ETH pinned on top, today's touches by default (本周 on demand),
/// 全部 / 上沿 / 下沿 / 收藏, one sort control (最新 / 评分 / 成交量), and the score shown on every row
/// (high scores highlighted). Favorites and hiding stay on this phone; hiding expires at the NY week end.
struct FibChooser: View {
    @Environment(FibStore.self) private var fib
    @Environment(AccountStore.self) private var account
    @Environment(TouchFeed.self) private var touches
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @AppStorage("fib.list.scope") private var scope = "today"
    @AppStorage("fib.list.filter") private var filter = "all"
    @AppStorage("fib.list.sort") private var sort = "latest"
    @State private var sortMenu = false
    @State private var hidden: String?

    private static let sorts: [(String, String)] = [("latest", "最新"), ("score", "评分"), ("volume", "成交量")]

    private var visible: [FibContract] {
        let query = search.trimmingCharacters(in: .whitespaces).uppercased()
        let today = Fmt.beijingDay(Date()), quotes = account.state?.quotes ?? [:], scores = touches.scoreBySymbol
        let rows = fib.rows.filter { row in
            guard let touch = row.latestTouch, !FibView.pinned.contains(row.symbol) else { return false }
            if scope == "today" && Fmt.beijingDay(Date(timeIntervalSince1970: touch.occurredMs / 1000)) != today { return false }
            let pass = switch filter {
            case "favorites": fib.isFavorite(row.symbol)
            case "upper": touch.side == .upper
            case "lower": touch.side == .lower
            default: true
            }
            return pass && (query.isEmpty || row.symbol.contains(query))
        }
        switch sort {
        case "score": return rows.sorted { (scores[$0.symbol] ?? -1) > (scores[$1.symbol] ?? -1) }
        case "volume": return rows.sorted { (Trading.turnover(quotes[$0.symbol]) ?? -1) > (Trading.turnover(quotes[$1.symbol]) ?? -1) }
        default: return rows.sorted { ($0.latestTouch?.occurredMs ?? 0) > ($1.latestTouch?.occurredMs ?? 0) }
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 8) {
                        SoftSegment(selection: $scope, options: [("today", "今日"), ("week", "本周")], height: 34).frame(width: 130)
                        Spacer(minLength: 0)
                        sortButton
                    }
                    .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 2, trailing: 0)).listRowSeparator(.hidden)
                    SoftSegment(selection: $filter, options: [("all", "全部"), ("upper", "上沿"), ("lower", "下沿"), ("favorites", "收藏")], height: 34)
                        .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 2, leading: 0, bottom: 4, trailing: 0)).listRowSeparator(.hidden)
                }
                Section {
                    ForEach(FibView.pinned, id: \.self) { symbol in pinnedRow(symbol) }
                }
                Section {
                    ForEach(visible) { row in rowView(row) }
                    if visible.isEmpty {
                        Text(!search.isEmpty ? "没有匹配的合约" : filter == "favorites" ? "暂无收藏" : scope == "today" ? "今日暂无触价" : "本周暂无触价")
                            .font(.subheadline).foregroundStyle(Color.muted).frame(maxWidth: .infinity).padding(.vertical, 18)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.canvas)
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "搜索合约")
            .textInputAutocapitalization(.characters)
            .navigationTitle("最新触价")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
            .onChange(of: scope) { _, _ in Haptics.select() }
            .onChange(of: filter) { _, _ in Haptics.select() }
            .overlay(alignment: .bottom) {
                if let hidden {
                    HStack(spacing: 12) {
                        Text("已隐藏 \(Fmt.coin(hidden))").font(.subheadline.weight(.medium))
                        Button("撤销") { Haptics.tap(); withAnimation(.snappy) { fib.restore(hidden); self.hidden = nil } }.font(.subheadline.weight(.bold))
                    }
                    .padding(.horizontal, 18).padding(.vertical, 12)
                    .frosted(Capsule(), veil: 0.85)
                    .padding(.bottom, 14)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: hidden) { try? await Task.sleep(for: .seconds(5)); withAnimation(.snappy) { self.hidden = nil } }
                }
            }
        }
    }

    private var sortButton: some View {
        Button { Haptics.tap(); sortMenu = true } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.up.arrow.down").font(.system(size: 12, weight: .bold))
                Text(Self.sorts.first { $0.0 == sort }?.1 ?? "").font(.system(size: 13, weight: .semibold))
            }
            .foregroundStyle(Color.ink).padding(.horizontal, 12).frame(height: 34)
            .background(Color.field, in: .capsule).contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .popover(isPresented: $sortMenu, arrowEdge: .top) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Self.sorts, id: \.0) { option in
                    Button { Haptics.select(); sort = option.0; sortMenu = false } label: {
                        HStack {
                            Text(option.1).font(.system(size: 15, weight: .semibold)).foregroundStyle(Color.ink)
                            Spacer(minLength: 20)
                            if sort == option.0 { Image(systemName: "checkmark").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.accentColor) }
                        }
                        .padding(.horizontal, 14).frame(width: 150, height: 40).contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 6)
            .presentationCompactAdaptation(.popover)
        }
        .accessibilityLabel("排序")
    }

    private func pinnedRow(_ symbol: String) -> some View {
        let row = fib.current?.items.first { $0.symbol == symbol }, coin = Fmt.coin(symbol), selected = symbol == fib.chartSymbol
        return Button { Haptics.select(); fib.focus(symbol); dismiss() } label: {
            HStack(spacing: 12) {
                CoinLogo(coin: coin, size: 32)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) { Text(coin).font(.body.weight(.semibold)).foregroundStyle(Color.ink); Image(systemName: "pin.fill").font(.system(size: 9)).foregroundStyle(Color.muted) }
                    Text(row?.latestTouch.map { "\($0.side == .upper ? "上沿" : "下沿") · \(FibView.touchTime($0.occurredMs))" } ?? "本周未触价")
                        .font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                }
                Spacer(minLength: 6)
                VStack(alignment: .trailing, spacing: 3) {
                    if let score = touches.scoreBySymbol[symbol] { ScoreBadge(score: score) }
                    Text(price(symbol, row: row)).font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .listRowBackground(selected ? Color.accentColor.opacity(0.10) : Color.white.opacity(0.7))
    }

    private func price(_ symbol: String, row: FibContract?) -> String {
        if let q = account.state?.quotes[symbol], let v = Double(q.last) { return Self.price(v) }
        return Self.price(row?.price)
    }

    private func rowView(_ row: FibContract) -> some View {
        let coin = Fmt.coin(row.symbol), favorite = fib.isFavorite(row.symbol), selected = row.symbol == fib.chartSymbol
        let touch = row.latestTouch
        return HStack(spacing: 12) {
            Button { Haptics.select(); fib.focus(row.symbol); dismiss() } label: {
                HStack(spacing: 12) {
                    CoinLogo(coin: coin, size: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(coin).font(.body.weight(.semibold)).foregroundStyle(Color.ink)
                            if let touch {
                                Text(touch.side == .upper ? "上沿" : "下沿").font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(touch.side == .upper ? ChartPalette.down : ChartPalette.up)
                                    .padding(.horizontal, 6).padding(.vertical, 1)
                                    .background((touch.side == .upper ? ChartPalette.down : ChartPalette.up).opacity(0.12), in: .capsule)
                            }
                        }
                        Text(touch.map { FibView.touchTime($0.occurredMs) } ?? "").font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                    }
                    Spacer(minLength: 6)
                    VStack(alignment: .trailing, spacing: 3) {
                        ScoreBadge(score: touches.scoreBySymbol[row.symbol])
                        Text(Self.price(row.price)).font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                    }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("查看 \(coin) K线")
            .accessibilityAddTraits(selected ? .isSelected : [])
            Button { Haptics.tap(); withAnimation(.snappy) { fib.toggleFavorite(row.symbol) } } label: {
                Image(systemName: favorite ? "star.fill" : "star").font(.body.weight(.semibold))
                    .foregroundStyle(favorite ? Color.amber : Color.muted.opacity(0.6))
                    .symbolEffect(.bounce, value: favorite)
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(favorite ? "取消收藏" : "收藏") \(coin)")
        }
        .listRowBackground(selected ? Color.accentColor.opacity(0.10) : Color.white.opacity(0.7))
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
