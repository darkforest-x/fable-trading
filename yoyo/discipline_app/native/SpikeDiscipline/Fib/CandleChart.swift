import SwiftUI

/// Native candle chart replicating the workbench / web-mobile wallpaper chart (yoyo/monitor/static:
/// range-chart.js, chart-tools.js, chart-style.js six lines, monday-fib-chart.js + monday-chart-appearance.js
/// levels, cloud-signal-chart.js cloud). Owner 2026-11-08: full screen, time axis visible, tap shows the
/// bar's time, web-mobile level lines, previous weeks' ranges, custom candle colours.
/// Gestures: pan in both axes with momentum, pinch to zoom time, drag the price axis to scale price,
/// tap for the crosshair (drag moves it, tap again hides it), double-tap to reset and follow.
/// Display only — never an execution price or risk input.
struct ChartLine: Equatable, Identifiable {
    let price: Double
    let title: String
    let color: Color
    var dashed = false
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
    @State private var spacing: CGFloat = 7
    @State private var offset: CGFloat = 0
    @State private var yShift: Double = 0
    @State private var yZoom: Double = 1
    @State private var panStart: (offset: CGFloat, shift: Double)?
    @State private var axisStart: Double?
    @State private var zoomStart: CGFloat?
    @State private var cross: CGPoint?
    @State private var crossHaptic = -1
    @State private var memo = IndicatorMemo()
    @State private var picked: String?
    @AppStorage("touch.highScore") private var highScore = 0.5

    private let axisWidth: CGFloat = 58
    private let timeHeight: CGFloat = 20
    private let rightPad: CGFloat = 5

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let plot = CGRect(x: 0, y: topInset, width: max(1, size.width - axisWidth),
                              height: max(1, size.height - topInset - (wallpaper ? 0 : timeHeight)))
            // The time axis sits just above the floating bottom UI in wallpaper mode.
            let axisY = wallpaper ? plot.maxY - bottomOverlay - 12 : plot.maxY + timeHeight / 2 + 1
            let layout = Layout(bars: bars, spacing: spacing, offset: offset, plot: plot, rightPad: rightPad, period: period, range: range,
                                lines: lines, wallpaper: wallpaper, bottomReserve: wallpaper ? Double((bottomOverlay + 34) / max(plot.height, 1)) : 0,
                                yShift: yShift, yZoom: yZoom)
            ZStack(alignment: .topLeading) {
                if wallpaper { (scheme == .dark ? ChartPalette.hex(0x0B0E14) : Color.white) }
                Canvas(rendersAsynchronously: false) { context, _ in draw(&context, layout: layout, axisY: axisY) }
                if let picked, let hit = markerPoints(layout).first(where: { $0.marker.id == picked }) {
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
            .simultaneousGesture(SpatialTapGesture(count: 2).onEnded { _ in reset() })
            .simultaneousGesture(SpatialTapGesture(count: 1).onEnded { value in tap(value.location, layout: layout) })
            .onChange(of: layout.firstVisible) { _, first in
                if first < 12 && bars.count >= 55 { onNeedOlder() }
            }
        }
        .onChange(of: generation) { _, _ in offset = 0; spacing = 7; yShift = 0; yZoom = 1; cross = nil; picked = nil }
        .onChange(of: followToken) { _, _ in reset() }
        .onChange(of: bars.last?.time) { old, new in
            if let old, let new, new > old, offset > 0.5 { offset += CGFloat((new - old) / period.seconds) }
        }
        .accessibilityElement()
        .accessibilityLabel("\(watermark ?? "") \(period.label) K线")
    }

    private func reset() {
        Haptics.tap()
        withAnimation(.snappy(duration: 0.35)) { offset = 0; yShift = 0; yZoom = 1; cross = nil }
    }

    // MARK: gestures

