import Foundation

// /v1/state and related payloads (yoyo/discipline_app api.py). Decimals stay strings, as served;
// the phone submits intent only and never sends locally computed sizes.

struct Policy: Codable, Hashable {
    /// Additive selector; missing or legacy values preserve the existing margin ticket contract.
    let entrySizing: String?
    let majorAllowanceScope: String?
    let riskFraction: String
    let majorMarginFraction: String?
    let altMarginFraction: String?
    let marginMode: String?
    let majorExposureMultiple: String?
    let altExposureMultiple: String?
    let majorLeverage: Int
    let altLeverage: Int?
    let maxPositions: Int
    let maxEntries: Int
    let profitEntryBonus: Int?
    let lossStreak: Int
    let cooldownAfterLosses: Int?
    let dailyLossFraction: String
    let lossCooldownSeconds: Double
    let breakevenR: String
    let takeProfits: [[String]]
    let feeRate: String
    let slippageRate: String
    let quoteMaxAgeSeconds: Double
}

struct Quote: Codable, Hashable {
    let last: String
    let bid: String
    let ask: String
    let time: Double
    let source: String?
    let todayChange: String?
    let volume24hUsdt: String?
}

struct Instrument: Codable, Hashable {
    let symbol: String
    let maxLeverage: Int
    let tick: String
    let step: String
    let minimum: String?
    let tradable: Bool?
}

struct Position: Codable, Hashable, Identifiable {
    let id: String
    let symbol: String
    let side: String
    let quantity: String
    let remaining: String
    let entry: String
    let stop: String
    let originalStop: String?
    let risk: String
    let margin: String
    let leverage: Double
    let pnl: String
    let currentR: String?
    let breakeven: Bool
    let protection: String
    let policy: Policy
    let entryFee: String
    let totalFee: String?
    let opened: Double?
    let closed: Double?
    let closeReason: String?
}

struct Plan: Codable, Hashable {
    let quantity: String
    let notional: String
    let margin: String
    let risk: String
    let entry: String
    let stop: String
    let priceRisk: String
    let costs: String
    let marginMode: String
    let notionalEquityMultiple: String
    let exposureLimited: Bool
    /// Actual exchange leverage can be a fractional tier (for example 66.66x).
    var leverage: Double? = nil
}

struct PendingOrder: Codable, Hashable, Identifiable {
    let id: String
    let symbol: String
    let side: String
    let kind: String
    let price: String
    let plan: Plan
    let created: Double?
    let filledQuantity: String?
}

struct Discipline: Codable, Hashable {
    let entriesRemaining: Int
    let entriesLimit: Int?
    let entriesBonus: Int?
    let lossesRemaining: Int
    let dailyLossRemaining: String
    let dailyLossLimit: String
}

struct Daily: Codable, Hashable { let key: String; let entries: Int; let lockedReason: String? }
struct PolicyProposal: Codable, Hashable { let policy: Policy; let effectiveAfter: Double }

struct AccountState: Codable {
    let mode: String
    let accountId: String?
    let accountName: String?
    let policy: Policy?
    let policyRevision: Int
    let policyProposal: PolicyProposal?
    let equity: String
    /// Exchange-reported available funds, when present (used only for local estimates).
    let available: String?
    let cash: String
    let positions: [Position]
    let pending: [PendingOrder]
    let closed: [Position]
    let quotes: [String: Quote]
    let instruments: [String: Instrument]
    let blockers: [String]
    let daily: Daily?
    let discipline: Discipline?
    let lossStreak: Int
    let cooldownUntil: Double
    let serverTime: Double
    let privateAt: Double?
    let privateError: String?
    let sharedLeverage: [String: Double]?
    let externalActivity: ExternalActivity?
}

/// Read-only exchange observations outside the app (ExternalActivity.tsx); never counted by discipline.
struct ExternalRecord: Codable, Hashable, Identifiable {
    let id: String
    let kind: String          // position | order | algo
    let symbol: String
    let side: String
    let mixed: Bool
    let valueCurrency: String?
    let price: String?
    let notional: String?
    let pnl: String?
    let created: Double?
    let lastSeen: Double
    let status: String
}
struct ExternalActivity: Codable, Hashable { let current: [ExternalRecord]; let recent: [ExternalRecord]; let updatedAt: Double }

/// Order intent sent to /v1/preview and /v1/order-requests (server re-sizes everything).
struct Ticket: Hashable {
    var symbol: String
    var side: String       // long | short
    var kind: String       // market | limit
    var price: String
    var stop: String
    var fraction: String
    var leverage: Double
    var sizingMode: String = "margin"
    var json: [String: Any] {
        ["symbol": symbol, "side": side, "kind": kind, "price": price, "stop": stop, "fraction": fraction, "leverage": leverage, "sizing_mode": sizingMode]
    }
}

struct PreviewResult: Codable { let plan: Plan; let policyRevision: Int; let previewId: String? }
struct RequestStatus: Codable { let id: String?; let status: String; let message: String? }
struct StopValue: Codable { let stop: String }
struct WickStop: Codable { let stopLoss: String }

struct AccountSummary: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let environment: String
    let connected: Bool?
    let positions: Int?
    let pending: Int?
}
struct AccountList: Codable { let items: [AccountSummary] }

