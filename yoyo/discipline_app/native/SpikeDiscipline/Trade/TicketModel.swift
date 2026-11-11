import Foundation
import Observation

/// Shared order ticket for 合约 and the 1.618 快捷开单 sheet (1.x App.tsx ticket state + useAutoStop.ts).
/// The phone sends intent only (symbol, side, kind, price, stop, allocation fraction, leverage);
/// the server re-sizes and enforces every rule.
@MainActor @Observable
final class TicketModel {
    var symbol: String = UserDefaults.standard.string(forKey: "ticket.symbol") ?? "BTC-USDT-SWAP" { didSet { UserDefaults.standard.set(symbol, forKey: "ticket.symbol") } }
    var direction = "long"
    var kind = "market"
    var price = ""
    var stop = ""
    var allocation: Double = 100
    var leverage = 20.0
    var fieldIssue: Trading.FieldIssue?
    var stopApplied: String?               // "<context>|<value>" when an automatic stop was filled in
    var fibTicket = false                  // 1.618 快捷开单 sheet is open
    var fibPeriod: CandlePeriod = .m15

    // Auto-stop (useAutoStop.ts): mode = side being calculated; ready once the value for this context lands.
    var autoMode: String?
    var autoWorking = false
    var autoCalculated = ""
    var autoRevision = 0

    // Review / confirm.
    struct Pending: Identifiable { let id: String; let ticket: Ticket; let plan: Plan?; let policyRevision: Int; let previewId: String? }
    var preview: Pending?
    var reviewing = false
    var error: String?

    // MARK: derived

    func existing(_ state: AccountState?) -> Position? { state?.positions.first { $0.symbol == symbol } }
    func side(_ state: AccountState?) -> String {
        if let e = existing(state) { return e.side == "short" ? "short" : "long" }
        return direction
    }
    func cap(_ state: AccountState?) -> Int {
        let p = state?.policy
        if Trading.usesExposureSizing(p) {
            // Major exposure mode always targets the fixed 100x policy. If the instrument
            // cannot support it, the server rejects preflight instead of silently lowering it.
            return Trading.isMajor(symbol) ? 100 : max(1, state?.instruments[symbol]?.maxLeverage ?? 1)
        }
        let policyCap = Trading.isMajor(symbol) ? (p?.majorLeverage ?? 100) : (p?.altLeverage ?? 20)
        return min(policyCap, state?.instruments[symbol]?.maxLeverage ?? policyCap)
    }
    func sizingMode(_ state: AccountState?) -> String { Trading.usesExposureSizing(state?.policy) ? "exposure" : "margin" }
    func leverageLabel(_ state: AccountState?) -> String {
        if let current = existing(state) { return "沿用 \(Fmt.leverage(current.leverage))x" }
        if let shared = state?.sharedLeverage?[symbol] { return "沿用 \(Fmt.leverage(shared))x" }
        if Trading.isMajor(symbol) { return "交易所 100x" }
        guard let maxLeverage = state?.instruments[symbol]?.maxLeverage else { return "最高 —" }
        return "最高 \(Fmt.leverage(Double(maxLeverage)))x"
    }
    func fraction(_ state: AccountState?) -> String {
        if sizingMode(state) == "exposure" {
            let percent = allocation.isFinite ? min(100, max(0, allocation)) : 0
            return String(percent / 100)
        }
        let value = Trading.allocatedMargin(room: Trading.marginRoom(state, symbol: symbol), percent: allocation)
        return value.formatted(.number.precision(.fractionLength(0...8)).grouping(.never).locale(Locale(identifier: "en_US")))
    }
    func ticket(_ state: AccountState?, side: String? = nil, stop: String? = nil) -> Ticket {
        Ticket(symbol: symbol, side: side ?? self.side(state), kind: kind, price: price.trimmingCharacters(in: .whitespaces),
               stop: (stop ?? self.stop).trimmingCharacters(in: .whitespaces), fraction: fraction(state), leverage: leverage,
               sizingMode: sizingMode(state))
    }
    /// Context of an automatic stop: any change invalidates a computed value.
    func stopContext(_ state: AccountState?, connection: Connection?) -> String {
        [connection?.accountId ?? "", connection?.mode ?? "", symbol, side(state), kind, price, "\(Int(allocation))", "\(leverage)",
         "\(state?.policyRevision ?? 0)", fibTicket ? fibPeriod.rawValue : "-"].joined(separator: "|")
    }
    func autoReady(_ context: String, enabled: Bool) -> Bool { autoMode == nil || (enabled && autoCalculated == context + ":" + (autoMode ?? "")) }

    // MARK: actions

    /// Switching contract clears price and stop (1.x choose()).
    func choose(_ next: String) {
        manual()
        symbol = next; allocation = 100; price = ""; stop = ""; fieldIssue = nil; stopApplied = nil
    }
    func manual() { autoMode = nil; autoWorking = false; autoCalculated = "" }
    func selectAuto(_ side: String) { autoCalculated = ""; autoWorking = true; autoMode = side; autoRevision += 1 }

    /// Keep leverage within the cap; inherit the exchange's leverage or the open position's.
    func syncLeverage(_ state: AccountState?) {
        let cap = cap(state)
        leverage = existing(state)?.leverage ?? state?.sharedLeverage?[symbol] ?? Double(cap)
        if leverage > Double(cap) { leverage = Double(cap) }
    }
    func syncExisting(_ state: AccountState?) {
        guard let e = existing(state) else { return }
        stop = e.stop; leverage = e.leverage; direction = e.side == "short" ? "short" : "long"
    }

    /// 1.618 快捷开单: market, side from the latest touch (upper → short), wick stop for the chart period.
    func openQuick(_ row: FibContract, period: CandlePeriod, state: AccountState?) {
        choose(row.symbol)
        fibPeriod = period; kind = "market"
        direction = row.latestTouch?.side == .upper ? "short" : "long"
        fibTicket = true
        syncLeverage(state)
        if existing(state) == nil { selectAuto(direction) } else { syncExisting(state) }
    }
}
