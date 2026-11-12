import Foundation

/// Display estimates and local field guidance, ported one-to-one from the tested 1.x TypeScript
/// (ticketEstimate.ts, ticketValidation.ts, entryFlow.ts, entryAvailability.ts, positionAmount.ts,
/// tradeExperience.ts, contractRanking.ts). The server recomputes and enforces every trading rule.
enum Trading {
    static func num(_ s: String?) -> Double { Double(s ?? "") ?? .nan }
    static func isMajor(_ symbol: String) -> Bool { symbol == "BTC-USDT-SWAP" || symbol == "ETH-USDT-SWAP" }
    /// Exposure sizing is configured only for the three independent pools (BTC, ETH, alts).
    /// Missing, legacy, unknown, and shared-pool policies keep the established margin behavior.
    static func usesExposureSizing(_ policy: Policy?) -> Bool {
        policy?.entrySizing == "exposure" && policy?.majorAllowanceScope == "independent"
    }

    private static func pool(_ policy: Policy?, _ symbol: String, exposure: Bool = false) -> String {
        if policy?.majorAllowanceScope == "independent" { return isMajor(symbol) ? symbol : "alts" }
        return exposure ? "portfolio" : (isMajor(symbol) ? "majors" : "alts")
    }

    /// The slider spends a fraction of the remaining allowed margin, not equity.
    static func allocatedMargin(room: Double, percent: Double) -> Double {
        guard room.isFinite, percent.isFinite, room > 0 else { return 0 }
        return floor(room * min(100, max(0, percent)) / 100 * 1e8) / 1e8
    }

    static func marginRoom(_ state: AccountState?, symbol: String) -> Double {
        guard let state else { return 0 }
        let cap = num(isMajor(symbol) ? state.policy?.majorMarginFraction : state.policy?.altMarginFraction)
        let equity = num(state.equity)
        guard cap > 0, equity > 0 else { return 0 }
        var used = 0.0
        for p in state.positions where pool(state.policy, p.symbol) == pool(state.policy, symbol) {
            used += max(num(p.entry), num(state.quotes[p.symbol]?.last ?? p.entry)) * num(p.remaining) / Double(p.leverage)
        }
        for p in state.pending where pool(state.policy, p.symbol) == pool(state.policy, symbol) { used += num(p.plan.margin) }
        return max(0, cap - used / equity)
    }

    /// Remaining raw notional allowance for the selected independent pool, in quote currency.
    /// This is a display estimate; preview and order submission remain server-authoritative.
    static func exposureRoom(_ state: AccountState?, symbol: String) -> Double {
        guard let state, let policy = state.policy, policy.majorAllowanceScope == "independent" else { return 0 }
        let equity = num(state.equity)
        let multiple = num(isMajor(symbol) ? policy.majorExposureMultiple : policy.altExposureMultiple)
        guard equity.isFinite, equity > 0, multiple.isFinite, multiple > 0 else { return 0 }
        let selectedPool = pool(policy, symbol, exposure: true)
        var used = 0.0
        for position in state.positions where pool(policy, position.symbol, exposure: true) == selectedPool {
            let quantity = num(position.remaining)
            let quote = num(state.quotes[position.symbol]?.last)
            let mark = quote.isFinite && quote > 0 ? quote : num(position.entry)
            if quantity.isFinite, quantity > 0, mark.isFinite, mark > 0 { used += quantity * mark }
        }
        for pending in state.pending where pool(policy, pending.symbol, exposure: true) == selectedPool {
            let planned = num(pending.plan.quantity)
            let filled = num(pending.filledQuantity)
            let quantity = max(0, planned - (filled.isFinite ? filled : 0))
            let entry = num(pending.plan.entry)
            let quote = num(state.quotes[pending.symbol]?.last)
            let mark = quote.isFinite && quote > 0 ? max(quote, entry) : entry
            if quantity.isFinite, mark.isFinite, mark > 0 { used += quantity * mark }
        }
        let room = equity * multiple - used
        return room.isFinite ? max(0, room) : 0
    }

