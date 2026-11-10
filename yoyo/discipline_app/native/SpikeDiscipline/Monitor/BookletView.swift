import Charts
import SwiftUI

/// 策略小册 (2.7.0, Stitch native-2.7.0 booklet c85cd00c…): overview facts, rule steps, the saved
/// historical equity path with drawdown, yearly returns and the original research tables — the same
/// sources as the web booklet (research.json / <track>-history.json / breadth-ema20-data.json).
/// Long-press then drag on the curve to read a point; plain drags keep scrolling the page.
struct BookletView: View {
    enum Chapter: String, CaseIterable, Identifiable {
        case overview, rules, curve, research
        var id: String { rawValue }
        var title: String { switch self { case .overview: "概况"; case .rules: "规则"; case .curve: "资金曲线"; case .research: "原研究" } }
    }
    enum Span: String, CaseIterable, Hashable {
        case all, year, quarter
        var title: String { switch self { case .all: "全部"; case .year: "近1年"; case .quarter: "近3月" } }
        var ms: Double? { switch self { case .all: nil; case .year: 365 * 86_400_000; case .quarter: 92 * 86_400_000 } }
    }

    @Environment(\.dismiss) private var dismiss
    let key: String
    let label: String
    let connection: Connection
    var showsClose = true
    @State private var data: Booklet?
    @State private var error: String?
    @State private var chapter: Chapter = .overview
    @State private var span: Span = .all
    @State private var picked: Double?
    @State private var attempt = 0

