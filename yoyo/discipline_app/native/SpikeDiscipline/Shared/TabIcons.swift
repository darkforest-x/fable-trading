import SwiftUI

/// Custom tab icons (owner 2026-11-07 “只需要图标 · 图标都需要重新设计”), drawn on a 24-pt grid with
/// 2-pt rounded strokes; the active icon also fills its key shape. Stitch native-2.2.0 brief:
/// 合约 candlestick + order arrow · 监控 radar sweep · 1.618 levels + golden arc · 持仓 shield + check · 统计 bars + trend.
struct TabGlyph: View {
    let tab: AppTab
    let active: Bool
    var body: some View {
        Canvas { context, size in
            let s = min(size.width, size.height) / 24
            func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x * s, y: y * s) }
            let stroke = StrokeStyle(lineWidth: 2 * s, lineCap: .round, lineJoin: .round)
            let ink = GraphicsContext.Shading.color(active ? Color.accentColor : Color.primary.opacity(0.62))
            let fill = GraphicsContext.Shading.color(active ? Color.accentColor : .clear)
            let soft = GraphicsContext.Shading.color(active ? Color.accentColor.opacity(0.22) : .clear)
            switch tab {
            case .trade:
                // Two candles and an up-right order arrow.
                var wick = Path(); wick.move(to: p(7, 3)); wick.addLine(to: p(7, 21)); wick.move(to: p(14, 7)); wick.addLine(to: p(14, 19))
                context.stroke(wick, with: ink, style: stroke)
                let a = Path(roundedRect: CGRect(x: 4.5 * s, y: 7 * s, width: 5 * s, height: 9 * s), cornerRadius: 1.4 * s)
                let b = Path(roundedRect: CGRect(x: 11.5 * s, y: 10 * s, width: 5 * s, height: 6 * s), cornerRadius: 1.4 * s)
                context.fill(a, with: fill); context.stroke(a, with: ink, style: stroke)
                context.fill(b, with: soft); context.stroke(b, with: ink, style: stroke)
                var arrow = Path(); arrow.move(to: p(17.5, 6.5)); arrow.addLine(to: p(21, 3)); arrow.move(to: p(17.8, 3)); arrow.addLine(to: p(21, 3)); arrow.addLine(to: p(21, 6.2))
                context.stroke(arrow, with: ink, style: stroke)
            case .monitor:
                // Radar: outer ring, inner ring, sweep wedge, blip.
                let outer = Path(ellipseIn: CGRect(x: 3 * s, y: 3 * s, width: 18 * s, height: 18 * s))
                context.stroke(outer, with: ink, style: stroke)
                var inner = Path(); inner.addArc(center: p(12, 12), radius: 4.6 * s, startAngle: .degrees(200), endAngle: .degrees(520), clockwise: false)
                context.stroke(inner, with: ink, style: StrokeStyle(lineWidth: 1.6 * s, lineCap: .round))
                var wedge = Path(); wedge.move(to: p(12, 12)); wedge.addArc(center: p(12, 12), radius: 9 * s, startAngle: .degrees(-90), endAngle: .degrees(-30), clockwise: false); wedge.closeSubpath()
                context.fill(wedge, with: active ? soft : .color(Color.primary.opacity(0.12)))
                var hand = Path(); hand.move(to: p(12, 12)); hand.addLine(to: p(12, 3.6)); context.stroke(hand, with: ink, style: stroke)
                context.fill(Path(ellipseIn: CGRect(x: 15.4 * s, y: 6.4 * s, width: 3.2 * s, height: 3.2 * s)), with: active ? fill : ink)
            case .fib:
                // Range levels with a golden arc running from the low level to the upper extension.
                var levels = Path()
                levels.move(to: p(3, 5)); levels.addLine(to: p(21, 5))
                levels.move(to: p(3, 19)); levels.addLine(to: p(21, 19))
                context.stroke(levels, with: ink, style: stroke)
                var mid = Path(); mid.move(to: p(3, 12)); mid.addLine(to: p(21, 12))
                context.stroke(mid, with: ink, style: StrokeStyle(lineWidth: 1.6 * s, lineCap: .round, dash: [2 * s, 2.6 * s]))
                var arc = Path(); arc.move(to: p(5, 19)); arc.addCurve(to: p(19, 5), control1: p(13, 19), control2: p(10, 7))
                context.stroke(arc, with: active ? fill : ink, style: StrokeStyle(lineWidth: 2.2 * s, lineCap: .round))
                context.fill(Path(ellipseIn: CGRect(x: 17.2 * s, y: 3.2 * s, width: 3.6 * s, height: 3.6 * s)), with: active ? fill : ink)
            case .positions:
                // Shield with a check.
                var shield = Path()
                shield.move(to: p(12, 2.8)); shield.addLine(to: p(19.5, 5.6)); shield.addLine(to: p(19.5, 11.4))
                shield.addCurve(to: p(12, 21.2), control1: p(19.5, 16.2), control2: p(16.2, 19.6))
                shield.addCurve(to: p(4.5, 11.4), control1: p(7.8, 19.6), control2: p(4.5, 16.2))
                shield.addLine(to: p(4.5, 5.6)); shield.closeSubpath()
                context.fill(shield, with: soft); context.stroke(shield, with: ink, style: stroke)
                var check = Path(); check.move(to: p(8.6, 12.2)); check.addLine(to: p(11.2, 14.8)); check.addLine(to: p(15.6, 9.6))
                context.stroke(check, with: ink, style: StrokeStyle(lineWidth: 2.2 * s, lineCap: .round, lineJoin: .round))
            case .stats:
                // Rising bars with a trend line.
                for (i, h) in [5.0, 8.0, 12.0].enumerated() {
                    let x = 4.5 + Double(i) * 6
                    let bar = Path(roundedRect: CGRect(x: x * s, y: (20 - h) * s, width: 3.6 * s, height: h * s), cornerRadius: 1.2 * s)
                    context.fill(bar, with: i == 2 ? fill : soft); context.stroke(bar, with: ink, style: StrokeStyle(lineWidth: 1.8 * s, lineJoin: .round))
                }
                var trend = Path(); trend.move(to: p(3.5, 11)); trend.addLine(to: p(9, 7.5)); trend.addLine(to: p(13.5, 9)); trend.addLine(to: p(20.5, 3.5))
                context.stroke(trend, with: ink, style: stroke)
            }
        }
        .frame(width: 26, height: 26)
        .scaleEffect(active ? 1.08 : 1)
        .animation(.spring(response: 0.35, dampingFraction: 0.7), value: active)
    }
}