    /// Amount the current ticket's slider can allocate from its active sizing pool.
    static func allocationRoom(_ state: AccountState?, symbol: String) -> Double {
        guard let state else { return 0 }
        return usesExposureSizing(state.policy) ? exposureRoom(state, symbol: symbol) : marginRoom(state, symbol: symbol)
    }

    private static func availableFunds(_ state: AccountState, equity: Double) -> Double {
        let reported = num(state.available)
        var reserved = 0.0
        for position in state.positions {
            let quantity = num(position.quantity), remaining = num(position.remaining), margin = num(position.margin)
            if quantity.isFinite, quantity > 0, remaining.isFinite, remaining > 0, margin.isFinite, margin > 0 {
                reserved += margin * min(1, remaining / quantity)
            }
        }
        for pending in state.pending {
            let margin = num(pending.plan.margin)
            if margin.isFinite, margin > 0 { reserved += margin }
        }
        let ledgerAvailable = max(0, equity - reserved)
        // LiveBook takes the smaller of exchange-reported availability and the
        // App ledger's unreserved equity so locally estimated size stays bounded
        // by the same collateral ceiling as server preview.
        return reported.isFinite && reported >= 0 ? min(ledgerAvailable, reported) : ledgerAvailable
    }

    struct Estimate: Equatable { let quantity: Double; let notional: Double; let margin: Double; let risk: Double; let limited: Bool }

    static func estimate(_ state: AccountState?, _ t: Ticket) -> Estimate? {
        guard let state, let policy = state.policy, let q = state.quotes[t.symbol], let instrument = state.instruments[t.symbol] else { return nil }
        let equity = num(state.equity), direction: Double = t.side == "long" ? 1 : -1
        let price = t.kind == "limit" ? num(t.price) : num(direction == 1 ? q.ask : q.bid) * (1 + direction * num(policy.slippageRate))
        let stop = num(t.stop), distance = (price - stop) * direction
        guard equity > 0, price > 0, stop > 0, distance > 0, t.leverage.isFinite, t.leverage > 0 else { return nil }
        let cost = price * (2 * num(policy.feeRate) + num(policy.slippageRate))
        let exposureMode = usesExposureSizing(policy) && t.sizingMode == "exposure"
        let requestFraction = num(t.fraction)
        let requested: Double
        if exposureMode {
            requested = exposureRoom(state, symbol: t.symbol) * min(1, max(0, requestFraction)) / price
        } else {
            requested = equity * min(requestFraction, marginRoom(state, symbol: t.symbol)) * t.leverage / price
        }
        func cap(_ s: String) -> Double { num(isMajor(s) ? policy.majorExposureMultiple : policy.altExposureMultiple) }
        var used = 0.0
        for p in state.positions where pool(policy, p.symbol, exposure: true) == pool(policy, t.symbol, exposure: true) {
            used += num(state.quotes[p.symbol]?.last ?? p.entry) * num(p.remaining) / cap(p.symbol)
        }
        for p in state.pending where pool(policy, p.symbol, exposure: true) == pool(policy, t.symbol, exposure: true) {
            let entry = num(p.plan.entry), mark = num(state.quotes[p.symbol]?.last ?? p.plan.entry)
            used += num(p.plan.quantity) * (max(mark, entry) / cap(p.symbol) + entry * num(policy.feeRate) + max(0, (entry - mark) * (p.side == "long" ? 1 : -1)))
        }
        let exposure = max(0, equity - used) / (max(price, num(q.last)) / cap(t.symbol) + price * num(policy.feeRate) + max(0, (price - num(q.last)) * direction))
        var remainingRisk = 0.0
        var isAddOn = false
        if let e = state.positions.first(where: { $0.symbol == t.symbol && $0.side == t.side }) {
            let remaining = num(e.remaining), entry = num(e.entry), stop = num(e.stop)
            let signedDistance = (entry - stop) * direction
            let currentCost = entry * (2 * num(policy.feeRate) + num(policy.slippageRate))
            let currentRisk = remaining * (signedDistance + currentCost)
            remainingRisk = currentRisk.isFinite ? max(0, currentRisk) : .infinity
            isAddOn = true
        }
        let remainingBudget = max(0, equity * num(policy.riskFraction) - remainingRisk)
        let immediateLoss = max(0, (price - num(q.last)) * direction)
        let budgetPerUnit = distance + cost + (isAddOn ? (price * num(policy.feeRate) + immediateLoss) * num(policy.riskFraction) : 0)
        let riskLimit = budgetPerUnit > 0 ? remainingBudget / budgetPerUnit : 0
        let step = num(instrument.step)
        let funds = availableFunds(state, equity: equity)
        let availablePerUnit = price / t.leverage + cost
        let availableQuantity = exposureMode && availablePerUnit > 0 ? funds / availablePerUnit : .infinity
        let quantity = floor(min(min(requested, riskLimit), min(exposure, availableQuantity)) / step) * step
        guard quantity.isFinite, quantity >= 0 else { return nil }
        return Estimate(quantity: quantity, notional: quantity * price, margin: quantity * price / t.leverage,
                        risk: quantity * (distance + cost) + remainingRisk, limited: quantity < requested - step / 2)
    }