    var body: some View {
        ScrollViewReader { reader in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12, pinnedViews: [.sectionHeaders]) {
                    Section {
                        if let data {
                            overview(data).id(Chapter.overview)
                            rules(data).id(Chapter.rules)
                            curve(data).id(Chapter.curve)
                            research(data).id(Chapter.research)
                            footer(data)
                        } else if let error {
                            VStack(spacing: 10) {
                                Label(error, systemImage: "exclamationmark.arrow.triangle.2.circlepath").font(.footnote).foregroundStyle(Color.amber)
                                Button("重试读取") { attempt += 1 }.buttonStyle(.pill(.soft))
                            }
                            .frame(maxWidth: .infinity).padding(20).glassCard()
                        } else {
                            HStack(spacing: 8) { ProgressView(); Text("正在读取策略小册").font(.footnote).foregroundStyle(Color.muted) }
                                .frame(maxWidth: .infinity).padding(24)
                        }
                    } header: {
                        chapterBar(reader)
                    }
                }
                .padding(.horizontal, 14).padding(.bottom, 28)
            }
        }
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle("\(label) · 策略小册")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { if showsClose { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) } }
        .task(id: attempt) {
            do {
                var query = [URLQueryItem(name: "track", value: key)]
                if key == "live-cloud-v17" { query.append(URLQueryItem(name: "view", value: "native-v2")) }
                let value: Booklet = try await API.get("/v1/monitor/booklet", query: query, connection: connection)
                withAnimation(.snappy) { data = value; error = nil }
            } catch is CancellationError {
            } catch { self.error = error.localizedDescription }
        }
    }

    // MARK: chapter bar

    private func chapterBar(_ reader: ScrollViewProxy) -> some View {
        HStack(spacing: 8) {
            ForEach(Chapter.allCases) { c in
                let on = c == chapter
                Button {
                    Haptics.select(); chapter = c
                    withAnimation(.snappy) { reader.scrollTo(c, anchor: .top) }
                } label: {
                    Text(c.title).font(.footnote.weight(on ? .semibold : .medium))
                        .foregroundStyle(on ? .white : Color.ink)
                        .padding(.horizontal, 13).frame(height: 32)
                        .background(on ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(Color.field), in: .capsule)
                }
                .buttonStyle(.plain)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 8)
        // Pinned over scrolling cards: frosted strip edge to edge so content never shows through.
        .background { Rectangle().fill(.regularMaterial).padding(.horizontal, -14).ignoresSafeArea(edges: .top) }
    }

    // MARK: overview / rules

    private func overview(_ d: Booklet) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(Color.accentColor).frame(width: 7, height: 7)
                Text(d.title.isEmpty ? d.label : d.title).font(.headline).foregroundStyle(Color.ink).lineLimit(2)
                Spacer(minLength: 4)
                Pill(text: d.paper ? "模拟运行" : "仅研究", color: d.paper ? .up : .secondary)
            }
            if let status = d.research?.status { Text(status).font(.caption).foregroundStyle(Color.muted) }
            if !d.facts.isEmpty {
                HStack(spacing: 8) {
                    ForEach(d.facts, id: \.label) { f in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(f.label).font(.caption2).foregroundStyle(Color.muted)
                            Text(f.value).font(.system(.subheadline, design: .monospaced).weight(.semibold)).foregroundStyle(Color.ink)
                                .lineLimit(1).minimumScaleFactor(0.7)
                            // Every tile keeps a unit line so the three tiles stay the same height.
                            Text(f.unit.isEmpty ? " " : f.unit).font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10).background(Color.field.opacity(0.8), in: .rect(cornerRadius: 14))
                    }
                }
            }
            ForEach(d.notes, id: \.self) { note in
                Label(note, systemImage: "info.circle").font(.caption2).foregroundStyle(Color.muted)
            }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    private func rules(_ d: Booklet) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("执行规则", systemImage: "checklist").font(.headline).foregroundStyle(Color.ink)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(d.flow.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .top, spacing: 10) {
                        VStack(spacing: 0) {
                            Text("\(index + 1)").font(.caption.weight(.bold)).foregroundStyle(Color.accentColor)
                                .frame(width: 22, height: 22).background(Color.accentColor.opacity(0.12), in: .circle)
                            if index < d.flow.count - 1 { Rectangle().fill(Color.primary.opacity(0.1)).frame(width: 1.5).frame(maxHeight: .infinity) }
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            Text(step.title).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                            Text(step.text).font(.footnote).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.bottom, index < d.flow.count - 1 ? 14 : 0)
                    }
                }
            }
            if d.flow.isEmpty { Text("规则说明见网页小册。").font(.footnote).foregroundStyle(Color.muted) }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    // MARK: equity curve

    private struct Point: Identifiable { let time: Double; let equity: Double; let dd: Double; var id: Double { time }; var date: Date { Date(timeIntervalSince1970: time / 1000) } }

    private func points(_ h: Booklet.History) -> [Point] {
        let all = h.points.compactMap { $0.count >= 3 ? Point(time: $0[0], equity: $0[1], dd: $0[2]) : nil }
        guard let span = span.ms, let last = all.last else { return all }
        let from = last.time - span
        let carried = all.last { $0.time < from }.map { Point(time: from, equity: $0.equity, dd: $0.dd) }
        return (carried.map { [$0] } ?? []) + all.filter { $0.time >= from }
    }

    @ViewBuilder private func curve(_ d: Booklet) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("历史资金曲线", systemImage: "chart.xyaxis.line").font(.headline).foregroundStyle(Color.ink)
            if let h = d.history {
                Text("\(StrategyFmt.utcDate(h.startMs)) — \(StrategyFmt.utcDate(h.endMs)) · UTC · 1000 USDT 起步")
                    .font(.system(.caption2, design: .monospaced)).foregroundStyle(Color.muted)
                metrics(h)
                SoftSegment(selection: $span, options: Span.allCases.map { ($0, $0.title) }, height: 32)
                equityChart(h)
                if let note = h.note { Text(note).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
                if h.drawdownBasis == "hourly_marked" {
                    Text("净值含浮盈亏，回撤按小时内可重建的最深阶段。")
                        .font(.caption2).foregroundStyle(Color.muted)
                    if let bound = h.summary.maxDrawdownBoundPct {
                        Text("小时高低价估值回撤上界 \(StrategyFmt.number(bound))%。")
                            .font(.caption2).foregroundStyle(Color.muted)
                    }
                } else {
                    Text(h.drawdownBasis == "marked" ? "曲线按平仓结算；上方最大回撤按每 5 分钟盯市。" : "曲线按平仓结算，不含持仓浮盈亏；回撤为相对历史高点的已实现回撤。")
                        .font(.caption2).foregroundStyle(Color.muted)
                }
                if !h.yearly.isEmpty { yearly(h) }
                if let sides = h.sides { ResearchTableView(table: ResearchTable(title: h.drawdownBasis == "marked" ? "分支表现" : "多空表现", headers: sides.headers, rows: sides.rows)) }
            } else {
                Label(d.historyError ?? "历史资金曲线暂时无法读取", systemImage: "chart.line.downtrend.xyaxis").font(.footnote).foregroundStyle(Color.amber)
            }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    private func metrics(_ h: Booklet.History) -> some View {
        let s = h.summary
        var items: [(String, String, Color)] = [
            (h.refilled ? "交易净盈亏" : "累计净收益", h.refilled ? StrategyFmt.signedMoney(s.pnl) + " U" : Fmt.signedPercent(s.returnPct, digits: 1), Fmt.tone(h.refilled ? s.pnl : s.returnPct).color),
            (h.drawdownBasis == "hourly_marked" ? "小时阶段回撤" : h.drawdownBasis == "marked" ? "盯市回撤" : "最大回撤", s.maxDrawdownPct.map { StrategyFmt.number($0) + "%" } ?? "—", .down),
            ("胜率", s.winPct.map { StrategyFmt.number($0) + "%" } ?? "—", .ink),
            ("已平仓", s.trades.map { "\(Int($0)) 笔" } ?? "—", .ink),
            ("盈利因子", s.noLosses == true ? "∞" : s.profitFactor.map { StrategyFmt.number($0, digits: 2) } ?? "—", .ink),
            ("期末资金", s.end.map { Fmt.money($0) + " U" } ?? "—", .ink)]
        if h.drawdownBasis == "hourly_marked", let bound = s.maxDrawdownBoundPct {
            items.insert(("回撤上界", StrategyFmt.number(bound) + "%", .down), at: 2)
        }
        return LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
            ForEach(items, id: \.0) { item in
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.0).font(.caption2).foregroundStyle(Color.muted)
                    Text(item.1).font(.system(.footnote, design: .monospaced).weight(.semibold)).foregroundStyle(item.2)
                        .lineLimit(1).minimumScaleFactor(0.6)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(9).background(Color.field.opacity(0.8), in: .rect(cornerRadius: 12))
            }
        }
    }

    private func equityChart(_ h: Booklet.History) -> some View {
        let rows = points(h)
        let lo = rows.map(\.equity).min() ?? 0, hi = rows.map(\.equity).max() ?? 1
        let pad = max((hi - lo) * 0.08, abs(hi) * 0.002, 0.01)
        let floor = lo - pad, worst = min(-1, rows.map(\.dd).min() ?? -1)
        let pick = picked.flatMap { t in rows.min { abs($0.time - t) < abs($1.time - t) } }
        let blue = Color(red: 0.145, green: 0.525, blue: 0.906)
        return VStack(alignment: .leading, spacing: 6) {
            ZStack(alignment: .top) {
                Chart {
                    ForEach(rows) { p in
                        AreaMark(x: .value("时间", p.date), yStart: .value("底", floor), yEnd: .value("资金", p.equity))
                            .interpolationMethod(.stepEnd)
                            .foregroundStyle(LinearGradient(colors: [blue.opacity(0.22), blue.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("时间", p.date), y: .value("资金", p.equity))
                            .interpolationMethod(.stepEnd).foregroundStyle(blue).lineStyle(StrokeStyle(lineWidth: 1.8))
                    }
                    if let pick { RuleMark(x: .value("选中", pick.date)).foregroundStyle(Color.primary.opacity(0.3)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3])) }
                }
                .chartYScale(domain: floor...(hi + pad))
                .chartXAxis(.hidden)
                .chartYAxis { AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { _ in AxisGridLine().foregroundStyle(Color.primary.opacity(0.06)); AxisValueLabel().font(.system(size: 9)) } }
                .chartOverlay { proxy in scrubber(proxy) }
                .frame(height: 180)
                if let pick {
                    Text("\(StrategyFmt.utcDate(pick.time)) · \(Fmt.money(pick.equity)) U · 回撤 \(StrategyFmt.number(pick.dd))%")
                        .font(.system(.caption2, design: .monospaced).weight(.semibold)).foregroundStyle(Color.ink)
                        .padding(.horizontal, 10).padding(.vertical, 5).frosted(Capsule(), veil: 0.85)
                        .transition(.opacity)
                }
            }
            Chart(rows) { p in
                AreaMark(x: .value("时间", p.date), y: .value("回撤", p.dd)).interpolationMethod(.stepEnd)
                    .foregroundStyle(Color.down.opacity(0.22))
                LineMark(x: .value("时间", p.date), y: .value("回撤", p.dd)).interpolationMethod(.stepEnd)
                    .foregroundStyle(Color.down.opacity(0.8)).lineStyle(StrokeStyle(lineWidth: 1))
            }
            .chartYScale(domain: (worst * 1.12)...0)
            .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { _ in AxisValueLabel(format: .dateTime.year().month(.twoDigits)).font(.system(size: 9)) } }
            .chartYAxis { AxisMarks(position: .trailing, values: .automatic(desiredCount: 2)) { v in AxisValueLabel { if let d = v.as(Double.self) { Text("\(Int(d))%").font(.system(size: 9)) } } } }
            .frame(height: 70)
            Text("长按曲线后拖动查看数值").font(.caption2).foregroundStyle(Color.muted)
        }
        .animation(.snappy, value: span)
    }

    private func scrubber(_ proxy: ChartProxy) -> some View {
        GeometryReader { geo in
            Rectangle().fill(.clear).contentShape(Rectangle())
                .gesture(LongPressGesture(minimumDuration: 0.2).sequenced(before: DragGesture(minimumDistance: 0))
                    .onChanged { value in
                        guard case .second(true, let drag?) = value, let frame = proxy.plotFrame else { return }
                        let x = drag.location.x - geo[frame].origin.x
                        if let date: Date = proxy.value(atX: x) {
                            if picked == nil { Haptics.select() }
                            picked = date.timeIntervalSince1970 * 1000
                        }
                    }
                    .onEnded { _ in withAnimation(.snappy) { picked = nil } })
        }
    }

    private func yearly(_ h: Booklet.History) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(h.refilled ? "年度交易净盈亏 · USDT" : "年度收益 · %").font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
            Chart(h.yearly) { y in
                BarMark(x: .value("年", y.period), y: .value("收益", y.value ?? 0), width: .ratio(0.55))
                    .foregroundStyle((y.value ?? 0) >= 0 ? Color.up.opacity(0.75) : Color.down.opacity(0.75))
                    .cornerRadius(3)
                    .annotation(position: (y.value ?? 0) >= 0 ? .top : .bottom, spacing: 2) {
                        Text(y.value.map { StrategyFmt.number($0, digits: abs($0) >= 100 ? 0 : 1) } ?? "—").font(.system(size: 8.5, weight: .semibold)).foregroundStyle(Color.muted)
                    }
            }
            .chartYAxis(.hidden)
            .chartXAxis { AxisMarks { _ in AxisValueLabel().font(.system(size: 9)) } }
            .frame(height: 120)
        }
        .padding(.top, 4)
    }

    // MARK: research

    @ViewBuilder private func research(_ d: Booklet) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("原研究", systemImage: "doc.text.magnifyingglass").font(.headline).foregroundStyle(Color.ink)
            if let r = d.research {
                if let note = r.note { Text(note).font(.footnote).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
                if let table = r.table { ResearchTableView(table: table) }
                if let capital = r.capital { ResearchTableView(table: ResearchTable(title: "原研究资金回放 · 多次顺序取中位数", headers: capital.headers, rows: capital.rows)) }
                if let detail = r.detail { Text(detail).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
                if let controls = r.controls { ResearchTableView(table: ResearchTable(title: "同条件随机入场对照", note: controls.note, headers: controls.headers, rows: controls.rows)) }
                ForEach(Array(r.sections.enumerated()), id: \.offset) { _, section in ResearchTableView(table: section) }
                if let cases = r.cases, !cases.isEmpty {
                    ForEach(Array(cases.enumerated()), id: \.offset) { _, item in
                        BookletTextCard(title: item.title, text: item.text)
                    }
                }
                if let risks = r.risk, !risks.isEmpty { BookletTextList(title: "风险与边界", items: risks) }
                if let sources = r.sources, !sources.isEmpty { BookletTextList(title: "数据来源与口径", items: sources) }
                if let m = r.methodology { Text(m).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
                if let p = r.periodNote { Text(p).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
            } else {
                Text("这个策略没有登记原研究表格。").font(.footnote).foregroundStyle(Color.muted)
            }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    @ViewBuilder private func footer(_ d: Booklet) -> some View {
        if d.backtest {
            NavigationLink(value: StrategyPage.backtest(key: d.key, label: d.label)) {
                Label("查看历史开单与 K 线", systemImage: "chart.bar.xaxis").frame(maxWidth: .infinity)
            }
            .buttonStyle(.pill(.tinted(.accentColor), height: 44))
        }
    }
}

private struct BookletTextCard: View {
    let title: String
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
            Text(text).font(.footnote).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(12)
        .background(Color.field.opacity(0.7), in: .rect(cornerRadius: 14))
    }
}

private struct BookletTextList: View {
    let title: String
    let items: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
            ForEach(Array(items.enumerated()), id: \.offset) { index, text in
                Text(text).font(.footnote).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true)
                if index < items.count - 1 { Divider() }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(12)
        .background(Color.field.opacity(0.7), in: .rect(cornerRadius: 14))
    }
}

/// Compact research table; scrolls sideways inside the booklet when columns overflow.
struct ResearchTableView: View {
    let table: ResearchTable
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let title = table.title, !title.isEmpty { Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink) }
            if let note = table.note { Text(note).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
            ScrollView(.horizontal) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 7) {
                    GridRow {
                        ForEach(Array(table.headers.enumerated()), id: \.offset) { _, h in
                            Text(h).font(.caption2.weight(.semibold)).foregroundStyle(Color.muted).lineLimit(1)
                        }
                    }
                    Divider()
                    ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { i, cell in
                                Text(cell.display).font(i == 0 ? .caption.weight(.medium) : .system(.caption, design: .monospaced))
                                    .foregroundStyle(Color.ink).lineLimit(1)
                            }
                        }
                    }
                }
                .padding(10)
            }
            .scrollIndicators(.hidden)
            .background(Color.field.opacity(0.7), in: .rect(cornerRadius: 12))
        }
    }
}
