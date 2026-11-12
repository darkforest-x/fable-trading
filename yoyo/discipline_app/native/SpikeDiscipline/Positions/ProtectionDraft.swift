import Foundation

/// A null target leaves the corresponding exchange order untouched. Compare decimals so a
/// formatting-only edit cannot produce an amendment on a different leg.
struct ProtectionDraft {
    var stop: String
    var targets: [String]

    init(_ position: Position) {
        stop = position.stop
        targets = (position.takeProfitPrices ?? []).map { $0 ?? "" }
    }

    static func same(_ a: String?, _ b: String?) -> Bool {
        if a == b { return true }
        func decimal(_ text: String?) -> Decimal? {
            let pattern = #"^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"#
            guard let text, text.range(of: pattern, options: .regularExpression) != nil else { return nil }
            return Decimal(string: text, locale: Locale(identifier: "en_US_POSIX"))
        }
        guard let left = decimal(a), let right = decimal(b) else { return false }
        return left == right
    }

    func changes(from original: Position) -> (stop: String?, targets: [String?]) {
        let baseline = original.takeProfitPrices ?? []
        let changedTargets = targets.enumerated().map { index, value -> String? in
            guard baseline.indices.contains(index), !Self.same(value, baseline[index] ?? "") else { return nil }
            return value
        }
        return (Self.same(stop, original.stop) ? nil : stop, changedTargets)
    }

    func issue(from original: Position, current: Position, last: String?, tick: String?) -> String? {
        let change = changes(from: original)
        let sign = current.side == "long" ? 1.0 : -1.0
        let market = Trading.num(last)
        guard market.isFinite, market > 0 else { return "请等待最新行情" }
        func valid(_ value: String) -> Bool {
            guard let aligned = Trading.priceAtTick(value, tick: tick) else { return false }
            return Self.same(value, aligned)
        }
        let stale = "持仓保护已变化，请关闭后重新打开"
        if let stop = change.stop {
            guard Self.same(current.stop, original.stop) else { return stale }
            guard valid(stop) else { return "止损价格须为正数且符合价格步长" }
            guard (Trading.num(stop) - Trading.num(current.stop)) * sign > 0 else { return "持仓止损只能向收紧方向调整" }
            guard (market - Trading.num(stop)) * sign > 0 else { return "止损价格不能越过最新价" }
        }
        let currentPrices = current.takeProfitPrices ?? []
        guard currentPrices.count == change.targets.count else { return stale }
        let editable = current.takeProfitEditable ?? []
        guard editable.count == currentPrices.count else { return stale }
        var previous: Double?
        for index in currentPrices.indices {
            let changed = change.targets[index]
            if let changed {
                guard editable[index],
                      Self.same(currentPrices[index], original.takeProfitPrices?[index]) else { return stale }
                guard valid(changed) else { return "止盈价格须为正数且符合价格步长" }
                let target = Trading.num(changed)
                guard (target - market) * sign > 0, (target - Trading.num(current.entry)) * sign > 0 else {
                    return sign > 0 ? "多单止盈需高于最新价和开仓价" : "空单止盈需低于最新价和开仓价"
                }
            }
            guard let value = changed ?? currentPrices[index], editable[index] else { continue }
            let target = Trading.num(value)
            if change.targets.contains(where: { $0 != nil }), let previous, (target - previous) * sign <= 0 {
                return sign > 0 ? "多单止盈价格需逐档升高" : "空单止盈价格需逐档降低"
            }
            previous = target
        }
        return nil
    }
}
