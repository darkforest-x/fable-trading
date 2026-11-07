import Foundation
import Observation

/// 模拟盘回放 (ReplayDesk.tsx / replayData.ts): historical bars stepped by hand, simulated cash only.
/// Commands carry a request id and the session version; an unconfirmed command is retried with the
/// same id (“继续原操作”), and a version conflict re-reads the session instead of re-sending.
struct Flex: Codable, Hashable {
    let value: Double?
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let d = try? c.decode(Double.self) { value = d.isFinite ? d : nil }
        else if let s = try? c.decode(String.self) { value = Double(s.trimmingCharacters(in: .whitespaces)).flatMap { $0.isFinite ? $0 : nil } }
        else { value = nil }
    }
    func encode(to encoder: Encoder) throws { var c = encoder.singleValueContainer(); try c.encode(value) }
    var number: Double { value ?? 0 }
}

struct ReplayBar: Codable, Hashable { let time: Flex; let open: Flex; let high: Flex; let low: Flex; let close: Flex; let volume: Flex }
struct ReplayRange: Codable, Hashable { let state: String; let week: String?; let start: Double?; let end: Double?; let expires: Double?; let high: Flex?; let low: Flex?; let upper: Flex?; let lower: Flex? }
struct ReplayPosition: Codable, Hashable, Identifiable { let id: String; let side: String; let entry: Flex; let quantity: Flex; let remaining: Flex; let notional: Flex; let stop: Flex?; let target: Flex?; let opened: Double; let unrealized: Flex }
struct ReplayOrder: Codable, Hashable, Identifiable { let id: String; let side: String; let price: Flex; let notional: Flex; let status: String; let tag: String?; let expires: Double? }
struct ReplayClosed: Codable, Hashable, Identifiable { let id: String; let side: String; let entry: Flex; let exit: Flex; let opened: Double; let closed: Double; let netPnl: Flex; let fees: Flex }
struct ReplayFill: Codable, Hashable, Identifiable { let id: String; let side: String; let price: Flex; let quantity: Flex; let fee: Flex; let role: String; let time: Double; let reason: String }
struct ReplayStats: Codable, Hashable { let trades: Int; let wins: Int; let losses: Int; let winRate: Double?; let payoff: Double?; let profitFactor: Double?; let netPnl: Flex; let returnPct: Flex; let maxDrawdown: Flex; let maxDrawdownPct: Flex; let longestLossStreak: Int }
struct ReplaySnapshot: Codable {
    let id: String; let symbol: String; let period: String; let source: String
    let initialCash: Flex; let requestedStart: Double; let clock: Double; let version: Int
    let bars: [ReplayBar]; let range: ReplayRange
    let cash: Flex; let equity: Flex; let available: Flex; let fees: Flex; let price: Flex
    let positions: [ReplayPosition]; let orders: [ReplayOrder]; let closed: [ReplayClosed]; let fills: [ReplayFill]
    let statistics: ReplayStats; let notice: String?
}
struct ReplaySessionRow: Codable, Identifiable, Hashable { let id: String; let symbol: String; let period: String; let source: String; let clock: Double; let initialCash: Flex; let version: Int }
struct ReplayCatalog: Codable { struct Source: Codable, Hashable { let id: String; let label: String }; let periods: [String]; let sources: [Source]; let warning: String? }
private struct SessionList: Codable { let items: [ReplaySessionRow] }

@MainActor @Observable
final class ReplayStore {
    var catalog: ReplayCatalog?
    var sessions: [ReplaySessionRow] = []
    var snapshot: ReplaySnapshot?
    var busy = false
    var error: String?
    var notice: String?
    var playing = false
    var speed = 1
    /// Unconfirmed command (network failure): retried only with the same request id.
    private(set) var pending: [String: Any]?
    private var connection: Connection?

    func attach(_ connection: Connection?) { self.connection = connection }

    func load() async {
        guard let connection else { return }
        do {
            async let c: ReplayCatalog = API.get("/v1/replay/catalog", connection: connection)
            async let s: SessionList = API.get("/v1/replay/sessions", connection: connection)
            catalog = try await c; sessions = try await s.items; error = nil
        } catch { self.error = error.localizedDescription }
    }

    func open(_ id: String) async {
        guard let connection else { return }
        busy = true; defer { busy = false }
        do { snapshot = try await API.get("/v1/replay/sessions/\(id)", connection: connection); error = nil; pending = nil; notice = nil }
        catch { self.error = error.localizedDescription }
    }

    func create(symbol: String, period: String, source: String, start: Date, cash: String) async {
        guard let connection, !busy else { return }
        busy = true; error = nil; defer { busy = false }
        let requestId = Trading.newRequestId()
        let body: [String: Any] = ["request_id": requestId, "symbol": symbol, "period": period, "source": source, "start": Int(start.timeIntervalSince1970), "cash": cash]
        do {
            let next: ReplaySnapshot = try await API.post("/v1/replay/sessions", body: body, connection: connection)
            snapshot = next; pending = nil
            await load()
        } catch { self.error = error.localizedDescription }
    }

