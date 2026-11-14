import SwiftUI

enum MarketWatchlistLayout: Equatable {
    case compact, full
}

/// Shared read-only OKX market list for the contract selector and notification center.
/// The only write is a local favorite toggle; selecting a row is delegated to the caller.
struct MarketWatchlist: View {
    @Environment(AccountStore.self) private var account
    @Binding var search: String
    let layout: MarketWatchlistLayout
    let selectedSymbol: String?
    let onSelect: (String) -> Void

    @AppStorage("trade.watchlist.filter") private var filter = 0
    @AppStorage("trade.watchlist.sort") private var sortValue = "volume"
    @State private var ranked: [String] = []
    @State private var favorites = UserDefaults.standard.stringArray(forKey: "spike-favorites") ?? []

    private static let filters = ["全部", "自选", "最近"]
    private var sort: Trading.ContractSort { Trading.ContractSort(rawValue: sortValue) ?? .volume }

    private var catalog: [String: Instrument] { account.state?.instruments ?? [:] }

    private var recent: Set<String> {
        guard let state = account.state else { return [] }
        return Set(state.positions.map(\.symbol) + state.pending.map(\.symbol) + state.closed.map(\.symbol))
    }

    private var filteredRows: [String] {
        let query = searchKey(search)
        let recentSymbols = filter == 2 && layout == .full ? recent : []
        let favoriteSet = Set(favorites)
        return ranked.filter { symbol in
            (layout == .compact || filter == 0 || (filter == 1 && favoriteSet.contains(symbol)) || (filter == 2 && recentSymbols.contains(symbol)))
                && (query.isEmpty || searchKey(symbol).contains(query))
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            if layout == .compact {
                compactHeader
                searchField
                    .padding(.horizontal, 10)
                    .padding(.bottom, 7)
                compactRows
            } else {
                searchField
                    .padding(.horizontal, 14)
                    .padding(.top, 8)
                fullControls
                tableHeader
                Divider()
                fullRows
            }
        }
        .background(Color(uiColor: .systemBackground))
        .onChange(of: catalog, initial: true) { _, _ in rank() }
    }

