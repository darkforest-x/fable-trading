import Charts
import SwiftUI

/// 统计 (owner 2026-11-08 “去掉没用的文字，保持简洁可用，图标好看一点，交互好一点”;
/// Stitch screen b4127036a8594e26aed72c8918cd1a5b): app-managed trades only — equity hero, period,
/// four icon tiles, a cumulative net PnL step chart you can scrub, long / short, per-coin rows and
/// the 交易纪律 entry. Accounting rules are unchanged from 2.x; only the explanatory copy is gone.
struct StatsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Binding var showSettings: Bool
    let active: Bool
    @AppStorage("stats.days") private var days = 30
    @State private var data: Statistics?
    @State private var error: String?
    @State private var loading = false
    @State private var discipline = false
    @State private var picked: String?
    @State private var scroll = ScrollPosition(edge: .top)
    @Environment(\.reselect) private var reselect
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var key: String { "\(session.connection?.accountId ?? "")|\(session.connection?.mode ?? "")|\(days)" }

    var body: some View {
        ZStack {
            AmbientBackground()
            VStack(spacing: 0) {
                CompactPageHeader(title: "表现统计", subtitle: "账户净值 · App 内已平仓交易") {
                    RoundIcon(systemName: "gearshape") { showSettings = true }.accessibilityLabel("设置")
                }
                SoftSegment(selection: $days, options: [(7, "近 7 天"), (30, "近 30 天"), (0, "全部历史")])
                    .padding(.horizontal, 14)
                    .padding(.vertical, 7)
                    .background(Color(uiColor: .systemBackground))
                    .overlay(alignment: .bottom) { Color.primary.opacity(0.07).frame(height: 1) }
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        if session.connection == nil { ConnectPrompt() }
                        LoadState(loading: loading, error: error, stale: data != nil)
                        hero
                        curve
                        directions
                        symbols
                        Button { Haptics.open(); discipline = true } label: { disciplineRow }.buttonStyle(CardPressStyle())
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 10)
                    .padding(.bottom, 24)
                }
                .scrollPosition($scroll)
                .refreshable { await load(); Haptics.success() }
                .contentMargins(.bottom, 96, for: .scrollContent)
                .scrollEdgeEffectHidden(true, for: [.top, .bottom])
            }
        }
        .sheet(isPresented: $discipline) {
            NavigationStack { DisciplineView() }.presentationDragIndicator(.visible)
        }
        .onChange(of: days) { _, _ in Haptics.select(); picked = nil }
        .onChange(of: key) { _, _ in
            data = nil
            error = nil
            loading = false
            picked = nil
            restore()
        }
        .onChange(of: reselect) { _, _ in withAnimation(.snappy) { scroll.scrollTo(edge: .top) } }
        .onAppear { if data == nil { restore() } }
        .task(id: "\(key)|\(active)") {
            guard active, session.connection != nil else { return }
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    /// Last statistics of this account and range from a previous launch (display only, LaunchCache).
    private func restore() {
        guard let connection = session.connection else { data = nil; return }
        guard let saved = LaunchCache.read(Statistics.self, "stats-\(days)", scope: account.scope),
              saved.periodDays == days, saved.accountId == connection.accountId, saved.mode == connection.mode else {
            data = nil
            return
        }
        data = saved
    }

    private func load() async {
        guard let connection = session.connection else { return }
        let requested = key
        loading = true
        defer { if requested == key { loading = false } }
        do {
            let next = try await account.statistics(days: days)
            guard next.mode == connection.mode, next.periodDays == days, next.accountId == connection.accountId else { throw APIError(status: 0, message: "统计响应与当前账户不匹配") }
            guard requested == key else { return }
            withAnimation(.snappy) { data = next }
            error = nil
            LaunchCache.write(next, "stats-\(days)", scope: account.scope)
        } catch is CancellationError {
        } catch {
            guard requested == key else { return }
            self.error = error.localizedDescription; session.handle(error)
        }
    }

    private var live: Bool { session.connection?.mode == "okx_live" }

    private func tone(_ text: String?) -> Color {
        guard let v = text.flatMap(Double.init), v != 0 else { return Color.ink }
        return v > 0 ? .up : .down
    }

    // MARK: hero

    private var hero: some View {
        let s = data, state = account.display
        let currentAt = state.map { live ? ($0.privateAt ?? 0) : $0.serverTime } ?? 0
        let cachedAt = s.map { $0.accountUpdatedAt ?? $0.serverTime } ?? 0
        let useCurrent = state != nil && currentAt > 0 && currentAt >= cachedAt
        let equity = useCurrent ? state?.equity : s?.account.equity
        let updatedAt = useCurrent ? currentAt : cachedAt
        let stale = error != nil || !account.online || updatedAt == 0 || Date().timeIntervalSince1970 - updatedAt > 15 || (useCurrent && state?.privateError != nil)
        let a = s?.account
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("账户累计净盈亏").font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted)
                Spacer()
                // Syncing after a launch from the cached snapshot (Stitch c8bea04c… “同步最新中”).
                if stale && session.connection != nil && NetworkWatch.shared.online {
                    ProgressView().controlSize(.mini).accessibilityLabel("同步中")
                }
                tag(stale ? (updatedAt > 0 ? "上次 \(Self.time(updatedAt))" : loading ? "读取中" : "待连接") : live ? "实盘 · 仅 App 交易" : "模拟",
                    tint: stale ? .amber : live ? .down : .up)
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(Fmt.signed(a?.netPnl.flatMap(Double.init)))
                    .font(.scaled(size: 31, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundStyle(tone(a?.netPnl))
                    .contentTransition(.numericText(value: Double(a?.netPnl ?? "0") ?? 0)).animation(.snappy, value: a?.netPnl)
                Text("USDT").font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted)
                Spacer(minLength: 2)
                if let r = a?.returnFraction {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text("\((Double(r) ?? 0) > 0 ? "+" : "")\(Fmt.fractionPercent(r, 2))%")
                            .font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(tone(r))
                        Text("总收益率").font(.caption2).foregroundStyle(Color.muted)
                    }
                }
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: dynamicTypeSize >= .xxLarge ? 2 : 3),
                      alignment: .leading, spacing: 8) {
                heroMetric("账户净值", equity, signed: false)
                heroMetric("已实现", a?.realizedPnl, signed: true)
                heroMetric("浮动", a?.unrealizedPnl, signed: true)
            }
            .padding(9)
            .background(Color.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            Divider().padding(.vertical, 2)
            tiles
        }
        .padding(14).glassCard(cornerRadius: 20)
    }

    private func heroMetric(_ title: String, _ value: String?, signed: Bool) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(Color.muted)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(signed ? Fmt.signed(value.flatMap(Double.init)) : Fmt.amount(value))
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(signed ? tone(value) : Color.ink)
                    .lineLimit(1).minimumScaleFactor(0.72)
                Text("USDT").font(.caption2.weight(.medium)).foregroundStyle(Color.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func tag(_ text: String, tint: Color) -> some View {
        HStack(spacing: 4) {
            Circle().fill(tint).frame(width: 6, height: 6)
            Text(text).font(.scaled(size: 11, weight: .semibold).monospacedDigit()).foregroundStyle(tint)
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        .background(tint.opacity(0.1), in: .capsule)
    }

    // MARK: tiles

    private var tiles: some View {
        let s = data?.closed
        let columns = Array(repeating: GridItem(.flexible(), spacing: 7), count: dynamicTypeSize >= .xxLarge ? 2 : 4)
        return LazyVGrid(columns: columns, spacing: 8) {
            tile("list.bullet", .accentColor, "已平仓", s.map { "\($0.count)" } ?? "—", "笔")
            tile("scope", .up, "胜率", Fmt.fractionPercent(s?.winRate), "%")
            tile("scalemass", .indigo, "盈亏比", Fmt.amount(s?.payoffRatio), nil)
            tile("sum", .amber, "平均 R", Fmt.signed(s?.averageNetR.flatMap(Double.init)), nil)
        }
        .padding(8)
        .background(Color.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func tile(_ icon: String, _ tint: Color, _ title: String, _ value: String, _ unit: String?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 11, weight: .semibold)).foregroundStyle(tint)
                Text(title).font(.scaled(size: 9, weight: .medium)).foregroundStyle(Color.muted).lineLimit(1).minimumScaleFactor(0.72)
            }
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(.scaled(size: 13, weight: .bold).monospacedDigit()).foregroundStyle(Color.ink).contentTransition(.numericText())
                    .lineLimit(1).minimumScaleFactor(0.65)
                if let unit { Text(unit).font(.scaled(size: 9, weight: .medium)).foregroundStyle(Color.muted).fixedSize() }
            }
        }
        .frame(maxWidth: .infinity, minHeight: 39, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: curve

    private var curve: some View {
        let points = data?.curve ?? []
        let net = data?.closed.netPnl
        let last = Double(points.last?.pnl ?? "0") ?? 0
        let hue = last >= 0 ? Color.up : Color.down
        let selected = picked.flatMap { d in points.first { $0.date == d } }
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("累计盈亏").font(.headline).foregroundStyle(Color.ink)
                Spacer()
                Text(Fmt.signed(net.flatMap(Double.init)) + " USDT").font(.headline.monospacedDigit()).foregroundStyle(tone(net))
                    .contentTransition(.numericText())
            }
            if points.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "chart.line.uptrend.xyaxis").font(.title2).foregroundStyle(Color.muted.opacity(0.7))
                    Text(session.connection == nil ? "连接后查看" : loading && data == nil ? "正在读取…" : error != nil && data == nil ? "记录暂不可用" : "暂无已平仓记录")
                        .font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 30)
            } else {
                Chart {
                    RuleMark(y: .value("零", 0)).foregroundStyle(Color.muted.opacity(0.35)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 5]))
                    ForEach(points, id: \.date) { p in
                        AreaMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0))
                            .interpolationMethod(.stepEnd)
                            .foregroundStyle(LinearGradient(colors: [hue.opacity(0.22), hue.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0))
                            .interpolationMethod(.stepEnd)
                            .foregroundStyle(hue)
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineJoin: .round))
                    }
                    if let p = selected {
                        RuleMark(x: .value("日期", p.date)).foregroundStyle(Color.accentColor.opacity(0.6)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                            .annotation(position: .top, spacing: 4, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                HStack(spacing: 6) {
                                    Text(Self.day(p.date)).foregroundStyle(.white.opacity(0.8))
                                    Text(Fmt.signed(Double(p.pnl)) + " U").foregroundStyle(.white)
                                }
                                .font(.scaled(size: 11, weight: .semibold).monospacedDigit())
                                .padding(.horizontal, 9).padding(.vertical, 5)
                                .background(Color(white: 0.12), in: .capsule)
                            }
                        PointMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0))
                            .symbolSize(70).foregroundStyle(Color.accentColor)
                    } else if let p = points.last {
                        PointMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0)).foregroundStyle(hue)
                            .symbolSize(points.count == 1 ? 80 : 30)
                            .annotation(position: .top, spacing: 6) {
                                // One day only: show its value instead of an empty grid (Stitch c8bea04c… “曲线点太少”).
                                if points.count == 1 {
                                    Text("\(Self.day(p.date)) \(Fmt.signed(Double(p.pnl)))")
                                        .font(.scaled(size: 12, weight: .semibold).monospacedDigit()).foregroundStyle(.white)
                                        .padding(.horizontal, 9).padding(.vertical, 5)
                                        .background(Color(white: 0.12), in: .capsule)
                                }
                            }
                    }
                }
                .chartXSelection(value: $picked)
                .chartXAxis {
                    AxisMarks(values: [points.first!.date, points.last!.date]) { value in
                        AxisValueLabel { Text(Self.day(value.as(String.self) ?? "")).font(.caption2.monospacedDigit()) }
                    }
                }
                .chartYAxis { AxisMarks(position: .trailing) { _ in AxisGridLine().foregroundStyle(Color.muted.opacity(0.12)); AxisValueLabel() } }
                .frame(height: 180)
                .onChange(of: picked) { _, now in if now != nil { Haptics.select() } }
                if points.count == 1 {
                    Label("只有 1 天记录，满 2 天后显示曲线", systemImage: "info.circle")
                        .font(.footnote).foregroundStyle(Color.muted)
                }
            }
        }
        .padding(16).glassCard()
    }

    // MARK: sides / coins

    private var directions: some View {
        HStack(spacing: 10) {
            ForEach(["long", "short"], id: \.self) { side in
                let row = data?.sides.first { $0.side == side }, hue = side == "long" ? Color.up : Color.down
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(side == "long" ? "做多" : "做空").font(.caption.weight(.bold)).foregroundStyle(hue)
                            .padding(.horizontal, 8).padding(.vertical, 3).background(hue.opacity(0.12), in: .capsule)
                        Spacer()
                        Text("\(row.map { "\($0.count)" } ?? "—") 笔").font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                    }
                    Text(Fmt.signed((row?.netPnl).flatMap(Double.init)) + " U").font(.headline.monospacedDigit()).foregroundStyle(tone(row?.netPnl))
                    Text("胜率 \(Fmt.fractionPercent(row?.winRate))%").font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard(cornerRadius: 20)
            }
        }
    }

    @ViewBuilder private var symbols: some View {
        if let rows = data?.symbols, !rows.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("币种").font(.headline).foregroundStyle(Color.ink).padding(.bottom, 4)
                ForEach(Array(rows.enumerated()), id: \.element.symbol) { index, row in
                    HStack(spacing: 10) {
                        CoinLogo(coin: Fmt.coin(row.symbol), size: 28)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(Fmt.coin(row.symbol)).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                            Text("\(row.count) 笔 · 胜率 \(Fmt.fractionPercent(row.winRate))%").font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                        }
                        Spacer()
                        Text(Fmt.signed(Double(row.netPnl))).font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(tone(row.netPnl))
                    }
                    .padding(.vertical, 6)
                    if index < rows.count - 1 { Divider().padding(.leading, 38) }
                }
            }
            .padding(16).glassCard()
        }
    }

    private var disciplineRow: some View {
        let state = account.state
        return HStack(spacing: 12) {
            Image(systemName: "checkmark.shield.fill").font(.system(size: 16, weight: .semibold)).foregroundStyle(Color.accentColor)
                .frame(width: 40, height: 40).background(Color.accentColor.opacity(0.12), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text("交易纪律").font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                Text(state.map { "今日 \($0.daily?.entries ?? 0) / \($0.discipline?.entriesLimit ?? $0.policy?.maxEntries ?? 3) 次" + ($0.blockers.isEmpty ? "" : " · 开仓受限") } ?? "—")
                    .font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(Color.muted.opacity(0.6))
        }
        .padding(14).glassCard()
    }

    static func time(_ seconds: Double) -> String {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "HH:mm"
        return f.string(from: Date(timeIntervalSince1970: seconds))
    }

    /// "2026-11-04" → "11-04".
    static func day(_ text: String) -> String { text.count >= 10 ? String(text.dropFirst(5).prefix(5)) : text }
}

struct Explainer: View {
    let title: String
    let paragraphs: [String]
    @State private var open = false
    var body: some View {
        DisclosureGroup(isExpanded: $open.animation(.snappy)) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(paragraphs, id: \.self) { Text($0) }
            }
            .font(.footnote).foregroundStyle(.secondary).padding(.top, 6)
        } label: {
            Text(title).font(.subheadline.weight(.semibold))
        }
        .onChange(of: open) { _, _ in Haptics.select() }
        .padding(16).glassCard()
    }
}