    func close() { playing = false; snapshot = nil; pending = nil; notice = nil; error = nil }

    func command(_ action: String, _ params: [String: Any], automated: Bool = false) async {
        guard let current = snapshot, !busy, pending == nil else { return }
        if !(action == "advance" && automated) { playing = false }
        notice = nil
        let body: [String: Any] = ["request_id": Trading.newRequestId(), "version": current.version, "action": action, "params": params]
        await post(body, automated: automated, fillsBefore: current.fills.count)
    }

    func retry() async {
        guard let body = pending, let current = snapshot else { return }
        await post(body, automated: false, fillsBefore: current.fills.count)
    }

    private func post(_ body: [String: Any], automated: Bool, fillsBefore: Int) async {
        guard let connection, let current = snapshot else { return }
        busy = true; error = nil; pending = body
        defer { busy = false }
        do {
            let next: ReplaySnapshot = try await API.post("/v1/replay/sessions/\(current.id)/commands", body: body, connection: connection)
            pending = nil
            let newFill = automated && next.fills.count > fillsBefore
            if newFill { playing = false }
            snapshot = next
            let action = body["action"] as? String ?? ""
            notice = next.notice?.trimmingCharacters(in: .whitespaces).nilIfEmpty ?? (newFill ? "本批出现成交，回放已暂停。" : action == "advance" ? nil : "\(Self.label(action))已完成")
            if newFill || action != "advance" { Haptics.success() }
        } catch let failure as APIError where failure.status == 409 {
            playing = false; pending = nil
            await open(current.id)
            error = failure.message.contains("另一处推进") ? nil : failure.message
            if failure.message.contains("另一处推进") { notice = "进度版本已变化，已重新读取回放。原操作未重复提交。" }
        } catch let failure as APIError where failure.status > 0 && failure.status < 500 {
            playing = false; pending = nil; error = failure.message; Haptics.warning()
        } catch {
            playing = false
            self.error = "操作结果尚未确认。点击“继续原操作”会沿用本次委托，避免重复开单。"
        }
    }

    static func label(_ action: String) -> String {
        ["order": "下单", "close": "平仓", "cancel": "撤单", "monday": "一键 1.618 挂单", "advance": "前进"][action] ?? "操作"
    }

    /// Bars at or before the replay clock (replayBarsForChart).
    var candles: [Candle] {
        guard let s = snapshot else { return [] }
        return s.bars.compactMap { b in
            guard let t = b.time.value, t <= s.clock, let o = b.open.value, let h = b.high.value, let l = b.low.value, let c = b.close.value else { return nil }
            return Candle(time: t, open: o, high: h, low: l, close: c, volume: b.volume.value ?? 0, confirmed: true)
        }
        .sorted { $0.time < $1.time }
    }

    var range: FibRange? {
        guard let r = snapshot?.range, r.state == "ready", let h = r.high?.value, let l = r.low?.value, let u = r.upper?.value, let d = r.lower?.value,
              let start = r.start, let end = r.end else { return nil }
        return FibRange(high: h, low: l, upper: u, lower: d, start: start, end: end)
    }
}

extension String { var nilIfEmpty: String? { isEmpty ? nil : self } }

enum ReplayFormat {
    static func value(_ v: Flex?, _ digits: Int = 2) -> String { Fmt.amount(v?.value, digits) }
    static func price(_ v: Flex?) -> String {
        guard let n = v?.value else { return "—" }
        let digits = min(8, max(2, 6 - Int(floor(log10(abs(n) == 0 ? 1 : abs(n))))))
        return Fmt.amount(n, digits)
    }
    static func time(_ seconds: Double?) -> String {
        guard let seconds, seconds > 0 else { return "—" }
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd HH:mm"
        return f.string(from: Date(timeIntervalSince1970: seconds))
    }
    static func status(_ s: String) -> String {
        ["open": "待成交", "pending": "待成交", "submitted": "处理中", "filled": "已成交", "canceled": "已撤销", "cancelled": "已撤销", "expired": "已过期", "rejected": "已拒绝"][s] ?? "已结束"
    }
    static func reason(_ r: String) -> String { ["entry": "开仓", "manual": "手动平仓", "manual_close": "手动平仓", "stop": "止损触发", "target": "止盈触发"][r] ?? "成交" }
    static func symbol(_ input: String) throws -> String {
        let v = input.trimmingCharacters(in: .whitespaces).uppercased()
        guard !v.isEmpty else { throw APIError(status: 0, message: "请输入合约代码") }
        let base = v.hasSuffix("-USDT-SWAP") ? String(v.dropLast(10)) : v.hasSuffix("-USDT") ? String(v.dropLast(5)) : v
        guard base.range(of: "^[A-Z0-9]+$", options: .regularExpression) != nil else { throw APIError(status: 0, message: "合约代码仅支持字母和数字，例如 BTC-USDT-SWAP") }
        return base + "-USDT-SWAP"
    }
}