    private var compactHeader: some View {
        HStack(spacing: 8) {
            Text("市场快速检索")
                .font(.scaled(size: 11, weight: .bold))
                .foregroundStyle(Color.muted)
            Spacer(minLength: 4)
            sortMenu(compact: true)
        }
        .padding(.horizontal, 10)
        .padding(.top, 8)
        .padding(.bottom, 7)
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Color.muted)
            TextField(layout == .compact ? "搜索标的（BTC、ETH、SOL…）" : "搜索币种或合约", text: $search)
                .font(layout == .compact ? .scaled(size: 12) : .subheadline)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .submitLabel(.search)
            if !search.isEmpty {
                Button { search = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Color.muted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("清除搜索")
            }
        }
        .padding(.horizontal, 10)
        .frame(height: layout == .compact ? 34 : 42)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        }
    }

    private var fullControls: some View {
        HStack(spacing: 5) {
            ForEach(Array(Self.filters.enumerated()), id: \.offset) { index, title in
                Button {
                    guard filter != index else { return }
                    Haptics.select()
                    filter = index
                } label: {
                    Text(title)
                        .font(.scaled(size: 13, weight: filter == index ? .semibold : .medium))
                        .foregroundStyle(filter == index ? Color(uiColor: .systemBackground) : Color.ink)
                        .padding(.horizontal, 12)
                        .frame(height: 31)
                        .background(filter == index ? Color.ink : Color.field, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(filter == index ? .isSelected : [])
            }
            Spacer(minLength: 2)
            sortMenu(compact: false)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 5)
    }

    private var tableHeader: some View {
        HStack {
            Text("合约").padding(.leading, 4)
            Spacer()
            Text("最新价 / 今日涨跌")
        }
        .font(.caption2)
        .foregroundStyle(Color.muted)
        .padding(.horizontal, 16)
        .padding(.bottom, 6)
    }

    private var compactRows: some View {
        let rows = filteredRows.prefix(2)
        return VStack(spacing: 0) {
            ForEach(Array(rows), id: \.self) { symbol in
                row(symbol, compact: true)
            }
            if rows.isEmpty {
                Text(emptyMessage)
                    .font(.scaled(size: 11))
                    .foregroundStyle(Color.muted)
                    .frame(maxWidth: .infinity, minHeight: 36)
            }
        }
        .padding(.horizontal, 8)
        .padding(.bottom, 8)
    }

    private var fullRows: some View {
        let rows = filteredRows
        return List {
            ForEach(rows, id: \.self) { symbol in
                row(symbol, compact: false)
                    .listRowInsets(EdgeInsets(top: 3, leading: 10, bottom: 3, trailing: 10))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
            if rows.isEmpty {
                Text(emptyMessage)
                    .font(.subheadline)
                    .foregroundStyle(Color.muted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 24)
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .environment(\.defaultMinListRowHeight, 1)
    }

    private var emptyMessage: String {
        if !searchKey(search).isEmpty { return "没有匹配的合约" }
        if filter == 1 { return "在全部合约中点星标即可加入自选" }
        if filter == 2 { return "交易过的合约会显示在这里" }
        return "暂无可交易合约"
    }

    private func row(_ symbol: String, compact: Bool) -> some View {
        let state = account.state
        let quote = state?.quotes[symbol]
        return MarketWatchlistRow(
            symbol: symbol,
            quote: quote,
            tick: state?.instruments[symbol]?.tick,
            now: account.now,
            fresh: account.quoteFresh(symbol),
            favorite: favorites.contains(symbol),
            selected: symbol == selectedSymbol,
            compact: compact,
            selectsContract: selectedSymbol != nil,
            onToggleFavorite: { toggleFavorite(symbol) },
            onSelect: { onSelect(symbol) }
        )
    }

    private func sortMenu(compact: Bool) -> some View {
        Menu {
            Button("24h 估算成交额") { changeSort(.volume) }
            Button("今日涨幅") { changeSort(.gainers) }
            Button("今日跌幅") { changeSort(.losers) }
            Divider()
            Button("更新排名") { rank() }
        } label: {
            HStack(spacing: 4) {
                if compact { Text("按\(sortTitle)") }
                else { Text(sortTitle) }
                Image(systemName: "arrow.up.arrow.down").font(.caption2)
            }
            .font(compact ? .scaled(size: 10, weight: .medium) : .caption)
            .foregroundStyle(Color.accentColor)
            .frame(minHeight: 34)
            .contentShape(Rectangle())
        }
        .tint(.primary)
        .accessibilityLabel("行情排序")
        .accessibilityValue(sortTitle)
    }

    private var sortTitle: String {
        switch sort {
        case .volume: "24H 成交额"
        case .gainers: "今日涨幅"
        case .losers: "今日跌幅"
        }
    }

    private func changeSort(_ value: Trading.ContractSort) {
        Haptics.select()
        sortValue = value.rawValue
        rank()
    }

    private func rank() {
        let symbols = catalog.values.compactMap { $0.tradable == false ? nil : $0.symbol }
        ranked = Trading.rank(symbols, quotes: account.state?.quotes ?? [:], now: account.now, sort: sort)
    }

    private func toggleFavorite(_ symbol: String) {
        if let index = favorites.firstIndex(of: symbol) { favorites.remove(at: index) }
        else { favorites.append(symbol) }
        UserDefaults.standard.set(favorites, forKey: "spike-favorites")
        Haptics.select()
    }

    private func searchKey(_ raw: String) -> String {
        raw.uppercased().replacingOccurrences(of: ".P", with: "").filter { $0.isLetter || $0.isNumber }
    }
}

private struct MarketWatchlistRow: View {
    let symbol: String
    let quote: Quote?
    let tick: String?
    let now: Double
    let fresh: Bool
    let favorite: Bool
    let selected: Bool
    let compact: Bool
    let selectsContract: Bool
    let onToggleFavorite: () -> Void
    let onSelect: () -> Void

    private var coin: String { Fmt.coin(symbol) }
    private var dayChange: Double? {
        guard fresh, let quote else { return nil }
        return Trading.dayChange(quote, now: now)
    }

    private var delta: Double? {
        guard let change = dayChange, change > -100,
              let value = quote.flatMap({ Double($0.last) }), value > 0 else { return nil }
        return value * change / (100 + change)
    }

    private var turnover: String {
        guard fresh else { return "24h —" }
        return Trading.turnoverLabel(Trading.turnover(quote))
    }

    var body: some View {
        HStack(spacing: 4) {
            Button(action: onToggleFavorite) {
                Image(systemName: favorite ? "star.fill" : "star")
                    .font(.system(size: compact ? 12 : 14, weight: .medium))
                    .foregroundStyle(favorite ? Color.amber : Color.muted.opacity(0.75))
                    .frame(width: compact ? 24 : 38, height: compact ? 38 : 48)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(favorite ? "取消自选" : "加入自选") \(coin)")

            Button(action: onSelect) {
                HStack(spacing: compact ? 8 : 9) {
                    if !compact { CoinLogo(coin: coin, size: 28) }
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) {
                            Text(compact ? coin : coin + "USDT")
                                .font(.scaled(size: compact ? 12 : 14, weight: .semibold))
                                .foregroundStyle(Color.ink)
                                .lineLimit(1)
                                .minimumScaleFactor(0.75)
                            Text("永续")
                                .font(.scaled(size: 9, weight: .medium))
                                .foregroundStyle(Color.muted)
                                .padding(.horizontal, 4)
                                .padding(.vertical, 1)
                                .background(Color.field, in: Capsule())
                            if selected {
                                Image(systemName: "checkmark.circle.fill")
                                    .font(.caption2)
                                    .foregroundStyle(Color.accentColor)
                            }
                        }
                        Text(compact ? turnover : "OKX 永续 · \(turnover)")
                            .font(.scaled(size: compact ? 9 : 10))
                            .foregroundStyle(Color.muted)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(fresh ? Fmt.price(quote?.last, tick: tick) : "—")
                            .font(.scaled(size: compact ? 12 : 14, weight: .semibold).monospacedDigit())
                            .foregroundStyle(Color.ink)
                        if let dayChange {
                            let percentage = Fmt.signed(dayChange) + "%"
                            Text(compact ? percentage : (delta.map { Fmt.signed($0, Fmt.decimals(tick: tick)) + "  " } ?? "") + percentage)
                                .font(.scaled(size: compact ? 10 : 11, weight: .medium).monospacedDigit())
                                .foregroundStyle(dayChange == 0 ? Color.muted : dayChange > 0 ? Color.up : Color.down)
                        } else {
                            Text("—").font(.scaled(size: compact ? 10 : 11)).foregroundStyle(Color.muted)
                        }
                    }
                }
                .frame(minHeight: compact ? 40 : 52)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel((selectsContract ? "选择 \(coin) 合约" : "查看 \(coin) K线") + "，" + (fresh ? Fmt.price(quote?.last, tick: tick) : "行情过期"))
            .accessibilityAddTraits(selected ? .isSelected : [])
        }
        .padding(.horizontal, compact ? 5 : 7)
        .padding(.vertical, compact ? 0 : 2)
        .background(selected ? Color.accentColor.opacity(0.07) : Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(alignment: .bottom) { Divider().padding(.leading, compact ? 30 : 62) }
        .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
