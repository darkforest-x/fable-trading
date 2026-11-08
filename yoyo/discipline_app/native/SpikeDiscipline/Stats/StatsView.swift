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

    private var key: String { "\(session.connection?.accountId ?? "")|\(session.connection?.mode ?? "")|\(days)" }

    var body: some View {
        ZStack {
            AmbientBackground()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    PageHeader(title: "统计") {
                        RoundIcon(systemName: "gearshape") { showSettings = true }.accessibilityLabel("设置")
                    }
                    if session.connection == nil { ConnectPrompt() }
                    hero
                    SoftSegment(selection: $days, options: [(7, "7天"), (30, "30天"), (0, "全部")])
                    tiles
                    curve
                    directions
                    symbols
                    Button { Haptics.open(); discipline = true } label: { disciplineRow }.buttonStyle(CardPressStyle())
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 24)
            }
            .scrollPosition($scroll)
            .refreshable { await load(); Haptics.success() }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .scrollEdgeEffectHidden(true, for: [.top, .bottom])
        }
        .sheet(isPresented: $discipline) {
            NavigationStack { DisciplineView() }.presentationDragIndicator(.visible)
        }
        .onChange(of: days) { _, _ in Haptics.select(); picked = nil; restore() }
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
        guard let connection = session.connection,
              let saved = LaunchCache.read(Statistics.self, "stats-\(days)", scope: account.scope),
              saved.periodDays == days, saved.accountId == connection.accountId, saved.mode == connection.mode else { return }
        data = saved
    }

    private func load() async {
        guard let connection = session.connection else { return }
        let requested = key
        loading = true
        defer { loading = false }
        do {
            let next = try await account.statistics(days: days)
            guard next.mode == connection.mode, next.periodDays == days, next.accountId == connection.accountId else { throw APIError(status: 0, message: "统计响应与当前账户不匹配") }
            guard requested == key else { return }
            withAnimation(.snappy) { data = next }
            error = nil
            LaunchCache.write(next, "stats-\(days)", scope: account.scope)
        } catch is CancellationError {
        } catch {
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
                Text("净值").font(.subheadline.weight(.medium)).foregroundStyle(Color.muted)
                Spacer()
                // Syncing after a launch from the cached snapshot (Stitch c8bea04c… “同步最新中”).
                if stale && session.connection != nil && NetworkWatch.shared.online {
                    ProgressView().controlSize(.mini).accessibilityLabel("同步中")
                }
                tag(stale ? (updatedAt > 0 ? "上次 \(Self.time(updatedAt))" : loading ? "读取中" : "待连接") : live ? "实盘 · 仅 App 交易" : "模拟",
                    tint: stale ? .amber : live ? .down : .up)
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(Fmt.amount(equity)).font(.scaled(size: 38, weight: .bold, design: .rounded).monospacedDigit()).foregroundStyle(Color.ink)
                    .contentTransition(.numericText(value: Double(equity ?? "0") ?? 0)).animation(.snappy, value: equity)
                Text("USDT").font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted)
            }
            HStack(spacing: 6) {
                Text("累计 \(Fmt.signed(a?.netPnl.flatMap(Double.init))) USDT").font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(tone(a?.netPnl))
                if let r = a?.returnFraction {
                    Text("\((Double(r) ?? 0) > 0 ? "+" : "")\(Fmt.fractionPercent(r, 2))%").font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(tone(r)).padding(.horizontal, 7).padding(.vertical, 3).background(tone(r).opacity(0.1), in: .capsule)
                }
            }
            Divider().padding(.vertical, 2)
            HStack(spacing: 0) {
                heroMetric("已实现", a?.realizedPnl)
                Divider().frame(height: 32).padding(.horizontal, 14)
                heroMetric("浮动", a?.unrealizedPnl)
            }
        }
        .padding(18).glassCard(cornerRadius: 26)
    }

    private func heroMetric(_ title: String, _ value: String?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(Color.muted)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(Fmt.signed(value.flatMap(Double.init))).font(.headline.monospacedDigit()).foregroundStyle(tone(value))
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
        return LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            tile("list.bullet", .accentColor, "已平仓", s.map { "\($0.count)" } ?? "—", "笔")
            tile("scope", .up, "胜率", Fmt.fractionPercent(s?.winRate), "%")
            tile("scalemass", .indigo, "盈亏比", Fmt.amount(s?.payoffRatio), nil)
            tile("sum", .amber, "平均 R", Fmt.signed(s?.averageNetR.flatMap(Double.init)), nil)
        }
    }

    private func tile(_ icon: String, _ tint: Color, _ title: String, _ value: String, _ unit: String?) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 16, weight: .semibold)).foregroundStyle(tint)
                .frame(width: 40, height: 40).background(tint.opacity(0.12), in: .circle)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption).foregroundStyle(Color.muted)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text(value).font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(Color.ink).contentTransition(.numericText())
                        .lineLimit(1).minimumScaleFactor(0.6)
                    if let unit { Text(unit).font(.caption.weight(.medium)).foregroundStyle(Color.muted).fixedSize() }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(12).glassCard(cornerRadius: 20)
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
