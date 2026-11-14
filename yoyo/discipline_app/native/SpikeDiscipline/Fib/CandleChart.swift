import SwiftUI

/// Native candle chart replicating the workbench / web-mobile wallpaper chart (yoyo/monitor/static:
/// range-chart.js, chart-tools.js, chart-style.js six lines, monday-fib-chart.js + monday-chart-appearance.js
/// levels, cloud-signal-chart.js cloud). Owner 2026-11-08: full screen, time axis visible, tap shows the
/// bar's time, web-mobile level lines, previous weeks' ranges, custom candle colours.
/// Gestures: pan in both axes with momentum, pinch to zoom time, drag the price axis to scale price,
/// tap for the crosshair (drag moves it, tap again hides it), double-tap to reset and follow.
/// Display only — never an execution price or risk input.
///
/// Smoothness (owner 2026-11-09 “拖动起来还是有卡顿的感觉，如何做到和tradingview一样的流畅”, tier 1):
/// a fling keeps gliding with UIScrollView-like exponential decay, driven frame by frame by a
/// TimelineView (a Canvas cannot interpolate `withAnimation`, so the old release animation jumped to its
/// end in one frame); candles and volume are filled as one pixel-snapped path per colour instead of
/// one stroke + fill per bar; axis/label strings, previous-week touch markers and indicator series are
/// cached instead of rebuilt every frame.
struct ChartLine: Equatable, Identifiable {
    let price: Double
    let title: String
    let color: Color
    var dashed = false
    var startTime: Double? = nil
    var endTime: Double? = nil
    var lineWidth: CGFloat = 1.4
    /// Reference overlays can be drawn without changing the candle auto-scale.
    var fitsScale = true
    var id: String { title }
}

enum ChartPalette {
    static func hex(_ v: UInt32, _ a: Double = 1) -> Color {
        Color(red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255, blue: Double(v & 0xff) / 255).opacity(a)
    }
    static let up = hex(0x169873)
    static let down = hex(0xD7355D)
    static let accent = hex(0x0A84FF)
    static let reference = hex(0x718096)
    // Workbench colours kept for markers elsewhere (touch dots, BTC card).
    static let upper = hex(0xC93248)
    static let lower = hex(0x087F69)
    static let monday = hex(0x528CC1)
    static let innerUpper = hex(0x408DDD)
    static let innerLower = hex(0x8A6CBD)
    // Web-mobile level lines (monday-chart-appearance.js THEME_COLORS).
    static func fibLine(dark: Bool) -> Color { dark ? hex(0xE3E7EC) : hex(0x17191D) }
    static let mondayLine = hex(0x9399A2)
    static let averages: [(kind: String, period: Int, color: Color)] = [
        ("sma", 20, hex(0x5C65AA)), ("sma", 60, hex(0x087F69)), ("sma", 120, hex(0x586D81)),
        ("ema", 20, hex(0x8955B6)), ("ema", 60, hex(0xA87512)), ("ema", 120, hex(0xBD3A52)),
    ]
    static func cloud(_ bullish: Bool, dark: Bool) -> Color {
        bullish ? (dark ? Color(red: 48 / 255, green: 209 / 255, blue: 158 / 255).opacity(0.32) : Color(red: 16 / 255, green: 185 / 255, blue: 129 / 255).opacity(0.26))
                : (dark ? Color(red: 1, green: 99 / 255, blue: 125 / 255).opacity(0.30) : Color(red: 244 / 255, green: 63 / 255, blue: 94 / 255).opacity(0.23))
    }
}

/// A 1.618 touch drawn on its bar (owner 2026-11-08 item 2, Stitch 5640c0c0…): a dot on the level
/// with the touch's own score above (upper) or below (lower). Previous weeks' first touches are found
/// from the bars, drawn faded and unscored. Display only.
struct ChartMarker: Equatable, Identifiable {
    let time: Double      // epoch seconds of the touch
    let price: Double     // the touched 1.618 level
    let edge: Edge
    var score: Double? = nil
    var faded = false
    var id: String { "\(edge.rawValue)-\(Int(time))" }
}

/// A frozen opening or close fill placed at its recorded time and price. Cloud history can contain many real
/// trim fills; each marker remains tappable so its exact hour, price and original-size share are clear.
struct ExecutionMarker: Equatable, Identifiable {
    let time: Double      // epoch seconds
    let price: Double
    let label: String
    let action: String
    let quantity: Double
    let share: Double
    let phase: String
    var endTime: Double? = nil   // exclusive right edge when the fill is known only to its native hour
    var id: String { "\(action)-\(Int(time))-\(price)" }
    var color: Color {
        switch action {
        case "entry": ChartPalette.up
        case "trim": ChartPalette.hex(0xD97706)
        default: ChartPalette.down
        }
    }
}

/// Initial viewport fit for a historical cloud trade. Short holds show 25–42 periods; longer holds
/// keep both endpoints in view and add eight periods of context on either side when the screen allows.
struct ChartReplayViewportFit {
    let spacing: CGFloat
    let offset: CGFloat
    let visiblePeriods: CGFloat

    static func make(plotWidth: CGFloat, periodSeconds: Double, firstBarTime: Double, lastBarTime: Double,
                     focus: ClosedRange<Double>, rightPad: CGFloat) -> ChartReplayViewportFit? {
        guard plotWidth > 0, periodSeconds > 0, firstBarTime.isFinite, lastBarTime.isFinite,
              focus.lowerBound.isFinite, focus.upperBound.isFinite, focus.upperBound >= focus.lowerBound else { return nil }
        let entry = (focus.lowerBound - firstBarTime) / periodSeconds
        let exit = (focus.upperBound - firstBarTime) / periodSeconds
        let duration = max(1, exit - entry)
        let targetVisible = max(25, ceil(duration) + 16)
        let spacing = min(32, max(0.25, plotWidth / CGFloat(targetVisible)))
        let contentEnd = max((lastBarTime - firstBarTime) / periodSeconds, exit)
        let targetRight = exit + 3
        let offset = CGFloat(contentEnd) + rightPad - CGFloat(targetRight)
        return ChartReplayViewportFit(spacing: spacing, offset: offset, visiblePeriods: plotWidth / spacing)
    }
}

/// Candle indices and OHLC bounds for the part of the series occupying the current horizontal viewport.
/// The initial price scale uses these bounds; reference annotations can be added separately by preference.
struct ChartVisibleBarWindow {
    static func indices(count: Int, left: CGFloat, right: CGFloat) -> ClosedRange<Int>? {
        guard count > 0, left.isFinite, right.isFinite, right >= left else { return nil }
        let first = max(0, Int(floor(left))), last = min(count - 1, Int(ceil(right)))
        return first <= last ? first...last : nil
    }

    static func ohlc(_ bars: [Candle], indices: ClosedRange<Int>?) -> ChartOHLCBounds? {
        guard let indices, indices.lowerBound >= 0, indices.upperBound < bars.count else { return nil }
        var low = Double.infinity, high = -Double.infinity
        for bar in bars[indices] where bar.low.isFinite && bar.high.isFinite {
            low = min(low, bar.low); high = max(high, bar.high)
        }
        guard low.isFinite, high.isFinite, low <= high else { return nil }
        return ChartOHLCBounds(low: low, high: high)
    }
}

struct ChartOHLCBounds: Equatable {
    let low: Double
    let high: Double
}

/// A drawable price annotation with the time window where its value is active.
struct ChartPriceLevel {
    let price: Double
    var startTime: Double? = nil
    var endTime: Double? = nil

    func overlaps(start: Double, end: Double) -> Bool {
        guard price.isFinite, price > 0, start.isFinite, end.isFinite, end > start else { return false }
        return (endTime ?? .infinity) > start && (startTime ?? -.infinity) < end
    }
}

/// Displaying a price never changes a live chart’s scale. Only explicit replay callers can fit levels.
enum ChartReferenceScalePolicy {
    static func includesReferences(callerAllows: Bool, explicitReplay: Bool) -> Bool {
        callerAllows && explicitReplay
    }
}

struct ChartEdgeLevel: Hashable {
    let title: String
    let price: Double
}

struct ChartEdgeCatalog {
    private(set) var above: [ChartEdgeLevel] = []
    private(set) var below: [ChartEdgeLevel] = []

    init(_ levels: [ChartEdgeLevel], visible: ClosedRange<Double>) {
        for level in Set(levels) where level.price.isFinite && level.price > 0 {
            if level.price > visible.upperBound { above.append(level) }
            if level.price < visible.lowerBound { below.append(level) }
        }
        above.sort { ($0.price, $0.title) < ($1.price, $1.title) }
        below.sort { $0.price != $1.price ? $0.price > $1.price : $0.title < $1.title }
    }
}

/// The final y-domain after visible OHLC, optional in-window annotations, plot padding and manual transforms.
struct ChartPriceDomain: Equatable {
    let low: Double
    let high: Double
}

/// Allocation-free y-scale accumulator used by the renderer on each frame.
struct ChartPriceScale {
    private var low: Double
    private var high: Double

    init(ohlc: ChartOHLCBounds?) {
        low = ohlc?.low ?? .infinity
        high = ohlc?.high ?? -.infinity
    }

    mutating func include(_ level: ChartPriceLevel, visibleStart: Double?, visibleEnd: Double?) {
        guard let visibleStart, let visibleEnd, level.overlaps(start: visibleStart, end: visibleEnd) else { return }
        include(level.price)
    }

    mutating func include(_ price: Double) {
        guard price.isFinite, price > 0 else { return }
        low = min(low, price); high = max(high, price)
    }

    func domain(topFraction: Double, bottomFraction: Double, yZoom: Double = 1, yShift: Double = 0) -> ChartPriceDomain {
        var lo = low, hi = high
        if !lo.isFinite || !hi.isFinite { lo = 0; hi = 1 }
        if hi - lo < hi * 1e-6 { let pad = max(hi * 0.001, 1e-9); lo -= pad; hi += pad }
        let top = max(0, topFraction), bottom = max(0, bottomFraction)
        let span = hi - lo
        let usable = max(0.2, 1 - top - bottom)
        let full = span / usable
        let l0 = lo - full * bottom, h0 = hi + full * top
        let zoom = yZoom.isFinite && yZoom > 0 ? yZoom : 1
        let shift = yShift.isFinite ? yShift : 0
        let half = (h0 - l0) / 2 * zoom, center = (l0 + h0) / 2 + shift * (h0 - l0)
        return ChartPriceDomain(low: center - half, high: center + half)
    }
}

