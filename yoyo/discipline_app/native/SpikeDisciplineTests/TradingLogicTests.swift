import XCTest
@testable import SpikeDiscipline

/// Ports of the 1.x TypeScript tests (tests/discipline_app/entry_flow, margin_allocation,
/// contract_ranking) plus decoding of synthetic fixture payloads (serve_iphone_ui_fixture.py).
final class TradingLogicTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "json"))
        return try Data(contentsOf: url)
    }

    private func base() throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: fixture("state_live")) as? [String: Any])
    }

    private func state(_ edit: (inout [String: Any]) -> Void) throws -> AccountState {
        var value = try base()
        edit(&value)
        return try API.decoder.decode(AccountState.self, from: JSONSerialization.data(withJSONObject: value))
    }

    private func position(_ symbol: String, entry: String, remaining: String, leverage: Int, from base: [String: Any]) -> [String: Any] {
        var p = (base["positions"] as! [[String: Any]])[0]
        p["symbol"] = symbol; p["entry"] = entry; p["remaining"] = remaining; p["quantity"] = remaining; p["leverage"] = leverage
        return p
    }

    private func pending(_ symbol: String, margin: String, quantity: String = "0", entry: String = "100") -> [String: Any] {
        ["id": "p-" + symbol, "symbol": symbol, "side": "long", "kind": "limit", "price": entry, "stop": "90", "fraction": ".1", "leverage": 10,
         "plan": ["quantity": quantity, "notional": "0", "margin": margin, "risk": "0", "entry": entry, "stop": "90", "price_risk": "0",
                  "costs": "0", "margin_mode": "cross", "notional_equity_multiple": "0", "exposure_limited": false]]
    }

    func testFixturePayloadsDecode() throws {
        let s = try API.decoder.decode(AccountState.self, from: fixture("state_live"))
        XCTAssertEqual(s.mode, "okx_live")
        XCTAssertFalse(s.positions.isEmpty)
        XCTAssertNil(s.policy?.entrySizing)
        XCTAssertEqual(s.positions.first?.leverage, 100)
        XCTAssertEqual(s.sharedLeverage?["BTC-USDT-SWAP"], 100)
        XCTAssertEqual(s.discipline?.entriesLimit, 4)
        XCTAssertNoThrow(try API.decoder.decode(Statistics.self, from: fixture("statistics")))
        XCTAssertEqual(try API.decoder.decode(AccountList.self, from: fixture("accounts")).items.first?.id, "primary")
        let fib = try API.decoder.decode(FibSnapshot.self, from: fixture("monday_fib"))
        XCTAssertEqual(fib.items.first?.latestTouch?.side, .upper)
        let page = try API.decoder.decode(CandlePage.self, from: fixture("candles"))
        let bars = Candle.parse(page.data)
        XCTAssertEqual(bars.count, 6)
        XCTAssertEqual(bars, bars.sorted { $0.time < $1.time })
        XCTAssertFalse(bars.last!.confirmed)
    }

    func testExposurePolicyAndFractionalExchangeLeverageDecodeWithLegacyFallback() throws {
        let raw = try base()
        let old = try state { _ in }
        XCTAssertFalse(Trading.usesExposureSizing(old.policy))

        let new = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["alt_leverage"] = NSNull()
            value["policy"] = policy
            var positions = value["positions"] as! [[String: Any]]
            positions[0]["leverage"] = 66.66
            value["positions"] = positions
            value["shared_leverage"] = ["BTC-USDT-SWAP": 66.66]
        }
        XCTAssertTrue(Trading.usesExposureSizing(new.policy))
        XCTAssertNil(new.policy?.altLeverage)
        XCTAssertEqual(new.positions.first?.leverage ?? 0, 66.66, accuracy: 1e-12)
        XCTAssertEqual(new.sharedLeverage?["BTC-USDT-SWAP"] ?? 0, 66.66, accuracy: 1e-12)

        let planData = try JSONSerialization.data(withJSONObject: [
            "quantity": "1", "notional": "100", "margin": "1.5", "risk": "2", "entry": "100", "stop": "98",
            "price_risk": "2", "costs": "0", "margin_mode": "cross", "notional_equity_multiple": "0.1",
            "exposure_limited": false, "leverage": 66.66
        ])
        let actualPlan = try API.decoder.decode(Plan.self, from: planData)
        XCTAssertEqual(actualPlan.leverage ?? 0, 66.66, accuracy: 1e-12)
        var oldPlanObject = try XCTUnwrap(JSONSerialization.jsonObject(with: planData) as? [String: Any])
        oldPlanObject.removeValue(forKey: "leverage")
        let oldPlanData = try JSONSerialization.data(withJSONObject: oldPlanObject)
        XCTAssertNil(try API.decoder.decode(Plan.self, from: oldPlanData).leverage)

        let intent = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "99", fraction: "0.33", leverage: 66.66, sizingMode: "exposure")
        XCTAssertEqual(intent.json["sizing_mode"] as? String, "exposure")
        XCTAssertEqual(intent.json["leverage"] as? Double ?? 0, 66.66, accuracy: 1e-12)
        let legacyIntent = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "99", fraction: "0.1", leverage: 20)
        XCTAssertEqual(legacyIntent.json["sizing_mode"] as? String, "margin")
        XCTAssertEqual(raw["equity"] as? String, "1000")
    }

    @MainActor
    func testExposureTicketUsesPoolRoomFractionAndExchangeLeverageCap() throws {
        let raw = try base()
        let s = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["major_exposure_multiple"] = "10"
            policy["alt_exposure_multiple"] = "5"
            policy["major_margin_fraction"] = NSNull()
            policy["alt_margin_fraction"] = NSNull()
            policy["alt_leverage"] = NSNull()
            value["policy"] = policy
            value["equity"] = "1000"
            value["available"] = "1000"
            value["positions"] = [position("BTC-USDT-SWAP", entry: "100", remaining: "2", leverage: 10, from: raw),
                                   position("SOL-USDT-SWAP", entry: "100", remaining: "3", leverage: 10, from: raw)]
            value["pending"] = [pending("SOL-USDT-SWAP", margin: "0", quantity: "4", entry: "100")]
            value["quotes"] = [
                "BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1],
                "ETH-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1],
                "SOL-USDT-SWAP": ["last": "200", "ask": "200", "bid": "200", "time": 1]
            ]
            var instruments = value["instruments"] as! [String: [String: Any]]
            instruments["SOL-USDT-SWAP"]?["max_leverage"] = 75
            value["instruments"] = instruments
        }
        XCTAssertEqual(Trading.exposureRoom(s, symbol: "BTC-USDT-SWAP"), 9_800, accuracy: 1e-9)
        XCTAssertEqual(Trading.exposureRoom(s, symbol: "ETH-USDT-SWAP"), 10_000, accuracy: 1e-9)
        XCTAssertEqual(Trading.exposureRoom(s, symbol: "SOL-USDT-SWAP"), 3_600, accuracy: 1e-9)

        let defaults = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["alt_leverage"] = NSNull()
            value["policy"] = policy
            value["positions"] = [Any]()
            value["pending"] = [Any]()
            value["shared_leverage"] = [String: Any]()
            var instruments = value["instruments"] as! [String: [String: Any]]
            instruments["BTC-USDT-SWAP"]?["max_leverage"] = 75
            instruments["SOL-USDT-SWAP"]?["max_leverage"] = 75
            value["instruments"] = instruments
        }
        let ticket = TicketModel()
        ticket.symbol = "BTC-USDT-SWAP"
        ticket.allocation = 33
        ticket.syncLeverage(defaults)
        XCTAssertEqual(ticket.cap(defaults), 100)
        XCTAssertEqual(ticket.leverage, 100)
        XCTAssertEqual(ticket.leverageLabel(defaults), "交易所 100x")
        XCTAssertEqual(ticket.fraction(defaults), "0.33")
        XCTAssertEqual(ticket.ticket(defaults).sizingMode, "exposure")

        ticket.symbol = "SOL-USDT-SWAP"
        ticket.syncLeverage(defaults)
        XCTAssertEqual(ticket.cap(defaults), 75)
        XCTAssertEqual(ticket.leverage, 75)
    }

    func testExposureEstimateAppliesNotionalRequestAndKeepsRiskAndStepCaps() throws {
        let s = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["major_exposure_multiple"] = "10"
            policy["risk_fraction"] = ".05"
            policy["fee_rate"] = ".0005"
            policy["slippage_rate"] = ".001"
            value["policy"] = policy
            value["equity"] = "1000"
            value["available"] = "1000"
            value["positions"] = [Any]()
            value["pending"] = [Any]()
            value["quotes"] = ["BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1]]
        }
        let ticket = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "99.99", fraction: "0.5", leverage: 100, sizingMode: "exposure")
        let estimate = try XCTUnwrap(Trading.estimate(s, ticket))
        XCTAssertEqual(estimate.quantity, 49.95, accuracy: 1e-9) // 50% × (1000 × 10 U), / estimated market entry, then .01 step
        XCTAssertEqual(estimate.notional, 4_999.995, accuracy: 1e-6)
        XCTAssertLessThanOrEqual(estimate.risk, 50)
        XCTAssertEqual((estimate.quantity / 0.01).rounded(), estimate.quantity / 0.01, accuracy: 1e-8)
    }

    func testExposureEstimateUsesPaperReservedFundsAndServerAddOnRiskBudget() throws {
        let noPosition = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["major_exposure_multiple"] = "10"
            policy["risk_fraction"] = ".05"
            policy["fee_rate"] = ".0005"
            policy["slippage_rate"] = ".001"
            value["policy"] = policy
            value["equity"] = "1000"
            value["available"] = NSNull()
            value["positions"] = [Any]()
            value["pending"] = [pending("BTC-USDT-SWAP", margin: "900", quantity: "0")]
            value["quotes"] = ["BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1]]
        }
        let freshTicket = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "99.99", fraction: "1", leverage: 100, sizingMode: "exposure")
        let availableEstimate = try XCTUnwrap(Trading.estimate(noPosition, freshTicket))
        let entry = 100.1, unitCost = entry * (2 * 0.0005 + 0.001)
        let expectedFundsQuantity = floor((100 / (entry / 100 + unitCost)) / 0.01) * 0.01
        XCTAssertEqual(availableEstimate.quantity, expectedFundsQuantity, accuracy: 1e-8)

        let exchangeLimited = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["major_exposure_multiple"] = "10"
            policy["risk_fraction"] = ".05"
            policy["fee_rate"] = ".0005"
            policy["slippage_rate"] = ".001"
            value["policy"] = policy
            value["equity"] = "1000"
            value["available"] = "50"
            value["positions"] = [Any]()
            value["pending"] = [pending("BTC-USDT-SWAP", margin: "900", quantity: "0")]
            value["quotes"] = ["BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1]]
        }
        let exchangeEstimate = try XCTUnwrap(Trading.estimate(exchangeLimited, freshTicket))
        let expectedExchangeQuantity = floor((50 / (entry / 100 + unitCost)) / 0.01) * 0.01
        XCTAssertEqual(exchangeEstimate.quantity, expectedExchangeQuantity, accuracy: 1e-8)

        let addOnBase = try base()
        let addOn = try state { value in
            var policy = value["policy"] as! [String: Any]
            policy["entry_sizing"] = "exposure"
            policy["major_allowance_scope"] = "independent"
            policy["major_exposure_multiple"] = "10"
            policy["risk_fraction"] = ".05"
            policy["fee_rate"] = ".0005"
            policy["slippage_rate"] = ".001"
            value["policy"] = policy
            value["equity"] = "1000"
            value["available"] = "1000"
            value["positions"] = [position("BTC-USDT-SWAP", entry: "100", remaining: "1", leverage: 10, from: addOnBase)]
            value["pending"] = [Any]()
            value["quotes"] = ["BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1]]
        }
        let addOnTicket = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "95", fraction: "1", leverage: 10, sizingMode: "exposure")
        let addOnEstimate = try XCTUnwrap(Trading.estimate(addOn, addOnTicket))
        let currentRisk = 1 * ((100.0 - 95) + 100 * (2 * 0.0005 + 0.001))
        let remainingBudget = 1000 * 0.05 - currentRisk
        let newRiskPerUnit = (entry - 95) + unitCost + (entry * 0.0005 + max(0, (entry - 100) * 1)) * 0.05
        let expectedRiskQuantity = floor((remainingBudget / newRiskPerUnit) / 0.01) * 0.01
        XCTAssertEqual(addOnEstimate.quantity, expectedRiskQuantity, accuracy: 1e-8)
        XCTAssertLessThanOrEqual(addOnEstimate.risk, 50)
    }

    func testCandleParsingRejectsMalformedRows() {
        let good = ["1794038400000", "1", "2", "0.5", "1.5", "10", "20", "30", "1"]
        var bad = good; bad[8] = "2"
        var inverted = good; inverted[2] = "0.9"
        XCTAssertEqual(Candle.parse([good, bad, inverted, ["1"]]).count, 1)
        let unconfirmed = ["1794038400000", "1", "2", "0.5", "1.2", "10", "20", "30", "0"]
        XCTAssertEqual(Candle.merge(Candle.parse([good]), Candle.parse([unconfirmed])).first?.close, 1.5)
    }

    func testPriceAtTick() {
        XCTAssertEqual(Trading.priceAtTick("0.000000123456", tick: ".00000001"), "0.00000012")
        XCTAssertEqual(Trading.priceAtTick("81073.123", tick: "0.1"), "81073.1")
        XCTAssertEqual(Trading.priceAtTick("1.239", tick: ".05"), "1.25")
        XCTAssertEqual(Trading.priceAtTick("100.000", tick: "0.01"), "100")
        for value in ["", "NaN", "1e6", "-1", "0"] { XCTAssertNil(Trading.priceAtTick(value, tick: ".01")) }
        XCTAssertNil(Trading.priceAtTick("1", tick: "0"))
    }

    func testEntryPollingKeepsAbsentRequestFence() {
        XCTAssertEqual(Trading.entryPollDelay("processing"), .seconds(1))
        XCTAssertEqual(Trading.entryPollDelay("not_found"), .seconds(3))
        for status in ["processing", "not_found", "unknown", "sending", "checking"] { XCTAssertFalse(Trading.entryResolved(status)) }
        for status in ["filled", "pending", "rejected", "interrupted"] { XCTAssertTrue(Trading.entryResolved(status)) }
        XCTAssertTrue(Trading.isRequestId(Trading.newRequestId()))
        XCTAssertFalse(Trading.isRequestId("bad"))
        XCTAssertEqual(AccountStore.fractionText(100), "1")
        XCTAssertEqual(AccountStore.fractionText(50), "0.5")
        XCTAssertEqual(AccountStore.fractionText(25), "0.25")
    }

    func testFullAllocationSpendsOnlyRemainingCategoryRoom() throws {
        let raw = try base()
        let s = try state { v in
            var policy = v["policy"] as! [String: Any]
            policy["major_allowance_scope"] = "shared"; policy["major_margin_fraction"] = ".1"; policy["alt_margin_fraction"] = ".05"
            v["policy"] = policy; v["equity"] = "1000"; v["quotes"] = [String: Any]()
            v["positions"] = [position("BTC-USDT-SWAP", entry: "100", remaining: "2", leverage: 10, from: raw)]
            v["pending"] = [pending("ETH-USDT-SWAP", margin: "30")]
        }
        XCTAssertEqual(Trading.allocatedMargin(room: Trading.marginRoom(s, symbol: "BTC-USDT-SWAP"), percent: 100), 0.05, accuracy: 1e-12)
        XCTAssertEqual(Trading.allocatedMargin(room: Trading.marginRoom(s, symbol: "ETH-USDT-SWAP"), percent: 50), 0.025, accuracy: 1e-12)
        XCTAssertEqual(Trading.allocatedMargin(room: Trading.marginRoom(s, symbol: "SOL-USDT-SWAP"), percent: 100), 0.05, accuracy: 1e-12)
        XCTAssertEqual(Trading.allocatedMargin(room: Trading.marginRoom(nil, symbol: "BTC-USDT-SWAP"), percent: 100), 0)
    }

    func testIndependentMajorsKeepTheirOwnRoom() throws {
        let raw = try base()
        let s = try state { v in
            var policy = v["policy"] as! [String: Any]
            policy["major_allowance_scope"] = "independent"; policy["major_margin_fraction"] = ".1"; policy["alt_margin_fraction"] = ".05"
            v["policy"] = policy; v["equity"] = "1000"; v["quotes"] = [String: Any]()
            v["positions"] = [position("BTC-USDT-SWAP", entry: "100", remaining: "70", leverage: 100, from: raw)]
            v["pending"] = [pending("BTC-USDT-SWAP", margin: "30"), pending("SOL-USDT-SWAP", margin: "50")]
        }
        XCTAssertEqual(Trading.marginRoom(s, symbol: "BTC-USDT-SWAP"), 0, accuracy: 1e-12)
        XCTAssertEqual(Trading.marginRoom(s, symbol: "ETH-USDT-SWAP"), 0.1, accuracy: 1e-12)
        XCTAssertEqual(Trading.marginRoom(s, symbol: "DOGE-USDT-SWAP"), 0, accuracy: 1e-12)
    }

    func testAllocationClampsAndNeverExceedsRoom() {
        for room in [0, 0.000000019, 0.029999999, 0.1] {
            for percent in [0.0, 1, 33, 100, 120] {
                let f = Trading.allocatedMargin(room: room, percent: percent)
                XCTAssertTrue(f >= 0 && f <= room)
            }
        }
        XCTAssertEqual(Trading.allocatedMargin(room: 0.1, percent: -10), 0)
        XCTAssertEqual(Trading.allocatedMargin(room: 0.1, percent: .nan), 0)
        XCTAssertEqual(Trading.allocatedMargin(room: .infinity, percent: 100), 0)
    }

    func testFixedWickStopControlsSize() throws {
        let s = try state { v in
            var policy = v["policy"] as! [String: Any]
            policy["major_allowance_scope"] = "independent"; policy["major_margin_fraction"] = ".1"; policy["alt_margin_fraction"] = ".05"
            policy["major_exposure_multiple"] = "10"; policy["alt_exposure_multiple"] = "5"; policy["risk_fraction"] = ".05"
            policy["fee_rate"] = ".0005"; policy["slippage_rate"] = ".001"
            v["policy"] = policy; v["equity"] = "1000"; v["positions"] = [Any](); v["pending"] = [Any]()
            v["quotes"] = ["BTC-USDT-SWAP": ["last": "100", "ask": "100", "bid": "100", "time": 1]]
            v["instruments"] = ["BTC-USDT-SWAP": ["symbol": "BTC-USDT-SWAP", "max_leverage": 100, "tick": ".01", "step": ".01"]]
        }
        let ticket = Ticket(symbol: "BTC-USDT-SWAP", side: "long", kind: "market", price: "", stop: "94", fraction: ".1", leverage: 100)
        var far = ticket; far.stop = "90"
        let n = try XCTUnwrap(Trading.estimate(s, ticket)), f = try XCTUnwrap(Trading.estimate(s, far))
        XCTAssertGreaterThan(n.quantity, f.quantity)
        XCTAssertTrue(n.risk <= 50 && f.risk <= 50)
        var cap = ticket; cap.stop = "100"
        let c = try XCTUnwrap(Trading.estimate(s, cap))
        XCTAssertTrue(c.notional <= 10000 && c.margin <= 100)
        var wrong = ticket; wrong.stop = "101"
        XCTAssertNil(Trading.estimate(s, wrong))
    }

    func testVolumeRankingPutsMissingBehindZero() {
        func q(_ volume: String?, _ change: String = "0", _ time: Double = 100) -> Quote {
            Quote(last: "100", bid: "99", ask: "101", time: time, source: "synthetic", todayChange: change, volume24hUsdt: volume)
        }
        let quotes = ["BTC": q("20"), "ETH": q("100"), "SOL": q("20"), "DOGE": q("900"), "ZERO": q("0"), "UNKNOWN": q(nil)]
        XCTAssertEqual(Trading.rank(Array(quotes.keys), quotes: quotes, now: 100, sort: .volume), ["DOGE", "ETH", "BTC", "SOL", "ZERO", "UNKNOWN"])
        for value in [nil, "", "-1", "NaN", "Infinity"] { XCTAssertNil(Trading.turnover(q(value))) }
        XCTAssertEqual(Trading.turnover(q("0")), 0)
        XCTAssertNil(Trading.dayChange(q("1", "2", 100), now: 116))
        XCTAssertNil(Trading.dayChange(q("1", "2", 57599), now: 57601))
        XCTAssertEqual(Trading.turnoverLabel(nil), "24h —")
        XCTAssertEqual(Trading.turnoverLabel(0), "24h ≈ 0 U")
        XCTAssertEqual(Trading.turnoverLabel(1_230_000_000), "24h ≈ 12.3亿 U")
        XCTAssertEqual(Trading.turnoverLabel(850_000), "24h ≈ 85万 U")
    }

    func testAvailabilityOrder() throws {
        let s = try state { v in v["blockers"] = ["已有委托在核对"] }
        XCTAssertEqual(Trading.availability(state: s, symbol: "BTC-USDT-SWAP", connected: false, online: true, fresh: true, pending: false, room: 1, now: 0)?.destination, .settings)
        XCTAssertEqual(Trading.availability(state: s, symbol: "BTC-USDT-SWAP", connected: true, online: true, fresh: true, pending: true, room: 1, now: 0)?.text, "正在核对上一笔请求")
        XCTAssertEqual(Trading.availability(state: s, symbol: "BTC-USDT-SWAP", connected: true, online: true, fresh: true, pending: false, room: 1, now: 0)?.destination, .positions)
    }

    func testTwelveHourBarsAggregateFourHourBarsOnUTCPlus8Boundaries() {
        let base = 1_793_894_400.0   // 2026-11-05 00:00 UTC+8 ((t + 8 h) % 1 d == 0)
        func bar(_ i: Double, _ o: Double, _ c: Double, confirmed: Bool = true) -> Candle {
            Candle(time: base + i * 14_400, open: o, high: max(o, c) + 1, low: min(o, c) - 1, close: c, volume: 10, confirmed: confirmed)
        }
        let bars = [bar(0, 10, 11), bar(1, 11, 12), bar(2, 12, 9), bar(3, 9, 8), bar(4, 8, 7, confirmed: false)]
        let out = Candle.aggregate(bars, seconds: 43_200, source: 14_400)
        XCTAssertEqual(out.count, 2)
        XCTAssertEqual(out[0].time, base)
        XCTAssertEqual(out[0].open, 10); XCTAssertEqual(out[0].close, 9); XCTAssertEqual(out[0].high, 13); XCTAssertEqual(out[0].low, 8)
        XCTAssertEqual(out[0].volume, 30); XCTAssertTrue(out[0].confirmed)
        XCTAssertEqual(out[1].time, base + 43_200); XCTAssertFalse(out[1].confirmed)
        XCTAssertEqual(CandlePeriod.h12.source, .h4)
    }

    func testIndicatorsRestartAfterGapsAndCloudSeedsWithFirstClose() {
        let bars = (0..<30).map { Candle(time: Double($0) * 60, open: 1, high: 2, low: 0.5, close: Double($0 + 1), volume: 1, confirmed: true) }
        let sma = Indicators.sma(bars, 20, step: 60)
        XCTAssertNil(sma[18]); XCTAssertEqual(sma[19], 10.5)
        let ema = Indicators.ema(bars, 20, step: 60)
        XCTAssertNil(ema[18]); XCTAssertNotNil(ema[19])
        var gapped = bars; gapped[25] = Candle(time: 25 * 60 + 600, open: 1, high: 2, low: 0.5, close: 26, volume: 1, confirmed: true)
        XCTAssertNil(Indicators.sma(gapped, 20, step: 60)[25])
        let cloud = Indicators.cloud(bars, step: 60)
        XCTAssertEqual(cloud[0]?.a, 1); XCTAssertEqual(cloud[0]?.b, 1)
        XCTAssertGreaterThan(cloud[29]!.a, cloud[29]!.b)   // rising closes: fast EMA above slow
    }
}