struct StatSummary: Codable, Hashable {
    let count: Int
    let wins: Int
    let losses: Int
    let flat: Int
    let netPnl: String
    let winRate: String?
    let payoffRatio: String?
    let averageNetR: String?
}
struct SymbolSummary: Codable, Hashable { let symbol: String; let count: Int; let netPnl: String; let winRate: String? }
struct SideSummary: Codable, Hashable { let side: String; let count: Int; let netPnl: String; let winRate: String? }
struct Statistics: Codable {
    struct Account: Codable {
        let equity: String
        let initialEquity: String?
        let realizedPnl: String
        let unrealizedPnl: String
        let netPnl: String?
        let returnFraction: String?
        let returnMethod: String?
        let openPositions: Int
        let pendingOrders: Int
    }
    struct Point: Codable, Hashable { let date: String; let pnl: String }
    let accountId: String
    let mode: String
    let serverTime: Double
    let accountUpdatedAt: Double?
    let periodDays: Int
    let account: Account
    let closed: StatSummary
    let fees: String
    let curve: [Point]
    let symbols: [SymbolSummary]
    let sides: [SideSummary]
}

// 1.618 monitor projection (/v1/market/monday-fib, public fields only).
struct FibTouch: Codable, Hashable { let side: Edge; let source: String; let occurredMs: Double }
struct FibContract: Codable, Hashable, Identifiable {
    let symbol: String
    let high: Double
    let low: Double
    let upper: Double
    let lower: Double
    let price: Double?
    let stale: Bool
    let touches: [String: FibTouch]
    var id: String { symbol }
    var latestTouch: FibTouch? { touches.values.max { $0.occurredMs < $1.occurredMs } }
}
struct FibSnapshot: Codable {
    let nowMs: Double
    let week: String
    let timezone: String?
    let weekStartMs: Double
    let mondayEndMs: Double
    let nextWeekMs: Double
    let mondayComplete: Bool
    let connected: Bool
    let lastMessageMs: Double?
    let items: [FibContract]
}

/// Public OKX display candles (candleData.ts) — never an execution price or risk input.
struct Candle: Hashable, Identifiable {
    let time: Double      // epoch seconds (bar open)
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Double
    let confirmed: Bool
    /// Frozen OKX cloud-v17 source values; nil for ordinary live-market candles.
    var ema20: Double? = nil
    var ema50: Double? = nil
    var emaReady: Bool? = nil
    var id: Double { time }

    static let maxCount = 2000

    var valid: Bool {
        [time, open, high, low, close, volume].allSatisfy(\.isFinite) && time > 0 && time == time.rounded()
            && min(open, high, low, close) > 0 && volume >= 0 && high >= max(open, close) && low <= min(open, close)
    }

    /// OKX rows [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]; volume uses volCcy like the 1.x app.
    static func parse(_ rows: [[String]]) -> [Candle] {
        var bars: [Candle] = []
        for row in rows where row.count >= 9 && ["0", "1"].contains(row[8]) && [0, 1, 2, 3, 4, 6].allSatisfy({ !row[$0].trimmingCharacters(in: .whitespaces).isEmpty }) {
            guard let t = Double(row[0]), let o = Double(row[1]), let h = Double(row[2]), let l = Double(row[3]), let c = Double(row[4]), let v = Double(row[6]) else { continue }
            let bar = Candle(time: t / 1000, open: o, high: h, low: l, close: c, volume: v, confirmed: row[8] == "1")
            if bar.valid { bars.append(bar) }
        }
        return merge([], bars)
    }

    /// Later rows replace earlier ones, except that a confirmed bar is never downgraded.
    static func merge(_ base: [Candle], _ incoming: [Candle], limit: Int = maxCount) -> [Candle] {
        var byTime: [Double: Candle] = [:]
        for bar in base + incoming where bar.valid {
            if byTime[bar.time]?.confirmed == true && !bar.confirmed { continue }
            byTime[bar.time] = bar
        }
        return Array(byTime.values.sorted { $0.time < $1.time }.suffix(max(1, limit)))
    }
}

struct CandlePage: Decodable { let data: [[String]] }

extension Candle {
    /// Buckets `bars` into `seconds`-long candles opening at UTC+8 boundaries (OKX 12H/1D convention).
    /// A bucket is confirmed only when its final source bar is confirmed.
    static func aggregate(_ bars: [Candle], seconds: Double, source: Double, offset: Double = 28800) -> [Candle] {
        var out: [Candle] = []
        for bar in bars {
            let start = floor((bar.time + offset) / seconds) * seconds - offset
            let closes = bar.time + source >= start + seconds
            if let last = out.last, last.time == start {
                out[out.count - 1] = Candle(time: start, open: last.open, high: max(last.high, bar.high), low: min(last.low, bar.low), close: bar.close,
                                            volume: last.volume + bar.volume, confirmed: closes && bar.confirmed)
            } else {
                out.append(Candle(time: start, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume, confirmed: closes && bar.confirmed))
            }
        }
        return out
    }
}

enum CandlePeriod: String, CaseIterable, Identifiable {
    case m1 = "1m", m5 = "5m", m15 = "15m", h1 = "1H", h4 = "4H", h12 = "12H", d1 = "1D"
    var id: String { rawValue }
    var label: String { switch self { case .m1: "1分"; case .m5: "5分"; case .m15: "15分"; case .h1: "1时"; case .h4: "4时"; case .h12: "12时"; case .d1: "日" } }
    var seconds: Double { switch self { case .m1: 60; case .m5: 300; case .m15: 900; case .h1: 3600; case .h4: 14400; case .h12: 43200; case .d1: 86400 } }
    /// The relay and the service only serve OKX's own bars; 12H is built from 4H (UTC+8 aligned, like OKX 12H).
    var source: CandlePeriod { self == .h12 ? .h4 : self }
}