struct ChartPlotReserve {
    static func bottomFraction(wallpaper: Bool, hasBottomOverlay: Bool, bottomReserve: Double, plotHeight: Double) -> Double {
        guard wallpaper else { return 0.07 }
        let existing = min(0.6, bottomReserve + 0.04)
        guard !hasBottomOverlay else { return existing }
        // Volume occupies the bottom 8% above the time labels. Leave four points above its tallest bar.
        let volumeClearance = 0.08 + 28 / max(plotHeight, 1)
        return min(0.6, max(existing, volumeClearance))
    }
}

/// A sparse source-bar time axis. Bar indices remain available to drawing code, but their horizontal
/// coordinates follow elapsed periods so missing source candles stay visible as empty time slots.
struct ChartTimeAxis {
    let times: [Double]
    let periodSeconds: Double

    private var origin: Double { times.first ?? 0 }

    func position(atTime time: Double) -> CGFloat {
        guard periodSeconds > 0, time.isFinite else { return 0 }
        return CGFloat((time - origin) / periodSeconds)
    }

    func position(forFractionalBarIndex index: CGFloat) -> CGFloat {
        guard let first = times.first, let last = times.last, !times.isEmpty, periodSeconds > 0 else { return index }
        let time: Double
        if index <= 0 {
            time = first + Double(index) * periodSeconds
        } else if index >= CGFloat(times.count - 1) {
            time = last + Double(index - CGFloat(times.count - 1)) * periodSeconds
        } else {
            let lower = Int(floor(index)), fraction = Double(index - CGFloat(lower))
            time = times[lower] + (times[lower + 1] - times[lower]) * fraction
        }
        return position(atTime: time)
    }

    /// Fractional source-array index at a time. `position(forFractionalBarIndex:)` round-trips through
    /// sparse gaps without snapping a time to either neighboring candle.
    func barIndex(atTime time: Double) -> CGFloat {
        guard let first = times.first, let last = times.last, !times.isEmpty, periodSeconds > 0 else { return 0 }
        if time <= first { return CGFloat((time - first) / periodSeconds) }
        if time >= last { return CGFloat(times.count - 1) + CGFloat((time - last) / periodSeconds) }
        var low = 0, high = times.count - 1
        while low + 1 < high {
            let middle = (low + high) / 2
            if times[middle] <= time { low = middle } else { high = middle }
        }
        return CGFloat(low) + CGFloat((time - times[low]) / max(times[high] - times[low], 1))
    }

    func nearestBarIndex(atPosition position: CGFloat) -> Int? {
        guard !times.isEmpty, periodSeconds > 0 else { return nil }
        let time = origin + Double(position) * periodSeconds
        var low = 0, high = times.count
        while low < high {
            let middle = (low + high) / 2
            if times[middle] < time { low = middle + 1 } else { high = middle }
        }
        if low == 0 { return 0 }
        if low == times.count { return times.count - 1 }
        return time - times[low - 1] <= times[low] - time ? low - 1 : low
    }

    func visibleBarRange(left: CGFloat, right: CGFloat) -> ClosedRange<Int>? {
        guard !times.isEmpty, periodSeconds > 0, left.isFinite, right.isFinite, right >= left else { return nil }
        let lowerTime = origin + Double(left - 0.5) * periodSeconds
        let upperTime = origin + Double(right + 0.5) * periodSeconds
        var low = 0, high = times.count
        while low < high {
            let middle = (low + high) / 2
            if times[middle] < lowerTime { low = middle + 1 } else { high = middle }
        }
        let first = low
        low = 0; high = times.count
        while low < high {
            let middle = (low + high) / 2
            if times[middle] <= upperTime { low = middle + 1 } else { high = middle }
        }
        let last = low - 1
        guard first < times.count, last >= first else { return nil }
        return first...last
    }
}

private struct ExecutionTagPlacement {
    let marker: ExecutionMarker
    let anchor: CGPoint
    let rect: CGRect
    let title: String
    let color: Color
}