    struct FieldIssue: Equatable { enum Field: String { case price, stop, targets, sizing, leverage }; let field: Field; let message: String }

    static func issue(forMessage message: String) -> FieldIssue? {
        if message.contains("止盈") { return FieldIssue(field: .targets, message: message) }
        if message.contains("止损") { return FieldIssue(field: .stop, message: message) }
        if message.contains("委托价格") || message.contains("限价") { return FieldIssue(field: .price, message: message) }
        if message.contains("杠杆") { return FieldIssue(field: .leverage, message: message) }
        if message.contains("数量") || message.contains("仓位额度") || message.contains("保证金比例") { return FieldIssue(field: .sizing, message: message) }
        return nil
    }

    static func fieldIssue(_ t: Ticket, state: AccountState?, skipStop: Bool = false) -> FieldIssue? {
        let instrument = state?.instruments[t.symbol], tick = num(instrument?.tick)
        func offTick(_ v: String) -> Bool { tick > 0 && abs(num(v) / tick - (num(v) / tick).rounded()) > 1e-6 }
        func invalid(_ v: String) -> Bool { v.trimmingCharacters(in: .whitespaces).isEmpty || !num(v).isFinite || num(v) <= 0 }
        if t.kind == "limit" {
            if invalid(t.price) { return FieldIssue(field: .price, message: "请输入有效的委托价格") }
            if offTick(t.price) { return FieldIssue(field: .price, message: "价格需为 \(instrument?.tick ?? "") 的整数倍") }
        }
        if !skipStop {
            if invalid(t.stop) { return FieldIssue(field: .stop, message: "请输入有效的止损价格") }
            if offTick(t.stop) { return FieldIssue(field: .stop, message: "止损需为 \(instrument?.tick ?? "") 的整数倍") }
            let q = state?.quotes[t.symbol]
            let entry = num(t.kind == "limit" ? t.price : (t.side == "long" ? q?.ask : q?.bid))
            if entry > 0, (entry - num(t.stop)) * (t.side == "long" ? 1 : -1) <= 0 {
                return FieldIssue(field: .stop, message: t.side == "long" ? "多单止损应低于入场价" : "空单止损应高于入场价")
            }
            if let targets = t.takeProfits {
                var prior = entry
                for (index, target) in targets.enumerated() {
                    guard let target else { continue }
                    if invalid(target) || offTick(target) {
                        return FieldIssue(field: .targets, message: "止盈 \(index + 1) 请输入符合最小价格步长的正数")
                    }
                    if prior > 0, (num(target) - prior) * (t.side == "long" ? 1 : -1) <= 0 {
                        return FieldIssue(field: .targets, message: t.side == "long" ? "多单止盈需高于入场价，逐档升高" : "空单止盈需低于入场价，逐档降低")
                    }
                    prior = num(target)
                }
            }
        }
        if !(num(t.fraction) > 0) { return FieldIssue(field: .sizing, message: "请选择大于 0 的仓位额度") }
        if t.leverage < 1 { return FieldIssue(field: .leverage, message: "请选择有效杠杆") }
        if !skipStop, let e = estimate(state, t), e.quantity < num(instrument?.minimum ?? instrument?.step) {
            return FieldIssue(field: .sizing, message: "低于合约最小委托量，请增加额度")
        }
        return nil
    }

