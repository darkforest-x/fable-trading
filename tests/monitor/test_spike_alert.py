"""Market-wide spike alert: threshold, window, cooldown, live-only, per side, delivery receipts."""
import json
import tempfile
import unittest

from yoyo.monitor.spike_alert import THRESHOLD, SpikeAlert, alert_text, install

MIN = 60_000
T0 = 1_792_000_000_000 // MIN * MIN


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class SpikeAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def sender(self, url, json=None, **kwargs):
        self.sent.append((url, json))
        return FakeResponse(200, {"ok": True, "result": {"message_id": 1}} if "telegram" in url else {"code": 200, "timestamp": 1})

    def alert(self, threshold=8, **kw):
        return SpikeAlert(self.tmp.name, threshold=threshold, telegram_creds=("token", "chat"), bark_key="k"*22, sender=self.sender,
                          clock=lambda: T0, **kw)

    def test_owner_threshold_is_25_coins(self):
        self.assertEqual(THRESHOLD, 25)
        a = SpikeAlert(self.tmp.name, telegram_creds=("t", "c"), bark_key="", sender=self.sender, clock=lambda: T0)
        self.touch(a, 24)
        self.assertEqual(a.events.qsize(), 0)
        self.touch(a, 1, start=24, minute=3)
        self.assertEqual((a.events.qsize(), a.snapshot()["threshold"]), (1, 25))

    def touch(self, a, n, side="lower", start=0, minute=0, source="live"):
        for k in range(start, start+n):
            a.on_touch({}, f"C{k}-USDT-SWAP", side, 1., T0, T0+minute*MIN, source)

    def test_seven_coins_do_not_alert_and_the_eighth_does(self):
        a = self.alert()
        self.touch(a, 7)
        self.assertEqual(a.events.qsize(), 0)
        self.touch(a, 1, start=7, minute=4)
        self.assertEqual(a.events.qsize(), 1)
        alert = a.events.get_nowait()
        self.assertEqual((alert["n"], alert["side"]), (8, "lower"))
        self.assertEqual(a.deliver(alert), {"telegram": "sent", "bark": "sent"})
        self.assertEqual([r["telegram"] for r in a.snapshot()["alerts"]], ["sent"])
        self.assertIn("8 个币同时触及下方 1.618", self.sent[0][1]["text"])

    def test_touches_older_than_five_minutes_drop_out(self):
        a = self.alert()
        self.touch(a, 7)
        self.touch(a, 1, start=7, minute=5)
        self.assertEqual(a.events.qsize(), 0)

    def test_one_alert_per_side_per_cooldown_and_sides_are_independent(self):
        a = self.alert()
        self.touch(a, 8)
        self.touch(a, 8, start=8, minute=10)
        self.assertEqual(a.events.qsize(), 1)
        self.touch(a, 8, side="upper", minute=10)
        self.assertEqual(a.events.qsize(), 2)
        self.touch(a, 8, start=20, minute=31)
        self.assertEqual(a.events.qsize(), 3)

    def test_history_and_repeated_symbols_do_not_count(self):
        a = self.alert()
        self.touch(a, 10, source="history")
        for _ in range(10):
            a.on_touch({}, "SAME-USDT-SWAP", "lower", 1., T0, T0, "live")
        self.assertEqual(a.events.qsize(), 0)

    def test_cooldown_survives_restart(self):
        a = self.alert()
        self.touch(a, 8)
        b = self.alert()
        self.touch(b, 8, start=8, minute=5)
        self.assertEqual(b.events.qsize(), 0)

    def test_uncertain_delivery_is_recorded_not_retried(self):
        def flaky(url, json=None, **kw):
            raise TimeoutError("slow")
        a = SpikeAlert(self.tmp.name, threshold=8, telegram_creds=("t", "c"), bark_key="", sender=flaky, clock=lambda: T0)
        self.touch(a, 8)
        self.assertEqual(a.deliver(a.events.get_nowait()), {"telegram": "unknown"})
        row = a.snapshot()["alerts"][0]
        self.assertEqual((row["telegram"], row["bark"]), ("unknown", "no_channel"))

    def test_text_lists_at_most_twelve_names(self):
        text = alert_text(dict(side="upper", last_ms=T0, n=15, symbols=[f"S{k}-USDT-SWAP" for k in range(15)]))
        self.assertIn("等 15 个", text); self.assertIn("上方 1.618", text); self.assertIn("只通知", text)

    def test_api_is_read_only(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        a = self.alert()
        self.touch(a, 8)
        app = FastAPI(); install(app, a)
        r = TestClient(app).get("/api/spike-alerts")
        self.assertEqual((r.status_code, r.json()["threshold"], len(r.json()["alerts"])), (200, 8, 1))
        self.assertTrue(r.json()["notify_only"])


if __name__ == "__main__":
    unittest.main()