struct CandleChart: View {
    let bars: [Candle]
    let generation: Int
    let period: CandlePeriod
    let decimals: Int
    var range: FibRange? = nil
    var history: [FibRange] = []
    var innerLines = false
    var averages = false
    var cloud = false
    var lines: [ChartLine] = []
    var markers: [ChartMarker] = []
    var executionMarkers: [ExecutionMarker] = []
    var replayEntryTitle: String? = nil
    /// Optional trade span to fit on first appearance. Historical replay sets this; live charts stay at the edge.
    var focusRange: ClosedRange<Double>? = nil
    /// Monthly event charts use an elapsed-time axis even when there is no event focus (symbol-only view).
    var timestampFocus = false
    /// Exact candle opening times flagged by the service (supports candles that overlap the first-week boundary).
    var highlightedBarTimes: Set<Double> = []
    /// Fallback for mock responses without `first_week`; applies only to the current anchor interval.
    var highlightRange: Range<Double>? = nil
    var highlightColor: Color? = nil
    /// Highlight the event's actual candle when the service resolved one. Nil means the bar is unknown.
    var focusBarTime: Double? = nil
    /// Caller-level exclusion for explicit replay fits; live charts always use visible candles.
    var fitReferenceLevels = true
    /// Also mark the first 1.618 touch of each previous week in `history` (found from the bars).
    var markHistory = false
    var colors: ChartColors = .classic
    var watermark: String? = nil
    var followToken = 0
    /// Wallpaper: full-bleed canvas, no grid (range-chart.js wallpaper view).
    var wallpaper = false
    /// Top inset (floating controls above the plot) and the height of floating UI over the bottom.
    var topInset: CGFloat = 0
    var bottomOverlay: CGFloat = 0
    var onNeedOlder: () -> Void = {}

    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.displayScale) private var displayScale
    @State private var spacing: CGFloat = 7
    /// Committed horizontal offset in bars (0 = live edge). While `glide` runs, the drawn offset comes from it.
    @State private var offset: CGFloat = 0
    @State private var glide: Glide?
    @State private var yShift: Double = 0
    @State private var yZoom: Double = 1
    @State private var panStart: (offset: CGFloat, shift: Double)?
    @State private var gestureEdges: ChartEdgeCatalog?
    @State private var axisStart: Double?
    @State private var zoomStart: CGFloat?
    @State private var cross: CGPoint?
    @State private var crossHaptic = -1
    @State private var memo = IndicatorMemo()
    @State private var picked: String?
    @State private var pickedExecutions: [ExecutionMarker] = []
    @State private var viewportInteracted = false
    @State private var resetGeneration = Int.min
    @AppStorage("touch.highScore") private var highScore = 0.5
    @AppStorage("chart.fitReferencePrices") private var showOffscreenPrices = true

    private let axisWidth: CGFloat = 58
    private let timeHeight: CGFloat = 20
    private let rightPad: CGFloat = 5

    /// Historical cloud replays have a bounded focus range; live and non-cloud charts keep their
    /// existing index-based viewport and spacing.
    private var timestampReplay: Bool { timestampFocus || (cloud && focusRange != nil) }
    private var fitReferencePricesInDomain: Bool {
        ChartReferenceScalePolicy.includesReferences(callerAllows: fitReferenceLevels,
                                                     explicitReplay: timestampFocus || focusRange != nil)
    }

    var body: some View {
        // Paused unless a fling is gliding; then it redraws once per display frame.
        TimelineView(.animation(minimumInterval: nil, paused: glide == nil)) { timeline in
        GeometryReader { proxy in
            let size = proxy.size
            let plot = CGRect(x: 0, y: topInset, width: max(1, size.width - axisWidth),
                              height: max(1, size.height - topInset - (wallpaper ? 0 : timeHeight)))
            // The time axis sits just above the floating bottom UI in wallpaper mode.
            let axisY = wallpaper ? plot.maxY - bottomOverlay - 12 : plot.maxY + timeHeight / 2 + 1
            let layout = Layout(bars: bars, spacing: spacing, offset: glide?.offset(at: timeline.date) ?? offset, plot: plot, rightPad: rightPad,
                                period: period, range: range, lines: lines, wallpaper: wallpaper,
                                bottomReserve: wallpaper ? Double((bottomOverlay + 34) / max(plot.height, 1)) : 0,
                                hasBottomOverlay: bottomOverlay > 0, yShift: yShift, yZoom: yZoom,
                                timestampAxis: timestampReplay, focusRange: focusRange, executions: executionMarkers,
                                fitReferenceLevels: fitReferencePricesInDomain)
            ZStack(alignment: .topLeading) {
                if wallpaper { (scheme == .dark ? ChartPalette.hex(0x0B0E14) : Color.white) }
                Canvas(rendersAsynchronously: false) { context, _ in
                    #if DEBUG
                    let started = CACurrentMediaTime()
                    #endif
                    draw(&context, layout: layout, axisY: axisY)
                    #if DEBUG
                    ChartFrameProbe.shared.drawn(CACurrentMediaTime() - started)
                    #endif
                }
                if let picked, let hit = executionPoints(layout).first(where: { $0.marker.id == picked }) {
                    ExecutionCallout(markers: pickedExecutions.isEmpty ? [hit.marker] : pickedExecutions)
                        .position(x: min(max(hit.point.x, plot.minX + 112), plot.maxX - 112),
                                  y: hit.point.y < plot.midY ? hit.point.y + 94 : hit.point.y - 94)
                        .transition(.scale(scale: 0.9).combined(with: .opacity))
                } else if let picked, let hit = markerPoints(layout).first(where: { $0.marker.id == picked }) {
                    MarkerCallout(marker: hit.marker, price: groupedPrice(hit.marker.price))
                        .position(x: min(max(hit.point.x, plot.minX + 112), plot.maxX - 112),
                                  y: hit.marker.edge == .upper ? hit.point.y + 72 : hit.point.y - 72)
                        .transition(.scale(scale: 0.9).combined(with: .opacity))
                }
                if let cross, !bars.isEmpty {
                    readout(layout.bars[layout.index(atX: cross.x)])
                        // Wallpaper charts have floating controls on top, so the readout sits just above the time pill.
                        .padding(.leading, 10).padding(.top, wallpaper ? max(plot.minY + 6, axisY - 44) : plot.minY + 6)
                        .allowsHitTesting(false)
                        .transition(.opacity)
                }
            }
            .contentShape(.rect)
            .highPriorityGesture(pan(layout))
            .simultaneousGesture(zoom)
            .simultaneousGesture(SpatialTapGesture(count: 2).onEnded { _ in reset(in: plot) })
            .simultaneousGesture(SpatialTapGesture(count: 1).onEnded { value in tap(value.location, layout: layout) })
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(watermark ?? "") \(period.label) K线")
            .overlay(alignment: .topLeading) {
                if showOffscreenPrices, !timestampFocus, focusRange == nil, layout.firstVisible <= layout.lastVisible {
                    let floor = wallpaper ? axisY - 12 : plot.maxY - 4
                    if floor > plot.minY + 80 {
                        let moving = panStart != nil || zoomStart != nil || axisStart != nil || cross != nil || glide != nil
                        let edges = (moving ? gestureEdges : nil) ?? edgeCatalog(layout, floor: floor)
                        VStack(alignment: .leading, spacing: 0) {
                            if !edges.above.isEmpty { edgeMenu(edges.above, above: true) }
                            Spacer(minLength: 0)
                            if !edges.below.isEmpty { edgeMenu(edges.below, above: false) }
                        }
                        // Keep native menus mounted across touch-down and coast; only the resting
                        // viewport exposes actionable reference prices.
                        .opacity(moving ? 0 : 1)
                        .allowsHitTesting(!moving)
                        .accessibilityHidden(moving)
                        .frame(height: floor - plot.minY)
                        .padding(.top, plot.minY)
                        .padding(.leading, 5)
                    }
                }
            }
            .onChange(of: layout.firstVisible) { _, first in
                if first < 12 && bars.count >= 55 { onNeedOlder() }
            }
            .onAppear { resetForGeneration(in: plot) }
            .onChange(of: generation) { _, _ in resetForGeneration(in: plot) }
            .onChange(of: plot.size) { old, new in
                guard old != new, resetGeneration == generation, !viewportInteracted else { return }
                resetForGeneration(in: plot)
            }
        }
        }
        // Commit the glide's resting offset when it has decayed (or hit the edge); a new touch cancels it first.
        .task(id: glide?.id) {
            guard let current = glide else { return }
            try? await Task.sleep(for: .seconds(current.duration))
            guard !Task.isCancelled, let finished = glide, finished.id == current.id else { return }
            offset = finished.final
            glide = nil
            #if DEBUG
            ChartFrameProbe.shared.end()
            #endif
        }
        .onChange(of: followToken) { _, _ in reset() }
        .onChange(of: bars.last?.time) { old, new in
            guard !timestampReplay, resetGeneration == generation else { return }
            guard let old, let new, new > old else { return }
            let added = CGFloat((new - old) / period.seconds)
            // Away from the live edge, keep the same bars in view when a new bar opens (also mid-glide).
            if var moving = glide {
                if moving.offset(at: .now) > 0.5 { moving.origin += added; moving.upper += added; glide = moving }
            } else if offset > 0.5 {
                offset += added
            }
        }

    }

    private func edgeCatalog(_ layout: Layout, floor: CGFloat) -> ChartEdgeCatalog {
        let start = bars[layout.firstVisible].time
        let end = bars[layout.lastVisible].time + period.seconds
        var levels = lines.compactMap { line -> ChartEdgeLevel? in
            let level = ChartPriceLevel(price: line.price, startTime: line.startTime, endTime: line.endTime)
            guard level.overlaps(start: start, end: end) else { return nil }
            return ChartEdgeLevel(title: line.title, price: line.price)
        }
        func append(_ range: FibRange, inner: Bool, prefix: String) {
            guard range.end > start && range.start < end else { return }
            levels += range.levels(inner: inner).map { ChartEdgeLevel(title: prefix + $0.title, price: $0.price) }
        }
        if let range { append(range, inner: innerLines, prefix: "") }
        for past in history where past != range && past.end > start && past.start < end {
            let day = Date(timeIntervalSince1970: past.start).formatted(.dateTime.month(.twoDigits).day(.twoDigits))
            append(past, inner: false, prefix: day + " · ")
        }
        return ChartEdgeCatalog(levels, visible: layout.price(atY: floor)...layout.high)
    }

    private func edgeMenu(_ levels: [ChartEdgeLevel], above: Bool) -> some View {
        let title = above ? "上方参考价" : "下方参考价"
        return Menu {
            Section(title) {
                ForEach(levels, id: \.self) { level in
                    Text("\(level.title)　\(format(level.price))")
                }
            }
        } label: {
            HStack(spacing: 3) {
                Image(systemName: above ? "arrow.up" : "arrow.down")
                Text("\(levels.count) · \(format(levels[0].price))").monospacedDigit()
            }
            .font(.caption2.weight(.semibold)).foregroundStyle(Color.muted)
            .padding(.horizontal, 7).padding(.vertical, 3).background(Color.field, in: .capsule)
            .frame(minWidth: 44, minHeight: 32).contentShape(.rect)
        }
        .menuStyle(.button).buttonStyle(.plain)
        .accessibilityLabel("\(title)，\(levels.count) 个")
        .accessibilityHint("查看价格明细")
    }

    private func reset() {
        Haptics.tap()
        glide = nil
        withAnimation(.snappy(duration: 0.35)) { offset = 0; yShift = 0; yZoom = 1; cross = nil; picked = nil; pickedExecutions = [] }
    }

    private func reset(in plot: CGRect) {
        guard let focusRange else { reset(); return }
        Haptics.tap()
        glide = nil
        withAnimation(.snappy(duration: 0.35)) {
            viewportInteracted = false
            spacing = 7; yShift = 0; yZoom = 1; cross = nil; picked = nil; pickedExecutions = []
            applyFocus(focusRange, in: plot)
        }
    }

    private func resetForGeneration(in plot: CGRect) {
        resetGeneration = generation
        viewportInteracted = false
        glide = nil; offset = 0; spacing = 7; yShift = 0; yZoom = 1; cross = nil; picked = nil; pickedExecutions = []
        if let focusRange { applyFocus(focusRange, in: plot) }
    }

    /// Fit historical cloud holds to the final plot width; normal charts retain their original three-bar fit.
    private func applyFocus(_ range: ClosedRange<Double>, in plot: CGRect) {
        if timestampReplay {
            // GeometryReader can report a transient one-point width while the card is entering layout.
            // Leave the default viewport until the size-change handler sees the final plot width.
            guard plot.width >= 120, let first = bars.first?.time, let last = bars.last?.time,
                  let fit = ChartReplayViewportFit.make(plotWidth: plot.width, periodSeconds: period.seconds,
                                                        firstBarTime: first, lastBarTime: last,
                                                        focus: range, rightPad: rightPad) else { return }
            spacing = fit.spacing
            offset = fit.offset
            return
        }
        guard plot.width > 0, let firstTrade = barIndex(range.lowerBound), let lastTrade = barIndex(range.upperBound) else { return }
        let first = max(0, firstTrade - 3), last = min(bars.count - 1, lastTrade + 3)
        let visibleBars = CGFloat(max(1, last - first + 1))
        spacing = min(7, max(1.6, plot.width / visibleBars))
        let rightIndex = CGFloat(last) + 3
        offset = CGFloat(bars.count - 1) + rightPad - rightIndex
        let visible = plot.width / spacing
        offset = min(max(offset, -visible * 0.6), max(0, CGFloat(bars.count) - visible * 0.3))
    }

    /// A touch stops a gliding chart where it is (UIScrollView behaviour). Returns true if it was gliding.
    @discardableResult private func stopGlide() -> Bool {
        guard let current = glide else { return false }
        offset = current.offset(at: .now)
        glide = nil
        #if DEBUG
        ChartFrameProbe.shared.end()
        #endif
        return true
    }

    // MARK: gestures

    private func tap(_ point: CGPoint, layout: Layout) {
        guard point.x <= layout.plot.maxX else { return }
        // A tap on a gliding chart only stops it (no crosshair), like a scroll view.
        if stopGlide() { return }
        if picked != nil { withAnimation(.easeOut(duration: 0.15)) { picked = nil; pickedExecutions = [] }; return }
        if cross != nil { withAnimation(.easeOut(duration: 0.15)) { cross = nil }; return }
        let executionHits = executionPoints(layout).filter { hypot($0.point.x - point.x, $0.point.y - point.y) < 22 }
        let tagHit = executionTagPlacements(layout).first { $0.rect.insetBy(dx: -8, dy: -8).contains(point) }
        let pointHit = executionHits.min(by: {
            hypot($0.point.x - point.x, $0.point.y - point.y) < hypot($1.point.x - point.x, $1.point.y - point.y)
        })
        if let hit = tagHit.map({ (marker: $0.marker, point: $0.anchor) }) ?? pointHit {
            Haptics.select()
            var clustered = executionHits.map(\.marker)
            if !clustered.contains(where: { $0.id == hit.marker.id }) { clustered.append(hit.marker) }
            withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
                picked = hit.marker.id
                pickedExecutions = clustered.sorted { $0.time < $1.time }
            }
            return
        }
        if let hit = markerPoints(layout).first(where: { hypot($0.point.x - point.x, $0.point.y - point.y) < 22 }) {
            Haptics.select()
            withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { picked = hit.marker.id; pickedExecutions = [] }
            return
        }
        Haptics.select()
        let p = clamp(point, layout)
        cross = p
        crossHaptic = layout.index(atX: p.x)
    }

    private func clamp(_ p: CGPoint, _ layout: Layout) -> CGPoint {
        CGPoint(x: min(max(p.x, layout.plot.minX), layout.plot.maxX), y: min(max(p.y, layout.plot.minY), layout.plot.maxY))
    }

    private func pan(_ layout: Layout) -> some Gesture {
        DragGesture(minimumDistance: 2, coordinateSpace: .local)
            .onChanged { value in
                if cross != nil {
                    // While the crosshair is shown, dragging moves it (web-mobile behaviour).
                    let p = clamp(value.location, layout)
                    cross = p
                    let index = layout.index(atX: p.x)
                    if index != crossHaptic { crossHaptic = index; Haptics.select() }
                    return
                }
                if value.startLocation.x > layout.plot.maxX {
                    if axisStart == nil { axisStart = yZoom }
                    yZoom = min(20, max(0.05, (axisStart ?? 1) * exp(Double(value.translation.height) / 180)))
                    return
                }
                if panStart == nil {
                    stopGlide()
                    if showOffscreenPrices, !timestampFocus, focusRange == nil, layout.firstVisible <= layout.lastVisible {
                        let floor = layout.plot.maxY - (wallpaper ? bottomOverlay + 24 : 4)
                        if floor > layout.plot.minY + 80 { gestureEdges = edgeCatalog(layout, floor: floor) }
                    }
                    panStart = (offset, yShift)
                    viewportInteracted = true
                    #if DEBUG
                    ChartFrameProbe.shared.begin("drag")
                    #endif
                }
                let start = panStart ?? (offset, yShift)
                offset = clampOffset(start.offset + value.translation.width / spacing, layout: layout)
                yShift = start.shift + Double(value.translation.height / layout.plot.height) * yZoom
            }
            .onEnded { value in
                if axisStart != nil { axisStart = nil; return }
                guard panStart != nil else { return }
                panStart = nil
                #if DEBUG
                ChartFrameProbe.shared.end()
                #endif
                let bounds = offsetBounds(layout: layout)
                glide = reduceMotion ? nil : Glide(origin: offset, speed: value.velocity.width, spacing: spacing, lower: bounds.lower, upper: bounds.upper)
                #if DEBUG
                if glide != nil { ChartFrameProbe.shared.begin("glide") }
                #endif
            }
    }

    private var zoom: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                if zoomStart == nil {
                    stopGlide()
                    zoomStart = spacing
                    viewportInteracted = true
                    #if DEBUG
                    ChartFrameProbe.shared.begin("pinch")
                    #endif
                }
                spacing = min(32, max(1.6, (zoomStart ?? spacing) * value.magnification))
            }
            .onEnded { _ in
                zoomStart = nil; Haptics.select()
                #if DEBUG
                ChartFrameProbe.shared.end()
                #endif
            }
    }

    private func offsetBounds(layout: Layout) -> (lower: CGFloat, upper: CGFloat) {
        let visible = layout.plot.width / spacing
        let latest = layout.timestampAxis ? layout.timelineEnd + rightPad : CGFloat(bars.count)
        return (-visible * 0.6, max(0, latest - visible * 0.3))
    }

    private func clampOffset(_ value: CGFloat, layout: Layout) -> CGFloat {
        let bounds = offsetBounds(layout: layout)
        return min(max(value, bounds.lower), bounds.upper)
    }

    /// Fling momentum in bars: exponential decay at UIScrollView's normal rate (0.998 per ms ≈ 2 per s),
    /// stopping at 8 pt/s or at the pan limit. Analytic, so every frame shows the exact position for its time.
    private struct Glide {
        static let decay = 2.0
        let id = UUID()
        let start = Date.now
        var origin: CGFloat
        let velocity: Double          // bars per second
        var lower: CGFloat
        var upper: CGFloat
        let duration: Double

        init?(origin: CGFloat, speed: CGFloat, spacing: CGFloat, lower: CGFloat, upper: CGFloat) {
            guard abs(speed) > 120, spacing > 0, speed.isFinite else { return nil }
            let velocity = Double(speed / spacing)
            let edge = Double((velocity > 0 ? upper : lower) - origin)
            guard edge * velocity > 0 else { return nil }   // already at the limit it is flung toward
            let toRest = log(Double(abs(speed)) / 8) / Self.decay
            let reach = edge * Self.decay / velocity           // fraction of the full coast needed to hit the edge
            let toEdge = reach < 1 ? -log(1 - reach) / Self.decay : .infinity
            self.origin = origin; self.velocity = velocity; self.lower = lower; self.upper = upper
            duration = max(0.05, min(toRest, toEdge))
        }

        func offset(at date: Date) -> CGFloat {
            let t = min(max(0, date.timeIntervalSince(start)), duration)
            return min(max(origin + CGFloat(velocity / Self.decay * (1 - exp(-Self.decay * t))), lower), upper)
        }

        var final: CGFloat { offset(at: start.addingTimeInterval(duration)) }
    }

    // MARK: layout

    private struct Layout {
        let bars: [Candle]
        let spacing: CGFloat
        let rightIndex: CGFloat
        let timelineEnd: CGFloat
        let plot: CGRect
        let period: CandlePeriod
        let timestampAxis: Bool
        let timeAxis: ChartTimeAxis
        let firstVisible: Int
        let lastVisible: Int
        let low: Double
        let high: Double

        init(bars: [Candle], spacing: CGFloat, offset: CGFloat, plot: CGRect, rightPad: CGFloat, period: CandlePeriod, range: FibRange?,
             lines: [ChartLine], wallpaper: Bool, bottomReserve: Double, hasBottomOverlay: Bool,
             yShift: Double, yZoom: Double,
             timestampAxis: Bool = false, focusRange: ClosedRange<Double>? = nil, executions: [ExecutionMarker] = [],
             fitReferenceLevels: Bool) {
            // Live charts retain their O(visible-bars) layout work; only the short historical replay
            // window materializes a timestamp axis.
            let axis = ChartTimeAxis(times: timestampAxis ? bars.map(\.time) : [], periodSeconds: period.seconds)
            let lastPosition = bars.isEmpty ? 0 : axis.position(forFractionalBarIndex: CGFloat(bars.count - 1))
            let focusEnd: CGFloat
            if timestampAxis, let focusRange { focusEnd = axis.position(atTime: focusRange.upperBound) }
            else { focusEnd = lastPosition }
            let end = timestampAxis ? max(lastPosition, focusEnd) : CGFloat(bars.count - 1)
            let right = end + rightPad - offset
            let left = right - plot.width / max(spacing, 0.25)
            let visible: ClosedRange<Int>?
            if timestampAxis {
                visible = axis.visibleBarRange(left: left, right: right)
            } else if bars.isEmpty {
                visible = nil
            } else {
                visible = ChartVisibleBarWindow.indices(count: bars.count, left: left, right: right)
            }
            self.bars = bars; self.spacing = spacing; self.plot = plot; self.period = period
            self.timestampAxis = timestampAxis; timeAxis = axis
            timelineEnd = end; rightIndex = right
            firstVisible = visible?.lowerBound ?? 0
            lastVisible = visible?.upperBound ?? -1
            let visibleStart = visible.map { bars[$0.lowerBound].time }
            let visibleEnd = visible.map { bars[$0.upperBound].time + period.seconds }
            var priceScale = ChartPriceScale(ohlc: ChartVisibleBarWindow.ohlc(bars, indices: visible))
            if fitReferenceLevels {
                if let range {
                    priceScale.include(ChartPriceLevel(price: range.lower, startTime: range.start, endTime: range.end),
                                       visibleStart: visibleStart, visibleEnd: visibleEnd)
                    priceScale.include(ChartPriceLevel(price: range.upper, startTime: range.start, endTime: range.end),
                                       visibleStart: visibleStart, visibleEnd: visibleEnd)
                }
                for line in lines where line.fitsScale {
                    priceScale.include(ChartPriceLevel(price: line.price, startTime: line.startTime, endTime: line.endTime),
                                       visibleStart: visibleStart, visibleEnd: visibleEnd)
                }
            }
            if timestampAxis {
                for fill in executions where fill.time.isFinite && fill.price.isFinite && fill.price > 0 {
                    let position = axis.position(atTime: fill.time)
                    if left <= position && position <= right { priceScale.include(fill.price) }
                }
            }
            // Fractions of the plot kept clear: top for controls/readout, bottom for overlays and volume.
            let topFrac = wallpaper ? 0.06 : 0.07 + 28 / max(Double(plot.height) - 28, 1)
            let botFrac = ChartPlotReserve.bottomFraction(wallpaper: wallpaper, hasBottomOverlay: hasBottomOverlay,
                                                          bottomReserve: bottomReserve, plotHeight: Double(plot.height))
            let domain = priceScale.domain(topFraction: topFrac, bottomFraction: botFrac,
                                           yZoom: yZoom, yShift: yShift)
            low = domain.low; high = domain.high
        }

        func x(_ index: CGFloat) -> CGFloat {
            let position = timestampAxis ? timeAxis.position(forFractionalBarIndex: index) : index
            return plot.maxX - (rightIndex - position) * spacing
        }
        func y(_ price: Double) -> CGFloat { plot.minY + CGFloat((high - price) / (high - low)) * plot.height }
        func price(atY y: CGFloat) -> Double { high - Double((y - plot.minY) / plot.height) * (high - low) }
        func index(atX x: CGFloat) -> Int {
            let position = rightIndex - (plot.maxX - x) / spacing
            if timestampAxis { return timeAxis.nearestBarIndex(atPosition: position) ?? 0 }
            return min(max(Int(position.rounded()), 0), max(0, bars.count - 1))
        }

        func index(atTime t: Double) -> CGFloat {
            guard timestampAxis else {
                guard let first = bars.first, let last = bars.last else { return 0 }
                if t <= first.time { return CGFloat((t - first.time) / period.seconds) }
                if t >= last.time { return CGFloat(bars.count - 1) + CGFloat((t - last.time) / period.seconds) }
                var lo = 0, hi = bars.count - 1
                while lo + 1 < hi { let mid = (lo + hi) / 2; if bars[mid].time <= t { lo = mid } else { hi = mid } }
                let a = bars[lo].time, b = bars[hi].time
                return CGFloat(lo) + CGFloat((t - a) / max(b - a, 1))
            }
            return timeAxis.barIndex(atTime: t)
        }
    }

    // Number styles are slow to apply; axis ticks and level prices repeat every frame, so cache the strings.
    private func format(_ value: Double) -> String {
        memo.text(.init(kind: 0, value: value, other: 0, digits: decimals)) { value.formatted(.number.precision(.fractionLength(decimals)).grouping(.never)) }
    }
    private func groupedPrice(_ value: Double) -> String {
        memo.text(.init(kind: 1, value: value, other: 0, digits: decimals)) { value.formatted(.number.precision(.fractionLength(0...max(2, decimals)))) }
    }

    // MARK: drawing

    private func draw(_ context: inout GraphicsContext, layout: Layout, axisY: CGFloat) {
        let plot = layout.plot, dark = scheme == .dark
        let gridColor = dark ? ChartPalette.hex(0x20232B) : ChartPalette.hex(0xEEF1F5)
        let textColor = dark ? ChartPalette.hex(0xC7C4D7) : ChartPalette.hex(0x64748B)
        let priceFloor = wallpaper ? axisY - 12 : plot.maxY - 4

        if let watermark {
            context.draw(Text(watermark).font(.system(size: wallpaper ? 54 : 44, weight: .bold)).foregroundStyle(Color.primary.opacity(dark ? 0.06 : 0.05)),
                         at: CGPoint(x: plot.midX, y: (plot.minY + priceFloor) / 2))
        }

        let ticks = niceTicks(low: layout.low, high: layout.high, count: wallpaper ? 9 : 6)
        let lastY = replayEntryTitle == nil ? layout.bars.last.map { layout.y($0.close) } : nil
        for value in ticks {
            let y = layout.y(value)
            guard y > plot.minY + 8, y < priceFloor else { continue }
            if !wallpaper { context.stroke(Path { $0.move(to: CGPoint(x: plot.minX, y: y)); $0.addLine(to: CGPoint(x: plot.maxX, y: y)) }, with: .color(gridColor), lineWidth: 1) }
            if let lastY, abs(lastY - y) < 14 { continue }
            context.draw(Text(format(value)).font(.system(size: 10).monospacedDigit()).foregroundStyle(textColor), at: CGPoint(x: plot.maxX + 6, y: y), anchor: .leading)
        }

        var clipped = context
        clipped.clip(to: Path(plot))
        // Stitch 4f56e93594b0418c9259759d6f6737e2: time boundaries, not an invented price trajectory.
        if replayEntryTitle != nil, let holding = focusRange {
            let start = layout.x(layout.index(atTime: holding.lowerBound))
            let end = layout.x(layout.index(atTime: holding.upperBound))
            let left = max(plot.minX, start), right = min(plot.maxX, end)
            if right > left {
                clipped.fill(Path(CGRect(x: left, y: plot.minY, width: right - left, height: plot.height)),
                             with: .color(ChartPalette.accent.opacity(dark ? 0.09 : 0.05)))
            }
            for x in [start, end] where plot.minX...plot.maxX ~= x {
                clipped.stroke(Path { $0.move(to: .init(x: x, y: plot.minY)); $0.addLine(to: .init(x: x, y: plot.maxY)) },
                               with: .color(ChartPalette.reference.opacity(0.48)), style: StrokeStyle(lineWidth: 0.8, dash: [3, 4]))
            }
        }
        guard layout.firstVisible <= layout.lastVisible else {
            if layout.timestampAxis { drawExecutions(&clipped, layout: layout) }
            return
        }
        let visible = layout.bars[layout.firstVisible...layout.lastVisible]

        // One path per colour (up / down / NY Monday) for candles and for volume: a handful of fills per
        // frame instead of a stroke and two fills per bar. Edges snap to device pixels so candles stay
        // crisp while panning instead of shimmering across fractional positions.
        let scale = max(displayScale, 1), pixel = 1 / scale
        func snap(_ v: CGFloat) -> CGFloat { (v * scale).rounded() / scale }
        let bodyWidth = max(pixel, snap(max(1, layout.spacing * 0.72))), wickWidth = max(pixel, snap(1))
        let wickInset = snap((bodyWidth - wickWidth) / 2)
        var candlePaths = [Path(), Path(), Path(), Path()], volumePaths = [Path(), Path(), Path(), Path()]
        let maxVolume = visible.lazy.map(\.volume).max() ?? 0
        let volumeFloor = snap(wallpaper ? axisY - 12 : plot.maxY), volumeHeight = plot.height * (wallpaper ? 0.08 : 0.14)
        for (index, bar) in zip(layout.firstVisible..., visible) {
            let tone = candleTone(bar), left = snap(layout.x(CGFloat(index)) - bodyWidth / 2)
            let top = snap(layout.y(max(bar.open, bar.close))), bottom = snap(layout.y(min(bar.open, bar.close)))
            let high = snap(layout.y(bar.high)), low = snap(layout.y(bar.low))
            candlePaths[tone].addRect(CGRect(x: left + wickInset, y: high, width: wickWidth, height: max(pixel, low - high)))
            candlePaths[tone].addRect(CGRect(x: left, y: top, width: bodyWidth, height: max(snap(1), bottom - top)))
            if maxVolume > 0, bar.volume > 0 {
                let h = snap(CGFloat(bar.volume / maxVolume) * volumeHeight)
                if h > 0 { volumePaths[tone].addRect(CGRect(x: left, y: volumeFloor - h, width: bodyWidth, height: h)) }
            }
        }

        if let focusBarTime, let index = barIndex(focusBarTime), index >= layout.firstVisible, index <= layout.lastVisible {
            let x = layout.x(CGFloat(index)), left = max(plot.minX, x - layout.spacing / 2), right = min(plot.maxX, x + layout.spacing / 2)
            if right > left {
                clipped.fill(Path(CGRect(x: left, y: plot.minY, width: right - left, height: plot.height)),
                             with: .color(ChartPalette.accent.opacity(dark ? 0.12 : 0.08)))
            }
        }

        // Volume rests on the time axis, its own scale.
        for tone in 0..<4 where !volumePaths[tone].isEmpty { clipped.fill(volumePaths[tone], with: .color(toneColor(tone).opacity(0.22))) }

        if cloud { drawCloud(&clipped, layout: layout, dark: dark) }

        // Previous weeks first (lighter), then the current week.
        // Full history can hold hundreds of weeks: draw only those overlapping the visible span.
        let shownFrom = layout.bars[layout.firstVisible].time, shownTo = layout.bars[layout.lastVisible].time + period.seconds
        for past in history where past.end > shownFrom && past.start < shownTo {
            drawRange(&clipped, past, layout: layout, dark: dark, inner: false, faded: true, priceFloor: priceFloor)
        }
        if let range { drawRange(&clipped, range, layout: layout, dark: dark, inner: innerLines, faded: false, priceFloor: priceFloor) }

        for tone in 0..<4 where !candlePaths[tone].isEmpty { clipped.fill(candlePaths[tone], with: .color(toneColor(tone))) }

        if averages {
            let step = period.seconds
            for line in ChartPalette.averages {
                let values = memo.average(line.kind, line.period, layout.bars, step: step)
                var path = Path(), started = false
                let from = max(0, layout.firstVisible - 1), to = min(values.count - 1, layout.lastVisible + 1)
                if from <= to {
                    for i in from...to {
                        guard let v = values[i] else { started = false; continue }
                        let point = CGPoint(x: layout.x(CGFloat(i)), y: layout.y(v))
                        if started { path.addLine(to: point) } else { path.move(to: point); started = true }
                    }
                }
                clipped.stroke(path, with: .color(line.color), lineWidth: 1.1)
            }
        }

        for line in lines where line.price > 0 {
            let y = layout.y(line.price)
            guard y >= plot.minY, y <= priceFloor else { continue }
            var left = line.startTime.map { layout.x(layout.index(atTime: $0)) } ?? plot.minX
            var right = line.endTime.map { layout.x(layout.index(atTime: $0)) } ?? plot.maxX
            left = max(plot.minX, left); right = min(plot.maxX, right)
            if replayEntryTitle != nil, let span = focusRange {
                left = max(left, layout.x(layout.index(atTime: span.lowerBound)))
                right = min(right, layout.x(layout.index(atTime: span.upperBound)))
            }
            guard right > left else { continue }
            clipped.stroke(Path { $0.move(to: CGPoint(x: left, y: y)); $0.addLine(to: CGPoint(x: right, y: y)) },
                           with: .color(line.color), style: StrokeStyle(lineWidth: line.lineWidth, dash: line.dashed ? [5, 4] : []))
            clipped.draw(Text("\(line.title) \(format(line.price))").font(.system(size: 10, weight: .semibold)).foregroundStyle(line.color),
                         at: CGPoint(x: left + 4, y: y - 3), anchor: .bottomLeading)
        }

        drawMarkers(&clipped, layout: layout)
        drawExecutions(&clipped, layout: layout)

        if replayEntryTitle == nil, let last = layout.bars.last {
            let y = layout.y(last.close), color = last.close >= last.open ? ChartPalette.up : ChartPalette.down
            if y >= plot.minY && y <= priceFloor {
                clipped.stroke(Path { $0.move(to: CGPoint(x: plot.minX, y: y)); $0.addLine(to: CGPoint(x: plot.maxX, y: y)) },
                               with: .color(color.opacity(0.55)), style: StrokeStyle(lineWidth: 1, dash: [2, 3]))
                let rect = CGRect(x: plot.maxX + 1, y: y - 9, width: axisWidth - 2, height: 18)
                context.fill(Path(roundedRect: rect, cornerRadius: 5), with: .color(color))
                context.draw(Text(format(last.close)).font(.system(size: 10, weight: .semibold).monospacedDigit()).foregroundStyle(.white), at: CGPoint(x: rect.midX, y: rect.midY))
            }
        }

        // Time axis (owner 2026-11-08 “怎么连时间都看不到”).
        let stride = max(1, Int(ceil(84 / layout.spacing)))
        var i = layout.firstVisible - layout.firstVisible % stride
        while i <= layout.lastVisible {
            if i >= 0 {
                let x = layout.x(CGFloat(i))
                if x > plot.minX + 24 && x < plot.maxX - 24 {
                    context.draw(Text(timeLabel(layout.bars[i].time, previous: i >= stride ? layout.bars[i - stride].time : nil))
                                    .font(.system(size: 10, weight: .medium).monospacedDigit()).foregroundStyle(textColor),
                                 at: CGPoint(x: x, y: axisY))
                }
            }
            i += stride
        }

        // Crosshair with time and price pills.
        if let cross {
            let index = layout.index(atX: cross.x), x = layout.x(CGFloat(index)), line = Color.primary.opacity(0.4)
            context.stroke(Path { $0.move(to: CGPoint(x: x, y: plot.minY)); $0.addLine(to: CGPoint(x: x, y: priceFloor)) }, with: .color(line), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            context.stroke(Path { $0.move(to: CGPoint(x: plot.minX, y: cross.y)); $0.addLine(to: CGPoint(x: plot.maxX, y: cross.y)) }, with: .color(line), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            let pill = Color(white: 0.2)
            let priceRect = CGRect(x: plot.maxX + 1, y: cross.y - 9, width: axisWidth - 2, height: 18)
            context.fill(Path(roundedRect: priceRect, cornerRadius: 5), with: .color(pill))
            context.draw(Text(format(layout.price(atY: cross.y))).font(.system(size: 10, weight: .semibold).monospacedDigit()).foregroundStyle(.white),
                         at: CGPoint(x: priceRect.midX, y: priceRect.midY))
            if !layout.bars.isEmpty {
                let label = context.resolve(Text(Self.fullTime(layout.bars[index].time)).font(.system(size: 10, weight: .semibold).monospacedDigit()).foregroundStyle(.white))
                let w = label.measure(in: plot.size).width + 14
                let timeRect = CGRect(x: min(max(x - w / 2, plot.minX + 2), plot.maxX - w - 2), y: axisY - 9, width: w, height: 18)
                context.fill(Path(roundedRect: timeRect, cornerRadius: 5), with: .color(pill))
                context.draw(label, at: CGPoint(x: timeRect.midX, y: timeRect.midY))
            }
        }
    }

    /// Web-mobile levels: dark 1.618 lines, grey Monday high/low, plain right-aligned "名称 (价格)" labels.
    private func drawRange(_ context: inout GraphicsContext, _ range: FibRange, layout: Layout, dark: Bool, inner: Bool, faded: Bool, priceFloor: CGFloat) {
        let plot = layout.plot
        let x1 = max(plot.minX, layout.x(layout.index(atTime: range.start))), x2 = min(plot.maxX, layout.x(layout.index(atTime: range.end)))
        guard x2 > x1 + 2 else { return }
        let alpha = faded ? 0.45 : 1
        let fib = ChartPalette.fibLine(dark: dark).opacity(alpha), monday = ChartPalette.mondayLine.opacity(alpha)
        let levels = range.levels(inner: inner)
        for level in levels {
            let y = layout.y(level.price)
            guard y >= plot.minY, y <= priceFloor else { continue }
            let color: Color = level.kind == .upper || level.kind == .lower ? fib : level.kind == .monday ? monday : ChartPalette.reference.opacity(0.6 * alpha)
            context.stroke(Path { $0.move(to: CGPoint(x: x1, y: y)); $0.addLine(to: CGPoint(x: x2, y: y)) },
                           with: .color(color), style: StrokeStyle(lineWidth: 1, dash: level.kind == .mid || level.kind == .inner ? [4, 4] : []))
        }
        var taken: [CGFloat] = []
        for level in levels.sorted(by: { priority($0.kind) < priority($1.kind) }) where level.kind != .inner || !faded {
            let y = layout.y(level.price), labelY = max(plot.minY + 14, y - 3)
            guard y >= plot.minY, y <= priceFloor, !taken.contains(where: { abs($0 - labelY) < 14 }) else { continue }
            let color: Color = level.kind == .upper || level.kind == .lower ? fib : monday
            let text = context.resolve(Text("\(level.title) (\(groupedPrice(level.price)))").font(.system(size: faded ? 10 : 11, weight: .medium)).foregroundStyle(color))
            // The current week always keeps its labels (a 12H / 1D segment is narrower than the text);
            // previous weeks label only when the segment is wide enough.
            if !faded || x2 - x1 > text.measure(in: plot.size).width + 8 {
                context.draw(text, at: CGPoint(x: x2 - 4, y: labelY), anchor: .bottomTrailing)
                taken.append(labelY)
            }
        }
    }

    // MARK: touch markers

    /// The bar holding time `t` (last bar opening at or before it), if any.
    private func barIndex(_ t: Double) -> Int? {
        guard let first = bars.first, t >= first.time, t < (bars.last?.time ?? 0) + period.seconds else { return nil }
        var lo = 0, hi = bars.count - 1
        while lo < hi { let mid = (lo + hi + 1) / 2; if bars[mid].time <= t { lo = mid } else { hi = mid - 1 } }
        return lo
    }

    /// Given markers plus each previous week's first touch after Monday (from the bars). The scan runs
    /// once per change of bars/history, not once per frame.
    private var allMarkers: [ChartMarker] {
        guard markHistory, !bars.isEmpty else { return markers }
        return markers + memo.historyMarkers(bars: bars, history: history) { historyTouches() }
    }

    private func historyTouches() -> [ChartMarker] {
        var out: [ChartMarker] = []
        guard let firstTime = bars.first?.time, let lastTime = bars.last?.time else { return out }
        for past in history where past.end > firstTime && past.start <= lastTime {
            let mondayEnd = past.start   // a FibRange starts when its Monday closes (Tuesday 00:00 NY)
            guard let from = barIndex(max(mondayEnd, bars[0].time)) ?? (bars[0].time >= mondayEnd ? 0 : nil) else { continue }
            var upper: Double?, lower: Double?
            for bar in bars[from...] where bar.time >= mondayEnd {
                if bar.time >= past.end { break }
                if upper == nil && bar.high >= past.upper { upper = bar.time }
                if lower == nil && bar.low <= past.lower { lower = bar.time }
                if upper != nil && lower != nil { break }
            }
            if let upper { out.append(ChartMarker(time: upper, price: past.upper, edge: .upper, faded: true)) }
            if let lower { out.append(ChartMarker(time: lower, price: past.lower, edge: .lower, faded: true)) }
        }
        return out
    }

    private func markerPoints(_ layout: Layout) -> [(marker: ChartMarker, point: CGPoint)] {
        allMarkers.compactMap { marker in
            guard let index = barIndex(marker.time), index >= layout.firstVisible, index <= layout.lastVisible else { return nil }
            let point = CGPoint(x: layout.x(CGFloat(index)), y: layout.y(marker.price))
            guard point.y > layout.plot.minY, point.y < layout.plot.maxY else { return nil }
            return (marker, point)
        }
    }

    /// The cloud ledger is independent of its 12H candles. A real fill can land in a candle gap;
    /// keep its timestamp and leave the underlying OHLC blank.
    private func executionPoints(_ layout: Layout) -> [(marker: ExecutionMarker, point: CGPoint)] {
        executionMarkers.compactMap { marker in
            guard marker.time.isFinite, marker.price.isFinite, marker.price > 0 else { return nil }
            let x: CGFloat
            if layout.timestampAxis {
                x = layout.plot.maxX - (layout.rightIndex - layout.timeAxis.position(atTime: marker.time)) * layout.spacing
            } else {
                guard let index = barIndex(marker.time), index >= layout.firstVisible, index <= layout.lastVisible,
                      index < bars.count, marker.time < bars[index].time + period.seconds else { return nil }
                let fraction = min(max((marker.time - bars[index].time) / period.seconds, 0), 0.999)
                x = layout.x(CGFloat(index) + CGFloat(fraction))
            }
            let point = CGPoint(x: x, y: layout.y(marker.price))
            guard point.x >= layout.plot.minX, point.x <= layout.plot.maxX,
                  point.y > layout.plot.minY, point.y < layout.plot.maxY else { return nil }
            return (marker, point)
        }
    }

    private func drawMarkers(_ context: inout GraphicsContext, layout: Layout) {
        for (marker, point) in markerPoints(layout) {
            let alpha = marker.faded ? 0.4 : 1, color = marker.edge == .upper ? ChartPalette.down : ChartPalette.up
            let selected = marker.id == picked
            if selected { context.fill(Path(ellipseIn: CGRect(x: point.x - 9, y: point.y - 9, width: 18, height: 18)), with: .color(color.opacity(0.22))) }
            context.fill(Path(ellipseIn: CGRect(x: point.x - 4.5, y: point.y - 4.5, width: 9, height: 9)), with: .color(.white.opacity(alpha)))
            context.fill(Path(ellipseIn: CGRect(x: point.x - 3.2, y: point.y - 3.2, width: 6.4, height: 6.4)), with: .color(color.opacity(alpha)))
            guard let score = marker.score else { continue }
            let text = context.resolve(Text(String(format: "%.2f", score)).font(.system(size: 10, weight: .bold).monospacedDigit()).foregroundStyle(.white))
            let size = text.measure(in: layout.plot.size)
            let rect = CGRect(x: point.x - size.width / 2 - 5, y: marker.edge == .upper ? point.y - 9 - size.height - 4 : point.y + 9,
                              width: size.width + 10, height: size.height + 4)
            let shape = Path(roundedRect: rect, cornerRadius: rect.height / 2)
            if score >= highScore {
                context.fill(shape, with: .linearGradient(Gradient(colors: [ChartPalette.hex(0xFF9F0A), ChartPalette.hex(0xF5731A)]),
                                                          startPoint: CGPoint(x: rect.minX, y: rect.minY), endPoint: CGPoint(x: rect.maxX, y: rect.maxY)))
            } else {
                context.fill(shape, with: .color(ChartPalette.hex(0x9CA3AF).opacity(alpha)))
            }
            context.draw(text, at: CGPoint(x: rect.midX, y: rect.midY))
        }
    }

    private func drawExecutions(_ context: inout GraphicsContext, layout: Layout) {
        let points = executionPoints(layout)
        let tags = executionTagPlacements(layout)
        // Leaders sit beneath the fill glyphs so the true price/time anchor stays visible.
        for tag in tags {
            let edge = CGPoint(x: min(max(tag.anchor.x, tag.rect.minX + 7), tag.rect.maxX - 7),
                               y: tag.anchor.y < tag.rect.minY ? tag.rect.minY : tag.anchor.y > tag.rect.maxY ? tag.rect.maxY : tag.rect.midY)
            context.stroke(Path { path in path.move(to: tag.anchor); path.addLine(to: edge) },
                           with: .color(tag.color.opacity(0.9)), style: StrokeStyle(lineWidth: 1.2, lineCap: .round))
        }
        for (marker, point) in points {
            let color = marker.color, selected = marker.id == picked
            if selected {
                context.fill(Path(ellipseIn: CGRect(x: point.x - 9, y: point.y - 9, width: 18, height: 18)),
                             with: .color(color.opacity(0.22)))
            }
            if marker.action == "entry" {
                let diamond = Path { path in
                    path.move(to: CGPoint(x: point.x, y: point.y - 5))
                    path.addLine(to: CGPoint(x: point.x + 5, y: point.y))
                    path.addLine(to: CGPoint(x: point.x, y: point.y + 5))
                    path.addLine(to: CGPoint(x: point.x - 5, y: point.y))
                    path.closeSubpath()
                }
                let center = Path { path in
                    path.move(to: CGPoint(x: point.x, y: point.y - 3.2))
                    path.addLine(to: CGPoint(x: point.x + 3.2, y: point.y))
                    path.addLine(to: CGPoint(x: point.x, y: point.y + 3.2))
                    path.addLine(to: CGPoint(x: point.x - 3.2, y: point.y))
                    path.closeSubpath()
                }
                context.fill(diamond, with: .color(.white))
                context.fill(center, with: .color(color))
            } else {
                context.fill(Path(ellipseIn: CGRect(x: point.x - 4.5, y: point.y - 4.5, width: 9, height: 9)), with: .color(.white))
                context.fill(Path(ellipseIn: CGRect(x: point.x - 3.1, y: point.y - 3.1, width: 6.2, height: 6.2)), with: .color(color))
            }
        }
        for tag in tags {
            let shape = Path(roundedRect: tag.rect, cornerRadius: 7)
            context.fill(shape, with: .color(tag.color))
            context.stroke(shape, with: .color(.white.opacity(0.78)), lineWidth: 1)
            let text = context.resolve(Text(tag.title).font(.system(size: 10, weight: .bold).monospacedDigit()).foregroundStyle(.white))
            context.draw(text, at: CGPoint(x: tag.rect.midX, y: tag.rect.midY))
        }
    }

    /// Label only the first funded entry and final ledger exit. Intermediate trims remain small dots.
    /// Each pill is positioned from the visible fill anchor, with a short leader and collision checks.
    private func executionTagPlacements(_ layout: Layout) -> [ExecutionTagPlacement] {
        let visible = executionPoints(layout)
        guard let entry = executionMarkers.filter({ $0.action == "entry" }).min(by: { $0.time < $1.time }),
              let exit = executionMarkers.filter({ $0.action != "entry" }).max(by: { $0.time < $1.time }) else { return [] }
        let endpoints: [(marker: ExecutionMarker, title: String)] = [
            (entry, replayEntryTitle ?? "① 开仓"),
            (exit, exit.action == "series_tail_close" ? "② 期末结算" : "② 平仓")
        ]
        var placed: [ExecutionTagPlacement] = []
        for endpoint in endpoints {
            guard let point = visible.first(where: { $0.marker.id == endpoint.marker.id })?.point else { continue }
            let width = max(58, CGFloat(endpoint.title.count) * 10.5 + 16)
            let height: CGFloat = 22
            let gap: CGFloat = 12
            let above = CGRect(x: point.x - width / 2, y: point.y - gap - height, width: width, height: height)
            let below = CGRect(x: point.x - width / 2, y: point.y + gap, width: width, height: height)
            let right = CGRect(x: point.x + gap, y: point.y - height / 2, width: width, height: height)
            let left = CGRect(x: point.x - gap - width, y: point.y - height / 2, width: width, height: height)
            let preferAbove = endpoint.marker.action == "entry" ? point.y > layout.plot.midY : point.y <= layout.plot.midY
            let ordered = preferAbove ? [above, below, right, left] : [below, above, left, right]
            func fit(_ candidate: CGRect) -> CGRect {
                CGRect(x: min(max(candidate.minX, layout.plot.minX + 2), layout.plot.maxX - candidate.width - 2),
                       y: min(max(candidate.minY, layout.plot.minY + 2), layout.plot.maxY - candidate.height - 2),
                       width: candidate.width, height: candidate.height)
            }
            let rect = ordered.map(fit).first { candidate in !placed.contains(where: { $0.rect.insetBy(dx: -3, dy: -3).intersects(candidate) }) } ?? fit(ordered[0])
            let color = endpoint.marker.action == "entry" ? ChartPalette.up : ChartPalette.down
            placed.append(ExecutionTagPlacement(marker: endpoint.marker, anchor: point, rect: rect, title: endpoint.title, color: color))
        }
        return placed
    }

    /// 0 up, 1 down, 2 NY Monday, 3 service-flagged/fallback first-week candle.
    private func candleTone(_ bar: Candle) -> Int {
        if highlightedBarTimes.contains(bar.time) || highlightRange.map({ $0.contains(bar.time) }) == true { return 3 }
        if colors.monday != nil, memo.isNYMonday(bar.time) { return 2 }
        return bar.close >= bar.open ? 0 : 1
    }

    private func toneColor(_ tone: Int) -> Color {
        switch tone {
        case 3: highlightColor ?? colors.up
        case 2: colors.monday ?? colors.up
        case 0: colors.up
        default: colors.down
        }
    }

    private func drawCloud(_ context: inout GraphicsContext, layout: Layout, dark: Bool) {
        let points = memo.cloud(layout.bars, step: period.seconds)
        let from = max(0, layout.firstVisible - 1), to = min(points.count - 1, layout.lastVisible + 1)
        guard from < to else { return }
        var segment: [(x: CGFloat, a: Double, b: Double)] = []
        func flush() {
            guard segment.count > 1 else { segment.removeAll(); return }
            let bullish = segment.contains { $0.a > $0.b }
            var path = Path()
            path.move(to: CGPoint(x: segment[0].x, y: layout.y(segment[0].a)))
            for p in segment.dropFirst() { path.addLine(to: CGPoint(x: p.x, y: layout.y(p.a))) }
            for p in segment.reversed() { path.addLine(to: CGPoint(x: p.x, y: layout.y(p.b))) }
            path.closeSubpath()
            context.fill(path, with: .color(ChartPalette.cloud(bullish, dark: dark)))
            segment.removeAll()
        }
        for i in from...to {
            guard let p = points[i] else { flush(); continue }
            let x = layout.x(CGFloat(i))
            if let prev = segment.last, (prev.a - prev.b) * (p.a - p.b) < 0 {
                let ratio = (prev.a - prev.b) / (prev.a - prev.b - p.a + p.b), value = prev.a + (p.a - prev.a) * ratio
                let cross = (x: prev.x + (x - prev.x) * CGFloat(ratio), a: value, b: value)
                segment.append(cross); flush(); segment.append(cross)
            }
            segment.append((x, p.a, p.b))
        }
        flush()
    }

    private func priority(_ kind: FibRange.Kind) -> Int {
        switch kind { case .upper, .lower: 0; case .monday: 1; case .mid: 2; case .inner: 3 }
    }

    /// OHLC readout for the crosshair bar, with its Beijing time (web-mobile “时间 · 开高低收”).
    private func readout(_ bar: Candle) -> some View {
        HStack(spacing: 7) {
            Text(Self.fullTime(bar.time)).foregroundStyle(Color.ink).fontWeight(.semibold)
            ForEach([("开", bar.open), ("高", bar.high), ("低", bar.low), ("收", bar.close)], id: \.0) { item in
                HStack(spacing: 2) {
                    Text(item.0).foregroundStyle(Color.muted)
                    Text(format(item.1)).foregroundStyle(bar.close >= bar.open ? ChartPalette.up : ChartPalette.down)
                }
            }
        }
        .font(.system(size: 10.5, weight: .medium).monospacedDigit())
        .lineLimit(1).minimumScaleFactor(0.7)
        .padding(.horizontal, 10).padding(.vertical, 6)
        .frosted(Capsule(), veil: 0.85)
    }

    private static let dayFormatter: DateFormatter = { let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd"; return f }()
    private static let timeFormatter: DateFormatter = { let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "HH:mm"; return f }()
    private static let fullFormatter: DateFormatter = { let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd HH:mm"; return f }()
    static func fullTime(_ t: Double) -> String { fullFormatter.string(from: Date(timeIntervalSince1970: t)) }

    static func executionTime(_ marker: ExecutionMarker) -> String {
        executionTime(marker.time, endTime: marker.endTime, phase: marker.phase)
    }

    static func executionTime(_ time: Double, endTime: Double?, phase: String) -> String {
        let start = fullTime(time)
        guard phase == "intrabar", let endTime, endTime > time else { return start }
        let end = fullTime(endTime)
        return start.prefix(5) == end.prefix(5) ? "\(start)–\(end.suffix(5))" : "\(start)–\(end)"
    }

    private func timeLabel(_ t: Double, previous: Double?) -> String {
        memo.text(.init(kind: 2, value: t, other: previous ?? -1, digits: period == .d1 || period == .h12 ? 1 : 0)) { makeTimeLabel(t, previous: previous) }
    }

    private func makeTimeLabel(_ t: Double, previous: Double?) -> String {
        let date = Date(timeIntervalSince1970: t)
        if period == .d1 || period == .h12 { return Self.dayFormatter.string(from: date) }
        if let previous, Self.dayFormatter.string(from: Date(timeIntervalSince1970: previous)) != Self.dayFormatter.string(from: date) {
            return Self.fullFormatter.string(from: date)
        }
        return Self.timeFormatter.string(from: date)
    }

    private func niceTicks(low: Double, high: Double, count: Int) -> [Double] {
        let span = high - low
        guard span > 0, span.isFinite else { return [] }
        let raw = span / Double(count), magnitude = pow(10, floor(log10(raw)))
        let step = [1, 2, 2.5, 5, 10].map { $0 * magnitude }.first { $0 >= raw } ?? raw
        var value = ceil(low / step) * step, result: [Double] = []
        while value <= high && result.count < 24 { result.append(value); value += step }
        return result
    }
}

/// Causal close-only averages (chart-style.js).
enum Indicators {
    static func sma(_ bars: [Candle], _ period: Int, step: Double? = nil) -> [Double?] {
        // Running sum over the current gap-free run; O(n) (Array.removeFirst made it O(n·period)).
        var out = [Double?](repeating: nil, count: bars.count), sum = 0.0, start = 0
        for (i, bar) in bars.enumerated() {
            if i > 0, let step, bar.time - bars[i - 1].time != step { start = i; sum = 0 }
            sum += bar.close
            if i - start + 1 > period { sum -= bars[i - period].close }
            if i - start + 1 >= period { out[i] = sum / Double(period) }
        }
        return out
    }

    static func ema(_ bars: [Candle], _ period: Int, step: Double? = nil) -> [Double?] {
        var out = [Double?](repeating: nil, count: bars.count), value: Double?, count = 0
        let k = 2 / Double(period + 1)
        for (i, bar) in bars.enumerated() {
            if i > 0, let step, bar.time - bars[i - 1].time != step { value = nil; count = 0 }
            value = value.map { k * bar.close + (1 - k) * $0 } ?? bar.close
            count += 1
            if count >= period { out[i] = value }
        }
        return out
    }

    static func cloud(_ bars: [Candle], step: Double) -> [(a: Double, b: Double)?] {
        var out: [(a: Double, b: Double)?] = [], a = 0.0, b = 0.0, last: Double?
        for bar in bars {
            if bar.emaReady == false {
                out.append(nil); a = 0; b = 0; last = bar.time
                continue
            }
            if let ema20 = bar.ema20, let ema50 = bar.ema50, ema20.isFinite, ema50.isFinite {
                out.append((ema20, ema50)); a = ema20; b = ema50; last = bar.time
                continue
            }
            if last.map({ bar.time - $0 != step }) ?? true { a = bar.close; b = bar.close }
            a += (bar.close - a) * 2 / 21; b += (bar.close - b) * 2 / 51; last = bar.time
            out.append((a, b))
        }
        return out
    }
}

/// Per-frame caches: indicator series while the bars are unchanged, NY-Monday flags per bar time,
/// previous-week touch markers, and formatted label strings.
final class IndicatorMemo {
    private struct BarsKey: Equatable {
        let count: Int, first: Double, last: Double, close: Double, high: Double, low: Double, step: Double
        init(_ bars: [Candle], step: Double) {
            count = bars.count; first = bars.first?.time ?? 0; last = bars.last?.time ?? 0
            close = bars.last?.close ?? 0; high = bars.last?.high ?? 0; low = bars.last?.low ?? 0; self.step = step
        }
    }
    private struct HistoryKey: Equatable {
        let bars: BarsKey, weeks: Int, firstStart: Double, lastStart: Double
    }
    struct TextKey: Hashable { let kind: UInt8; let value: Double; let other: Double; let digits: Int }

    private var key: BarsKey?
    private var averages: [String: [Double?]] = [:]
    private var cloudPoints: [(a: Double, b: Double)?] = []
    private var mondays: [Double: Bool] = [:]
    private var historyKey: HistoryKey?
    private var historyValue: [ChartMarker] = []
    private var texts: [TextKey: String] = [:]
    private static let ny: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/New_York")!; return c }()

    private func check(_ bars: [Candle], _ step: Double) {
        let next = BarsKey(bars, step: step)
        if next != key { key = next; averages = [:]; cloudPoints = [] }
    }

    func text(_ key: TextKey, _ make: () -> String) -> String {
        if let hit = texts[key] { return hit }
        if texts.count > 2048 { texts.removeAll(keepingCapacity: true) }
        let value = make()
        texts[key] = value
        return value
    }

    func historyMarkers(bars: [Candle], history: [FibRange], scan: () -> [ChartMarker]) -> [ChartMarker] {
        let next = HistoryKey(bars: BarsKey(bars, step: 0), weeks: history.count, firstStart: history.first?.start ?? 0, lastStart: history.last?.start ?? 0)
        if next != historyKey { historyKey = next; historyValue = scan() }
        return historyValue
    }
    func average(_ kind: String, _ period: Int, _ bars: [Candle], step: Double) -> [Double?] {
        check(bars, step)
        let id = kind + String(period)
        if let hit = averages[id] { return hit }
        let values = kind == "sma" ? Indicators.sma(bars, period, step: step) : Indicators.ema(bars, period, step: step)
        averages[id] = values
        return values
    }
    func cloud(_ bars: [Candle], step: Double) -> [(a: Double, b: Double)?] {
        check(bars, step)
        if cloudPoints.count != bars.count { cloudPoints = Indicators.cloud(bars, step: step) }
        return cloudPoints
    }
    /// The bar opens on a New York Monday (cloud-candle-colors.js category()).
    func isNYMonday(_ time: Double) -> Bool {
        if let hit = mondays[time] { return hit }
        if mondays.count > 8192 { mondays.removeAll() }
        let value = Self.ny.component(.weekday, from: Date(timeIntervalSince1970: time)) == 2
        mondays[time] = value
        return value
    }
}

/// Tap callout for a touch marker: edge and Beijing time, the touch's score with the 0.5 gate of the
/// live-wick6x3 paper account (通过评分), and the touched level. Read-only; no order action here.
private struct MarkerCallout: View {
    let marker: ChartMarker
    let price: String

    var body: some View {
        let color = marker.edge == .upper ? ChartPalette.down : ChartPalette.up
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 7, height: 7)
                Text("\(marker.faded ? "往周" : "")\(marker.edge == .upper ? "上沿" : "下沿")触价").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.ink)
                Spacer(minLength: 8)
                Text(CandleChart.fullTime(marker.time)).font(.system(size: 12).monospacedDigit()).foregroundStyle(Color.muted)
            }
            HStack(spacing: 6) {
                Text("评分").font(.system(size: 12)).foregroundStyle(Color.muted)
                ScoreBadge(score: marker.score)
                Spacer(minLength: 8)
                if let score = marker.score {
                    Label(score >= 0.5 ? "已通过" : "未通过", systemImage: score >= 0.5 ? "checkmark.circle.fill" : "minus.circle")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(score >= 0.5 ? ChartPalette.up : Color.muted)
                } else {
                    Text(marker.faded ? "往周不评分" : "未评分").font(.system(size: 11)).foregroundStyle(Color.muted)
                }
            }
            HStack {
                Text("触价价位").font(.system(size: 12)).foregroundStyle(Color.muted)
                Spacer()
                Text(price).font(.system(size: 15, weight: .bold).monospacedDigit()).foregroundStyle(Color.ink)
            }
        }
        .padding(12)
        .frame(width: 214)
        .frosted(RoundedRectangle(cornerRadius: 16, style: .continuous), veil: 0.9)
        .accessibilityElement(children: .combine)
    }
}

