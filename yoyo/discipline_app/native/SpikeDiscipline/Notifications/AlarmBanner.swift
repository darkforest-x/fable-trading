import SwiftUI

/// In-app alert banner in tinted glass. Alarms stay until dismissed; 1.618 pushes fade after 7 s.
/// “查看” only navigates; it never opens an order ticket.
struct AlarmBanner: View {
    let event: NotificationEvent
    let now: Double
    let onView: () -> Void
    let onDismiss: () -> Void
    @State private var pulse = false
    private var alarm: Bool { event.kind == "alarm" }

    private var monthly: Bool { event.kind == MonthTouchStyle.kind }
    private var tint: Color { monthly ? MonthTouchStyle.tint : (alarm ? .down : .blue) }
    private var title: String {
        if alarm { return "全市场插针 · \(Fmt.edge(event.side))" }
        return (monthly ? "月首周 1.618" : "周一 1.618") + " · " + MonthTouchStyle.edge(event.side)
    }
    private var detail: String {
        let who = alarm ? "\(event.n ?? 0) 个币触及" : Fmt.coin(event.symbol ?? "")
        return who + " · " + Fmt.clock(event.atMs, now: now)
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: monthly ? MonthTouchStyle.icon : alarm ? "bell.and.waves.left.and.right.fill" : "chart.line.uptrend.xyaxis")
                .font(.scaled(size: 17, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 40, height: 40)
                .background(tint.opacity(0.14), in: .circle)
                .scaleEffect(alarm && pulse ? 1.08 : 1)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
                Text(detail)
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 4)
            Button("查看", action: onView).buttonStyle(.pill(.solid(.accentColor), height: 30))
            Button(action: onDismiss) { Image(systemName: "xmark").font(.caption.weight(.bold)) }
                .buttonStyle(.pill(.soft, height: 30)).accessibilityLabel(alarm ? "知道了" : "关闭提醒")
        }
        .padding(.leading, 12).padding(.trailing, 10).padding(.vertical, 10)
        .frosted(RoundedRectangle(cornerRadius: 24, style: .continuous), veil: 0.85)
        .padding(.horizontal, 10)
        .onAppear { if alarm { withAnimation(.easeInOut(duration: 0.9).repeatForever()) { pulse = true } } }
    }
}