    struct ErrorCopy { let title: String; let detail: String; let rules: Bool }
    static func errorCopy(_ message: String) -> ErrorCopy {
        if message.contains("请填写初始止损") { return ErrorCopy(title: "请填写初始止损", detail: "先填写止损价格，服务端才能根据止损距离计算仓位与计划风险。", rules: false) }
        if message.contains("杠杆过高") { return ErrorCopy(title: "杠杆与止损不匹配", detail: message, rules: true) }
        if message.contains("止损") { return ErrorCopy(title: "请检查止损价格", detail: message, rules: false) }
        if message.contains("委托价格") { return ErrorCopy(title: "请检查委托价格", detail: message, rules: false) }
        let rules = ["规则", "上限", "冷静", "停手"].contains { message.contains($0) }
        return ErrorCopy(title: "本次操作未完成", detail: message.isEmpty ? "服务暂时未响应，请稍后重试。" : message, rules: rules)
    }

    // entryFlow.ts
    static func entryResolved(_ status: String) -> Bool { ["pending", "partially_filled", "filled", "closed", "rejected", "cancelled", "interrupted"].contains(status) }
    static func entryPollDelay(_ status: String) -> Duration { status == "not_found" ? .seconds(3) : .seconds(1) }

    /// Decimal-string rounding to the instrument tick, without floating-point drift.
    static func priceAtTick(_ value: String?, tick: String?) -> String? {
        func parse(_ raw: String?) -> (units: Decimal, scale: Int)? {
            guard let raw, raw.count <= 32, raw.range(of: #"^\d*\.?\d+$"#, options: .regularExpression) != nil else { return nil }
            let parts = raw.split(separator: ".", omittingEmptySubsequences: false)
            let whole = parts.first.map(String.init) ?? "", frac = parts.count > 1 ? String(parts[1]) : ""
            guard let units = Decimal(string: (whole.isEmpty ? "0" : whole) + frac) else { return nil }
            return (units, frac.count)
        }
        guard let p = parse(value), let t = parse(tick), p.units > 0, t.units > 0 else { return nil }
        let scale = max(p.scale, t.scale)
        func pow10(_ n: Int) -> Decimal { var r: Decimal = 1; for _ in 0..<n { r *= 10 }; return r }
        let pv = p.units * pow10(scale - p.scale), tv = t.units * pow10(scale - t.scale)
        var q = (pv + tv / 2) / tv, rounded = Decimal()
        NSDecimalRound(&rounded, &q, 0, .down)
        let units = rounded * tv
        guard units > 0 else { return nil }
        var digits = NSDecimalNumber(decimal: units).stringValue
        if scale == 0 { return digits }
        while digits.count < scale + 1 { digits = "0" + digits }
        let head = digits.dropLast(scale), tail = digits.suffix(scale)
        var text = "\(head).\(tail)"
        while text.hasSuffix("0") { text.removeLast() }
        if text.hasSuffix(".") { text.removeLast() }
        return text
    }

    // entryAvailability.ts
    enum Destination { case settings, positions, discipline }
    struct Block: Equatable { let text: String; let destination: Destination }
    static func availability(state: AccountState?, symbol: String, connected: Bool, online: Bool, fresh: Bool, pending: Bool, room: Double, now: Double) -> Block? {
        if !connected { return Block(text: "正在连接纪律服务", destination: .settings) }
        if !online { return Block(text: "连接中断 · 正在重连", destination: .settings) }
        if pending { return Block(text: "正在核对上一笔请求", destination: .positions) }
        if let state, state.cooldownUntil > now { return Block(text: "冷静期剩余 \(countdown(state.cooldownUntil - now))", destination: .discipline) }
        if let block = state?.blockers.first {
            let positions = ["委托", "持仓", "保护", "交易所请求"].contains { block.contains($0) }
            return Block(text: block, destination: positions ? .positions : .discipline)
        }
        guard let state, let policy = state.policy else { return Block(text: "等待账户规则", destination: .discipline) }
        if !fresh { return Block(text: "行情更新中 · 暂停开仓", destination: .settings) }
        if state.pending.contains(where: { $0.symbol == symbol }) { return Block(text: "该币种已有挂单，成交或撤单后可继续", destination: .positions) }
        if room <= 0 {
            let name = isMajor(symbol) ? (policy.majorAllowanceScope == "independent" ? Fmt.coin(symbol) : "BTC/ETH") : "山寨币"
            let allowance = usesExposureSizing(policy) ? "名义额度" : "保证金额度"
            return Block(text: "\(name)可用\(allowance)已用完", destination: .positions)
        }
        return nil
    }

    static func countdown(_ seconds: Double) -> String {
        let s = max(0, Int(ceil(seconds)))
        return String(format: "%02d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
    }

    // positionAmount.ts
    static func positionAmount(remaining: String, entry: String, quote: Quote?, online: Bool, serverTime: Double, maxAge: Double = 15) -> (value: Double?, caption: String) {
        let quantity = num(remaining)
        let quoted = num(quote?.last) > 0
        let price = quoted ? num(quote?.last) : (num(entry) > 0 ? num(entry) : .nan)
        let amount = quantity.isFinite && quantity >= 0 ? quantity * price : .nan
        let fresh = quoted && online && (serverTime - (quote?.time ?? -1)) >= 0 && serverTime - (quote?.time ?? 0) <= maxAge
        return (amount.isFinite ? amount : nil, quoted ? (fresh ? "按最新价格估算" : "按上次报价估算") : "按开仓均价估算")
    }

    static func isRequestId(_ s: String) -> Bool { s.range(of: #"^[a-zA-Z0-9]{16,80}$"#, options: .regularExpression) != nil }
    static func newRequestId() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }

    // contractRanking.ts
    enum ContractSort: String, CaseIterable { case volume, gainers, losers }
    static func dayChange(_ q: Quote, now: Double) -> Double? {
        guard let raw = q.todayChange, let v = Double(raw), v.isFinite, now - q.time >= 0, now - q.time <= 15,
              floor((now + 28800) / 86400) == floor((q.time + 28800) / 86400) else { return nil }
        return v
    }
    static func turnover(_ q: Quote?) -> Double? {
        guard let raw = q?.volume24hUsdt?.trimmingCharacters(in: .whitespaces), !raw.isEmpty, let v = Double(raw), v.isFinite, v >= 0 else { return nil }
        return v
    }
    static func rank(_ symbols: [String], quotes: [String: Quote], now: Double, sort: ContractSort) -> [String] {
        let metric: [String: Double?] = Dictionary(symbols.map { s in
            (s, sort == .volume ? turnover(quotes[s]) : quotes[s].flatMap { dayChange($0, now: now) })
        }, uniquingKeysWith: { first, _ in first })
        return symbols.sorted { a, b in
            let x = metric[a] ?? nil, y = metric[b] ?? nil
            switch (x, y) {
            case (nil, nil): return a < b
            case (nil, _): return false
            case (_, nil): return true
            case let (x?, y?): return x == y ? a < b : (sort == .losers ? x < y : x > y)
            }
        }
    }
    static func turnoverLabel(_ value: Double?) -> String {
        guard let value else { return "24h —" }
        let (unit, div) = value >= 1e8 ? ("亿", 1e8) : value >= 1e4 ? ("万", 1e4) : ("", 1)
        return "24h ≈ \((value / div).formatted(.number.precision(.fractionLength(0...(unit.isEmpty ? 0 : 1)))))\(unit) U"
    }
}