/// Read-only details for a frozen strategy fill. A tap exposes the native H1 time, fill price and
/// the share of the original position, including repeated trims that share a 12H candle.
private struct ExecutionCallout: View {
    let markers: [ExecutionMarker]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(markers.count == 1 ? "成交流水" : "附近 \(markers.count) 笔成交")
                .font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(markers) { marker in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 5) {
                                Circle().fill(marker.color).frame(width: 6, height: 6)
                                Text(marker.label).font(.system(size: 11, weight: .semibold)).foregroundStyle(Color.ink)
                                Spacer(minLength: 4)
                                Text(CandleChart.executionTime(marker)).font(.system(size: 10).monospacedDigit()).foregroundStyle(Color.muted)
                            }
                            HStack(spacing: 8) {
                                Text("价 \(marker.price.formatted(.number.precision(.fractionLength(0...10)).grouping(.never)))")
                                Text("占初始 \((marker.share * 100).formatted(.number.precision(.fractionLength(0...2))))%")
                                Spacer(minLength: 2)
                                Text(marker.quantity.formatted(.number.precision(.fractionLength(0...8)).grouping(.automatic)))
                            }
                            .font(.system(size: 10).monospacedDigit()).foregroundStyle(Color.muted)
                            Text(marker.phase == "intrabar" ? "H1 区间内成交 · 精确时点未记录" : "K线开盘成交")
                                .font(.system(size: 9)).foregroundStyle(Color.muted)
                        }
                        .padding(.vertical, 4)
                        if marker.id != markers.last?.id { Divider() }
                    }
                }
            }
            .frame(maxHeight: 180)
            .scrollIndicators(.hidden)
        }
        .padding(11)
        .frame(width: 250)
        .frosted(RoundedRectangle(cornerRadius: 16, style: .continuous), veil: 0.9)
        .accessibilityElement(children: .combine)
    }
}

