"""Forward paper account for the market-wide lower-rail breadth signal (owner 2026-11-08).

Owner 2026-11-08 ("Yes, set it up" to a SOL-main paper account with BTC/ETH for comparison), after
exp-monday-breadth-drop-control-20261108-v1/v2. The studied signal (exp-monday-breadth-six-day-excursions-20261108-v1):
one native 5m bar in which 50-99 Binance USD-M coins first touch their weekly lower Monday 1.618, no new lower first
touch in the next 30 minutes, buy at the next 5m open, sell at the open six days later. Against same-coin drops
matched on depth, bounce, volatility and 30-day trend the excess held for SOL (+9.35pp, CI +2.61 to +15.84) but not
for BTC (+2.35pp) or ETH (+0.82pp). SOL is therefore the funded leg; BTC and ETH are shadow comparison legs.

Live adaptation (approximate, and recorded as such):
  universe = the OKX USDT swaps of MondayFibService; Binance REST is unreachable from this host. In the weeks both
             ledgers cover (2026-10-19..27) OKX live counts per 5m bar were about 0.63 x the Binance counts (corr 0.83,
             few large bars), so the band is 31-62 OKX coins in one bar.
  bar      = UTC 5m bar of the 1m touch candle. Counts come from the monitor's monday-fib touch book (live and
             reconciled history touches), read only, so restarts keep them.
  decision = bar close + 30 minutes, evaluated once after a 90 s grace: the bar must still hold 31-62 coins and no
             lower first touch may have a candle in [bar close, decision).
  entry    = OKX 1m open at the decision minute; exit = OKX 1m open six days later; taker 5 bp per fill (account Lv1
             read 2026-10-23), no slippage or funding.
  sizing   = 1000 USDT; SOL notional = realized equity at 1x. BTC and ETH carry the same notional as shadow rows
             that never change equity. One signal at a time: a signal while SOL is held is recorded as busy.
Notifications: one Telegram/Bark message when the legs open and one when they close (owner 2026-11-01: every live-
strategy signal is sent, never twice). A message is attempted once; uncertain outcomes are stored, never retried.
Paper only: no account key or order endpoint is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
from zoneinfo import ZoneInfo

from fastapi.responses import JSONResponse
import requests

from yoyo.data.candle_hub import validate

MINUTE, DAY = 60_000, 86_400_000
BAR = 5*MINUTE
OBSERVE_MS, HOLD_MS, GRACE_MS = 30*MINUTE, 6*DAY, 90_000
COUNT_MIN, COUNT_MAX = 31, 62
LOOKBACK_MS = 3*60*MINUTE
PRICE_GIVEUP_MS = 2*DAY
RECENT_MS = 12*60*MINUTE
FUNDED = "SOL-USDT-SWAP"
SHADOWS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
LEGS = (FUNDED,)+SHADOWS
START_EQUITY, TAKER_BP = 1000.0, 5.0
TRACK_KEY = "live-breadth"
TRACK_ID = 9260  # row.track on the paper page; never a reclaim-ledger track
ID_OFFSET = 9_260_000_000
SHANGHAI = ZoneInfo("Asia/Shanghai")
RULE = dict(track=TRACK_KEY, count_min=COUNT_MIN, count_max=COUNT_MAX, bar_minutes=5, observe_minutes=30, hold_days=6,
            funded=FUNDED, shadows=list(SHADOWS), start_equity=START_EQUITY, leverage=1.0, taker_bp=TAKER_BP,
            source="exp-monday-breadth-six-day-excursions-20261108-v1; controls exp-monday-breadth-drop-control-20261108-v1/v2",
            universe="OKX USDT swaps (MondayFibService); band 31-62 = Binance 50-99 x 0.63, approximate",
            paper_only=True)


def bj(ms):
    return datetime.fromtimestamp(ms/1000, SHANGHAI).strftime("%m-%d %H:%M")


def base(symbol):
    return symbol.split("-")[0]


class BreadthPaper:
    def __init__(self, runtime, client, clock=None, telegram_creds=None, bark_key=None, sender=None, quotes=None):
        from yoyo.monitor import bark, telegram
        self.runtime = Path(runtime)
        self.path = self.runtime/"breadth-paper.sqlite3"
        self.touch_book = self.runtime/"monday-fib.sqlite3"
        self.client = client
        self.clock = clock or client.clock
        self.telegram_creds = telegram_creds if telegram_creds is not None else telegram.credentials()
        self.bark_key = bark_key if bark_key is not None else bark.credentials(self.runtime)
        self.sender = sender or requests.post
        self.quotes = quotes or (lambda: {})  # read-only {symbol: {"price": ...}} for open-leg marks
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        self.write_lock = threading.Lock()
        self.runtime.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), equity REAL NOT NULL, started_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY, bar_ms INTEGER NOT NULL UNIQUE, n INTEGER, symbols TEXT, decision_ms INTEGER NOT NULL,
                    new_n INTEGER, new_symbols TEXT, status TEXT NOT NULL, created_ms INTEGER, evaluated_ms INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY, signal_id INTEGER NOT NULL, symbol TEXT NOT NULL, funded INTEGER NOT NULL,
                    notional REAL NOT NULL, entry_ms INTEGER NOT NULL, entry REAL NOT NULL, exit_due_ms INTEGER NOT NULL,
                    exit_ms INTEGER, exit REAL, gross_bp REAL, fee_bp REAL, net_bp REAL, pnl REAL, stage TEXT NOT NULL,
                    error TEXT, UNIQUE(signal_id, symbol));
                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY, signal_id INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                    telegram TEXT, bark TEXT, error TEXT, created_ms INTEGER, UNIQUE(signal_id, kind));
            """)
            db.execute("INSERT OR IGNORE INTO account (id,equity,started_ms) VALUES (1,?,?)", (START_EQUITY, self.clock()))
            # A send interrupted by a restart has an unknown outcome and is never repeated.
            for ch in ("telegram", "bark"):
                db.execute(f"UPDATE notices SET {ch}='unknown' WHERE {ch}='attempting'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- inputs -------------------------------------------------------------------------------------------------
    def touches(self, lo, hi):
        """Distinct lower first touches with 1m candles in [lo, hi): {symbol: candle_ms}, or None if unreadable."""
        if not self.touch_book.exists():
            return None
        book = sqlite3.connect(f"file:{self.touch_book}?mode=ro", uri=True, timeout=5)
        try:
            rows = book.execute("SELECT symbol, MIN(candle_ms) FROM touches WHERE side='lower' AND candle_ms>=? AND candle_ms<? "
                                "GROUP BY symbol", (lo, hi)).fetchall()
        finally:
            book.close()
        return {s: int(c) for s, c in rows}

    def open_price(self, symbol, ms, now):
        """OKX 1m open at ms once that minute has closed; None while unavailable.

        The shared candle hub caches per (symbol, bar) whatever endpoint answered, so an empty recent-endpoint reply
        for an old minute would also be served to the history endpoint. Pick one endpoint by age instead: /candles
        covers about the last 1440 one-minute bars.
        """
        if ms+MINUTE > now:
            return None
        path = "/api/v5/market/candles" if now-ms <= RECENT_MS else "/api/v5/market/history-candles"
        try:
            rows = validate(self.client.get(path, {"instId": symbol, "bar": "1m", "after": str(ms+MINUTE), "limit": "3"}), "1m", now)
        except Exception:
            return None
        row = rows.get(ms)
        return float(row[1]) if row is not None else None

    # ---- state machine ------------------------------------------------------------------------------------------
    def account(self, db):
        return dict(db.execute("SELECT * FROM account WHERE id=1").fetchone())

    def scan(self, now):
        """Record closed 5m bars holding COUNT_MIN..COUNT_MAX lower first touches as watched signals."""
        with self.db() as db:
            started = self.account(db)["started_ms"]
        lo = max(now-LOOKBACK_MS, started)//BAR*BAR
        found = self.touches(lo, now//BAR*BAR)
        if found is None:
            return 0
        bars = {}
        for symbol, candle in found.items():
            bars.setdefault(candle//BAR*BAR, []).append(symbol)
        added = 0
        with self.write_lock, self.db() as db:
            for bar, symbols in bars.items():
                if bar < started or bar+BAR+MINUTE > now or not COUNT_MIN <= len(symbols) <= COUNT_MAX:
                    continue
                added += db.execute("INSERT OR IGNORE INTO signals (bar_ms,n,symbols,decision_ms,status,created_ms) VALUES (?,?,?,?,?,?)",
                                    (bar, len(symbols), json.dumps(sorted(symbols)), bar+BAR+OBSERVE_MS, "watching", now)).rowcount
        return added

    def evaluate(self, now):
        """Decide each watched signal once its 30 minute observation has closed."""
        with self.db() as db:
            due = [dict(r) for r in db.execute("SELECT * FROM signals WHERE status IN ('watching','entering') AND decision_ms+?<=? "
                                               "ORDER BY bar_ms", (GRACE_MS, now))]
        for s in due:
            if s["status"] == "watching":
                found = self.touches(s["bar_ms"], s["decision_ms"])
                if found is None:
                    continue
                initial = sorted(k for k, c in found.items() if c < s["bar_ms"]+BAR)
                new = sorted(k for k, c in found.items() if c >= s["bar_ms"]+BAR)
                with self.write_lock, self.db() as db:
                    busy = db.execute("SELECT 1 FROM positions WHERE funded=1 AND stage='open' LIMIT 1").fetchone() or \
                        db.execute("SELECT 1 FROM signals WHERE status='entering' AND id<>? LIMIT 1", (s["id"],)).fetchone()
                    status = ("out_of_band" if not COUNT_MIN <= len(initial) <= COUNT_MAX else "followers" if new
                              else "busy" if busy else "entering")
                    db.execute("UPDATE signals SET n=?,symbols=?,new_n=?,new_symbols=?,status=?,evaluated_ms=? WHERE id=?",
                               (len(initial), json.dumps(initial), len(new), json.dumps(new), status, now, s["id"]))
                s["status"], s["n"] = status, len(initial)
            if s["status"] == "entering":
                self._enter(s, now)

    def _enter(self, s, now):
        prices = {sym: self.open_price(sym, s["decision_ms"], now) for sym in LEGS}
        if any(p is None for p in prices.values()):
            if now-s["decision_ms"] > PRICE_GIVEUP_MS:
                with self.write_lock, self.db() as db:
                    db.execute("UPDATE signals SET status='price_missing',error=? WHERE id=?",
                               ("no OKX 1m open for " + ",".join(k for k, v in prices.items() if v is None), s["id"]))
            return False
        with self.write_lock, self.db() as db:
            if db.execute("SELECT status FROM signals WHERE id=?", (s["id"],)).fetchone()["status"] != "entering":
                return False
            equity = self.account(db)["equity"]
            for sym in LEGS:
                db.execute("INSERT OR IGNORE INTO positions (signal_id,symbol,funded,notional,entry_ms,entry,exit_due_ms,stage) "
                           "VALUES (?,?,?,?,?,?,?, 'open')", (s["id"], sym, int(sym == FUNDED), equity, s["decision_ms"],
                                                              prices[sym], s["decision_ms"]+HOLD_MS))
            db.execute("UPDATE signals SET status='taken' WHERE id=?", (s["id"],))
            text = (f"全市场下轨宽度信号 · {TRACK_KEY}（模拟）\n"
                    f"{bj(s['bar_ms'])} 这根5分钟K：{s['n']} 个币首次碰周一下轨，之后30分钟无新币\n"
                    f"{bj(s['decision_ms'])} 模拟买入 SOL {prices[FUNDED]:g}（计资金 {equity:.2f}U，1倍）\n"
                    f"对照：BTC {prices['BTC-USDT-SWAP']:g}，ETH {prices['ETH-USDT-SWAP']:g}\n"
                    f"{bj(s['decision_ms']+HOLD_MS)} 按开盘价平仓。只模拟，不下单")
            self._notice(db, s["id"], "entry", text, now)
        return True

    def settle(self, now):
        """Close legs at the OKX 1m open six days after entry."""
        with self.db() as db:
            due = [dict(r) for r in db.execute("SELECT * FROM positions WHERE stage='open' AND exit_due_ms+?<=? ORDER BY id",
                                               (GRACE_MS, now))]
        closed = set()
        for p in due:
            price = self.open_price(p["symbol"], p["exit_due_ms"], now)
            if price is None:
                if now-p["exit_due_ms"] > PRICE_GIVEUP_MS:
                    with self.write_lock, self.db() as db:
                        db.execute("UPDATE positions SET error='exit price unavailable' WHERE id=?", (p["id"],))
                continue
            gross = (price/p["entry"]-1)*1e4
            fee = 2*TAKER_BP
            net = gross-fee
            pnl = p["notional"]*net/1e4
            with self.write_lock, self.db() as db:
                if db.execute("UPDATE positions SET stage='closed',exit_ms=?,exit=?,gross_bp=?,fee_bp=?,net_bp=?,pnl=?,error=NULL "
                              "WHERE id=? AND stage='open'", (p["exit_due_ms"], price, gross, fee, net, pnl, p["id"])).rowcount and p["funded"]:
                    db.execute("UPDATE account SET equity=equity+? WHERE id=1", (pnl,))
            closed.add(p["signal_id"])
        for sid in closed:
            with self.write_lock, self.db() as db:
                legs = [dict(r) for r in db.execute("SELECT * FROM positions WHERE signal_id=? ORDER BY funded DESC, symbol", (sid,))]
                if not legs or any(l["stage"] != "closed" for l in legs):
                    continue
                db.execute("UPDATE signals SET status='closed' WHERE id=?", (sid,))
                equity = self.account(db)["equity"]
                body = "，".join(f"{base(l['symbol'])} {l['entry']:g}→{l['exit']:g} 净{l['net_bp']/100:+.2f}%" for l in legs)
                text = (f"全市场下轨宽度信号 · {TRACK_KEY} 平仓（模拟）\n{body}\n"
                        f"SOL 计资金，账户 {equity:.2f}U；BTC/ETH 只作对照")
                self._notice(db, sid, "exit", text, now)

    # ---- notifications ------------------------------------------------------------------------------------------
    def _notice(self, db, signal_id, kind, text, now):
        db.execute("INSERT OR IGNORE INTO notices (signal_id,kind,text,telegram,bark,created_ms) VALUES (?,?,?,?,?,?)",
                   (signal_id, kind, text, "pending" if self.telegram_creds else "no_channel",
                    "pending" if self.bark_key else "no_channel", now))

    def deliver(self):
        with self.db() as db:
            pending = [dict(r) for r in db.execute("SELECT * FROM notices WHERE telegram='pending' OR bark='pending' ORDER BY id")]
        for n in pending:
            errors = []
            if n["telegram"] == "pending":
                self._mark(n["id"], "telegram", "attempting")
                try:
                    r = self.sender("https://api.telegram.org/bot"+self.telegram_creds[0]+"/sendMessage",
                                    json={"chat_id": self.telegram_creds[1], "text": n["text"], "disable_web_page_preview": True},
                                    timeout=(6, 15), allow_redirects=False)
                    ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("ok") is True
                    self._mark(n["id"], "telegram", "sent" if ok else ("unknown" if r.status_code >= 500 else "failed"))
                except Exception as err:
                    self._mark(n["id"], "telegram", "unknown"); errors.append(f"telegram {type(err).__name__}")
            if n["bark"] == "pending":
                self._mark(n["id"], "bark", "attempting")
                lines = n["text"].split("\n")
                try:
                    r = self.sender("https://api.day.app/push", json={"title": lines[0], "body": "\n".join(lines[1:]),
                                    "group": TRACK_KEY, "device_key": self.bark_key}, timeout=(6, 15), allow_redirects=False)
                    ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("code") == 200
                    self._mark(n["id"], "bark", "sent" if ok else ("unknown" if r.status_code >= 500 else "failed"))
                except Exception as err:
                    self._mark(n["id"], "bark", "unknown"); errors.append(f"bark {type(err).__name__}")
            if errors:
                with self.db() as db:
                    db.execute("UPDATE notices SET error=? WHERE id=?", ("; ".join(errors), n["id"]))

    def _mark(self, notice_id, channel, status):
        with self.db() as db:
            db.execute(f"UPDATE notices SET {channel}=? WHERE id=?", (status, notice_id))

    # ---- worker and read model ----------------------------------------------------------------------------------
    def step(self, now=None):
        now = self.clock() if now is None else now
        self.scan(now)
        self.evaluate(now)
        self.settle(now)
        self.deliver()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="breadth-paper", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _run(self):
        while not self.stop.is_set():
            try:
                self.step()
                self.error = None
            except Exception as err:  # keep the worker alive; the snapshot shows the last error
                self.error = f"{type(err).__name__}: {err}"
            self.stop.wait(20)

    def snapshot(self):
        quotes = self.quotes() or {}
        with self.db() as db:
            acct = self.account(db)
            signals = [dict(r) for r in db.execute("SELECT * FROM signals ORDER BY bar_ms DESC LIMIT 50")]
            positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY entry_ms DESC, funded DESC, symbol LIMIT 300")]
            notices = [dict(r) for r in db.execute("SELECT id,signal_id,kind,telegram,bark,error,created_ms FROM notices ORDER BY id DESC LIMIT 50")]
        for s in signals:
            for k in ("symbols", "new_symbols"):
                s[k] = json.loads(s[k]) if s[k] else []
        for p in positions:
            if p["stage"] == "open":
                mark = (quotes.get(p["symbol"]) or {}).get("price")
                p["mark"] = mark
                p["unrealized_bp"] = (mark/p["entry"]-1)*1e4-2*TAKER_BP if mark else None
        funded_closed = [p for p in positions if p["funded"] and p["stage"] == "closed"]
        return dict(rule=RULE, now_ms=self.clock(), account=dict(acct, start_equity=START_EQUITY,
                    closed=len(funded_closed), wins=sum(1 for p in funded_closed if p["net_bp"] > 0)),
                    signals=signals, positions=positions, notices=notices,
                    worker=dict(alive=bool(self.thread and self.thread.is_alive()), error=self.error))


    # ---- paper-page read model (Stitch 2762455ffefe4f5a9784c2e2e0fd4db5) ----------------------------------------
    # The paper page lists every account from /api/monday-reclaim-paper. These methods shape this ledger like that
    # snapshot so the existing picker, account card, trade book and chart can show it. Ids are offset so they can
    # never collide with reclaim-ledger ids in shared lists; there is no manual close (remaining_notional is None).
    def paper_row(self, p, quotes=None, now=None):
        closed = p["stage"] == "closed"
        row = dict(id=ID_OFFSET+p["id"], track=TRACK_ID, signal_id=ID_OFFSET+p["signal_id"], symbol=p["symbol"], side=1,
                   entry=p["entry"], target=None, fill_ms=p["entry_ms"], decision_ms=p["exit_due_ms"],
                   stage="closed" if closed else "holding", be_from_ms=None, backstop=None, be_price=None, parent_id=None,
                   taken=int(p["funded"]), notional=p["notional"], remaining_notional=None, realized_pnl=0., close_revision=0,
                   exit_ms=p["exit_ms"], exit_price=p["exit"], reason="time_6d" if closed else None, gross_bp=p["gross_bp"],
                   fee_bp=p["fee_bp"], net_bp=p["net_bp"], net_slip_bp=p["net_bp"], pnl=p["pnl"],
                   note=None if p["funded"] else "comparison", score=None, ema_order=None, score_basis=None, exits=[],
                   error=p["error"])
        if not closed:
            q = (quotes or {}).get(p["symbol"]) or {}
            mark = q.get("price")
            row.update(mark=mark, quote_ms=q.get("received_ms"),
                       unrealized_bp=(mark/p["entry"]-1)*1e4 if isinstance(mark, (int, float)) and mark > 0 else None)
        return row

    def paper_signal(self, s):
        return dict(id=ID_OFFSET+s["id"], symbol=FUNDED, side=1, candle_ms=s["bar_ms"], observed_ms=s["evaluated_ms"] or s["created_ms"],
                    decision_ms=s["decision_ms"], n=s["n"], new_n=s["new_n"], status=s["status"], breadth=True,
                    score=None, error=s["error"])

    def paper_track(self, quotes=None, detail=True, today=False, day_start_ms=None):
        now = self.clock()
        with self.db() as db:
            acct = self.account(db)
            rows = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY entry_ms DESC, funded DESC, symbol")]
            signals = [dict(r) for r in db.execute("SELECT * FROM signals ORDER BY bar_ms DESC LIMIT 100")]
        shaped = [self.paper_row(p, quotes, now) for p in rows]
        open_rows = [r for r in shaped if r["stage"] != "closed"]
        closed = [r for r in shaped if r["stage"] == "closed"]
        closed.sort(key=lambda r: (r["exit_ms"], r["id"]), reverse=True)
        funded_closed = [r for r in closed if r["taken"] == 1]
        mean = lambda xs: sum(xs)/len(xs) if xs else None
        latest = next((r for r in sorted(shaped, key=lambda r: (r["fill_ms"], r["id"]), reverse=True) if r["taken"] == 1), None)
        track = dict(confirm_minutes=None, rule=TRACK_KEY, equity=acct["equity"], start_equity=START_EQUITY, started_ms=acct["started_ms"],
                     open=open_rows, closed=closed[:100] if detail else [], pending_adds=[], latest_funded=latest,
                     stats=dict(shadow_closed=len(closed), shadow_mean_net_bp=mean([r["net_bp"] for r in closed]),
                                taken_closed=len(funded_closed), wins=sum(1 for r in funded_closed if r["net_bp"] > 0),
                                mean_net_bp=mean([r["net_bp"] for r in funded_closed])))
        if today:
            track["record_closed"] = [r for r in closed if day_start_ms <= r["exit_ms"] < day_start_ms+DAY] if detail else []
        if not detail:
            track["detail_omitted"] = True
        config = dict(RULE, signals=[self.paper_signal(s) for s in signals], now_ms=now)
        return track, config

    def paper_analytics(self, quotes, now, days, connected):
        from .reclaim_analytics import build_analytics
        with self.db() as db:
            acct = self.account(db)
            rows = [dict(r) for r in db.execute("SELECT * FROM positions")]
        positions = [self.paper_row(p, quotes, now) for p in rows]
        account = dict(track=TRACK_ID, started_ms=acct["started_ms"], equity=acct["equity"], start_equity=START_EQUITY, refills=[])
        return build_analytics(positions, [], account, quotes, now, days, connected)

    def paper_history(self, tab, scope, q, offset, limit, sort, day_start_ms):
        """Rows before today, like ReclaimPaper.record_history, for the signal or closed tab."""
        needle = q.strip().upper()
        reverse = sort != "time_asc"
        with self.db() as db:
            if tab == "events":
                items = [self.paper_signal(dict(r)) for r in db.execute("SELECT * FROM signals WHERE bar_ms<?", (day_start_ms,))]
                items = [r for r in items if not needle or needle in r["symbol"]]
                items.sort(key=lambda r: (r["candle_ms"], r["id"]), reverse=reverse)
            else:
                items = [self.paper_row(dict(r)) for r in db.execute("SELECT * FROM positions WHERE stage='closed' AND exit_ms<?", (day_start_ms,))]
                items = [r for r in items if (scope == "all" or (scope == "funded") == (r["taken"] == 1)) and (not needle or needle in r["symbol"])]
                items.sort(key=lambda r: (r["exit_ms"], r["id"]), reverse=reverse)
        page = items[offset:offset+limit]
        return dict(rows=page, total=len(items), has_more=offset+len(page) < len(items), now_ms=self.clock(), day_start_ms=day_start_ms)


def install(app, engine):
    app.state.breadth_paper = engine

    @app.get("/api/breadth-paper")
    def breadth_paper_snapshot():
        return JSONResponse(engine.snapshot())