    private func tap(_ point: CGPoint, layout: Layout) {
        guard point.x <= layout.plot.maxX else { return }
        if picked != nil { withAnimation(.easeOut(duration: 0.15)) { picked = nil }; return }
        if cross != nil { withAnimation(.easeOut(duration: 0.15)) { cross = nil }; return }
        if let hit = markerPoints(layout).first(where: { hypot($0.point.x - point.x, $0.point.y - point.y) < 22 }) {
            Haptics.select()
            withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { picked = hit.marker.id }
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
                if panStart == nil { panStart = (offset, yShift) }
                let start = panStart ?? (offset, yShift)
                offset = clampOffset(start.offset + value.translation.width / spacing, layout: layout)
                yShift = start.shift + Double(value.translation.height / layout.plot.height) * yZoom
            }
            .onEnded { value in
                if axisStart != nil { axisStart = nil; return }
                guard let start = panStart else { return }
                panStart = nil
                let target = clampOffset(start.offset + value.predictedEndTranslation.width / spacing, layout: layout)
                withAnimation(.smooth(duration: 0.55)) { offset = target }
            }
    }

    private var zoom: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                if zoomStart == nil { zoomStart = spacing }
                spacing = min(32, max(1.6, (zoomStart ?? spacing) * value.magnification))
            }
            .onEnded { _ in zoomStart = nil; Haptics.select() }
    }

    private func clampOffset(_ value: CGFloat, layout: Layout) -> CGFloat {
        let visible = layout.plot.width / spacing
        return min(max(value, -visible * 0.6), max(0, CGFloat(bars.count) - visible * 0.3))
    }

    // MARK: layout

    private struct Layout {
        let bars: [Candle]
        let spacing: CGFloat
        let rightIndex: CGFloat
        let plot: CGRect
        let period: CandlePeriod
        let firstVisible: Int
        let lastVisible: Int
        let low: Double
        let high: Double

        init(bars: [Candle], spacing: CGFloat, offset: CGFloat, plot: CGRect, rightPad: CGFloat, period: CandlePeriod, range: FibRange?,
             lines: [ChartLine], wallpaper: Bool, bottomReserve: Double, yShift: Double, yZoom: Double) {
            self.bars = bars; self.spacing = spacing; self.plot = plot; self.period = period
            rightIndex = CGFloat(bars.count - 1) + rightPad - offset
            firstVisible = max(0, Int(floor(rightIndex - plot.width / spacing)))
            lastVisible = min(bars.count - 1, Int(ceil(rightIndex)))
            var lo = Double.infinity, hi = -Double.infinity
            if firstVisible <= lastVisible {
                for bar in bars[firstVisible...lastVisible] { lo = min(lo, bar.low); hi = max(hi, bar.high) }
            }
            if let range, firstVisible <= lastVisible, bars[lastVisible].time + period.seconds > range.start, bars[firstVisible].time < range.end {
                lo = min(lo, max(0, range.lower)); hi = max(hi, range.upper)
            }
            for line in lines where line.price > 0 { lo = min(lo, line.price); hi = max(hi, line.price) }
            if !lo.isFinite || !hi.isFinite { lo = 0; hi = 1 }
            if hi - lo < hi * 1e-6 { let pad = max(hi * 0.001, 1e-9); lo -= pad; hi += pad }
            let span = hi - lo
            // Fractions of the plot kept clear: top for the controls/readout, bottom for floating UI.
            let topFrac = wallpaper ? 0.06 : 0.07 + 28 / max(Double(plot.height) - 28, 1)
            let botFrac = wallpaper ? min(0.6, bottomReserve + 0.04) : 0.07
            let usable = max(0.2, 1 - topFrac - botFrac)
            let full = span / usable
            let l0 = lo - full * botFrac, h0 = hi + full * topFrac
            let half = (h0 - l0) / 2 * yZoom, center = (l0 + h0) / 2 + yShift * (h0 - l0)
            low = center - half; high = center + half
        }

        func x(_ index: CGFloat) -> CGFloat { plot.maxX - (rightIndex - index) * spacing }
        func y(_ price: Double) -> CGFloat { plot.minY + CGFloat((high - price) / (high - low)) * plot.height }
        func price(atY y: CGFloat) -> Double { high - Double((y - plot.minY) / plot.height) * (high - low) }
        func index(atX x: CGFloat) -> Int { min(max(Int((rightIndex - (plot.maxX - x) / spacing).rounded()), 0), max(0, bars.count - 1)) }

        func index(atTime t: Double) -> CGFloat {
            guard let first = bars.first, let last = bars.last else { return 0 }
            if t <= first.time { return CGFloat((t - first.time) / period.seconds) }
            if t >= last.time { return CGFloat(bars.count - 1) + CGFloat((t - last.time) / period.seconds) }
            var lo = 0, hi = bars.count - 1
            while lo + 1 < hi { let mid = (lo + hi) / 2; if bars[mid].time <= t { lo = mid } else { hi = mid } }
            let a = bars[lo].time, b = bars[hi].time
            return CGFloat(lo) + CGFloat((t - a) / max(b - a, 1))
        }
    }

    private func format(_ value: Double) -> String { value.formatted(.number.precision(.fractionLength(decimals)).grouping(.never)) }
    private func groupedPrice(_ value: Double) -> String { value.formatted(.number.precision(.fractionLength(0...max(2, decimals)))) }

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
        let lastY = layout.bars.last.map { layout.y($0.close) }
        for value in ticks {
            let y = layout.y(value)
            guard y > plot.minY + 8, y < priceFloor else { continue }
            if !wallpaper { context.stroke(Path { $0.move(to: CGPoint(x: plot.minX, y: y)); $0.addLine(to: CGPoint(x: plot.maxX, y: y)) }, with: .color(gridColor), lineWidth: 1) }
            if let lastY, abs(lastY - y) < 14 { continue }
            context.draw(Text(format(value)).font(.system(size: 10).monospacedDigit()).foregroundStyle(textColor), at: CGPoint(x: plot.maxX + 6, y: y), anchor: .leading)
        }

        guard layout.firstVisible <= layout.lastVisible else { return }
        var clipped = context
        clipped.clip(to: Path(plot))
        let visible = layout.bars[layout.firstVisible...layout.lastVisible]
        let body = max(1, layout.spacing * 0.72)

        // Volume rests on the time axis, its own scale.
        let maxVolume = visible.map(\.volume).max() ?? 0
        let volumeFloor = wallpaper ? axisY - 12 : plot.maxY
        if maxVolume > 0 {
            for (offset, bar) in visible.enumerated() {
                let x = layout.x(CGFloat(layout.firstVisible + offset)), h = CGFloat(bar.volume / maxVolume) * plot.height * (wallpaper ? 0.08 : 0.14)
                clipped.fill(Path(CGRect(x: x - body / 2, y: volumeFloor - h, width: body, height: h)), with: .color(candleColor(bar).opacity(0.22)))
            }
        }

        if cloud { drawCloud(&clipped, layout: layout, dark: dark) }

        // Previous weeks first (lighter), then the current week.
        // Full history can hold hundreds of weeks: draw only those overlapping the visible span.
        let shownFrom = layout.bars[layout.firstVisible].time, shownTo = layout.bars[layout.lastVisible].time + period.seconds
        for past in history where past.end > shownFrom && past.start < shownTo {
            drawRange(&clipped, past, layout: layout, dark: dark, inner: false, faded: true)
        }
        if let range { drawRange(&clipped, range, layout: layout, dark: dark, inner: innerLines, faded: false) }

        for (offset, bar) in visible.enumerated() {
            let x = layout.x(CGFloat(layout.firstVisible + offset))
            let color = candleColor(bar)
            let top = layout.y(max(bar.open, bar.close)), bottom = layout.y(min(bar.open, bar.close))
            clipped.stroke(Path { $0.move(to: CGPoint(x: x, y: layout.y(bar.high))); $0.addLine(to: CGPoint(x: x, y: layout.y(bar.low))) }, with: .color(color), lineWidth: 1)
            clipped.fill(Path(CGRect(x: x - body / 2, y: top, width: body, height: max(1, bottom - top))), with: .color(color))
        }

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
            clipped.stroke(Path { $0.move(to: CGPoint(x: plot.minX, y: y)); $0.addLine(to: CGPoint(x: plot.maxX, y: y)) },
                           with: .color(line.color), style: StrokeStyle(lineWidth: 1.4, dash: line.dashed ? [5, 4] : []))
            clipped.draw(Text("\(line.title) \(format(line.price))").font(.system(size: 10, weight: .semibold)).foregroundStyle(line.color),
                         at: CGPoint(x: plot.minX + 6, y: y - 3), anchor: .bottomLeading)
        }

        drawMarkers(&clipped, layout: layout)

        if let last = layout.bars.last {
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
    private func drawRange(_ context: inout GraphicsContext, _ range: FibRange, layout: Layout, dark: Bool, inner: Bool, faded: Bool) {
        let plot = layout.plot
        let x1 = max(plot.minX, layout.x(layout.index(atTime: range.start))), x2 = min(plot.maxX, layout.x(layout.index(atTime: range.end)))
        guard x2 > x1 + 2 else { return }
        let alpha = faded ? 0.45 : 1
        let fib = ChartPalette.fibLine(dark: dark).opacity(alpha), monday = ChartPalette.mondayLine.opacity(alpha)
        let levels = range.levels(inner: inner)
        for level in levels {
            let y = layout.y(level.price)
            guard y >= plot.minY, y <= plot.maxY else { continue }
            let color: Color = level.kind == .upper || level.kind == .lower ? fib : level.kind == .monday ? monday : ChartPalette.reference.opacity(0.6 * alpha)
            context.stroke(Path { $0.move(to: CGPoint(x: x1, y: y)); $0.addLine(to: CGPoint(x: x2, y: y)) },
                           with: .color(color), style: StrokeStyle(lineWidth: 1, dash: level.kind == .mid || level.kind == .inner ? [4, 4] : []))
        }
        var taken: [CGFloat] = []
        for level in levels.sorted(by: { priority($0.kind) < priority($1.kind) }) where level.kind != .inner || !faded {
            let y = layout.y(level.price), labelY = max(plot.minY + 14, y - 3)
            guard y >= plot.minY, y <= plot.maxY, !taken.contains(where: { abs($0 - labelY) < 14 }) else { continue }
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

    /// Given markers plus each previous week's first touch after Monday (from the bars).
    private var allMarkers: [ChartMarker] {
        guard markHistory, !bars.isEmpty else { return markers }
        var out = markers
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

    private func candleColor(_ bar: Candle) -> Color {
        if let monday = colors.monday, memo.isNYMonday(bar.time) { return monday }
        return bar.close >= bar.open ? colors.up : colors.down
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

    private func timeLabel(_ t: Double, previous: Double?) -> String {
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
        var out = [Double?](repeating: nil, count: bars.count), window: [Double] = [], sum = 0.0
        for (i, bar) in bars.enumerated() {
            if i > 0, let step, bar.time - bars[i - 1].time != step { window.removeAll(); sum = 0 }
            window.append(bar.close); sum += bar.close
            if window.count > period { sum -= window.removeFirst() }
            if window.count >= period { out[i] = sum / Double(period) }
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
            if last.map({ bar.time - $0 != step }) ?? true { a = bar.close; b = bar.close }
            a += (bar.close - a) * 2 / 21; b += (bar.close - b) * 2 / 51; last = bar.time
            out.append((a, b))
        }
        return out
    }
}

/// Per-frame caches: indicator series while the bars are unchanged, and NY-Monday flags per bar time.
final class IndicatorMemo {
    private var key = ""
    private var averages: [String: [Double?]] = [:]
    private var cloudPoints: [(a: Double, b: Double)?] = []
    private var mondays: [Double: Bool] = [:]
    private static let ny: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/New_York")!; return c }()

    private func check(_ bars: [Candle], _ step: Double) {
        let next = "\(bars.count)|\(bars.first?.time ?? 0)|\(bars.last?.time ?? 0)|\(bars.last?.close ?? 0)|\(step)"
        if next != key { key = next; averages = [:]; cloudPoints = [] }
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