#if DEBUG
/// QA-only frame probe (DEBUG build + launch argument `-SpikeQAFrames YES`): while the chart is dragged,
/// pinched or gliding it records display-link frame intervals and the CPU time of each canvas draw, then
/// prints one line per gesture. Used to compare drag smoothness before/after a change on the simulator.
/// Release builds never contain it.
final class ChartFrameProbe: NSObject {
    nonisolated(unsafe) static let shared = ChartFrameProbe()
    private var link: CADisplayLink?
    private var last: CFTimeInterval = 0
    private var intervals: [Double] = []
    private var draws: [Double] = []
    private var label = ""
    private lazy var enabled = UserDefaults.standard.bool(forKey: "SpikeQAFrames")

    func begin(_ name: String) {
        guard enabled else { return }
        if link != nil { if name != label { end() } else { return } }
        label = name; intervals = []; draws = []; last = 0
        let link = CADisplayLink(target: self, selector: #selector(tick(_:)))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
        link.add(to: .main, forMode: .common)
        self.link = link
    }

    func drawn(_ seconds: Double) { if link != nil { draws.append(seconds) } }

    @objc private func tick(_ link: CADisplayLink) {
        if last > 0 { intervals.append(link.timestamp - last) }
        last = link.timestamp
    }

    func end() {
        guard let link else { return }
        link.invalidate(); self.link = nil
        guard intervals.count > 5 else { return }
        let sorted = intervals.sorted(), frame = sorted[sorted.count / 2]
        let dropped = intervals.reduce(0) { $0 + max(0, Int(($1 / frame).rounded()) - 1) }
        let d = draws.sorted()
        let ms = { (v: Double) in String(format: "%.2f", v * 1000) }
        print("[frames] \(label) frames=\(intervals.count) frame=\(ms(frame))ms p95=\(ms(sorted[(sorted.count - 1) * 95 / 100]))ms max=\(ms(sorted.last!))ms dropped=\(dropped) draws=\(d.count) draw_mean=\(ms(d.reduce(0, +) / Double(max(d.count, 1))))ms draw_p95=\(ms(d.isEmpty ? 0 : d[(d.count - 1) * 95 / 100]))ms")
    }
}
#endif
