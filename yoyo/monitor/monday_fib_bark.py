"""Bark delivery for the existing weekly first-touch ledger, without a scanner.

Uses the existing Bark V2 POST contract (Finb/bark-server/docs/API_V2.md).
The owner's 1.618 destination is topic-local: changing it cannot redirect other
signal notifications. Only explicit rate limits retry; ambiguous sends do not.
"""
from .bark import SERVER, TV_APP_URL, credentials
from .monday_fib import claim_scored_notification, notification_text


def configuration_directory(book):
    return book.path.parent / "monday-fib"


def configured_credentials(book):
    return credentials(configuration_directory(book))


def message(event, score):
    tv = event["symbol"].removesuffix("-SWAP").replace("-", "") + ".P"
    title, body = notification_text(event, score).split("\n", 1)
    return {"title": title, "body": body,
            "group": "SPIKE 1.618", "level": "active", "isArchive": "1", "ttl": 600,
            "copy": f"OKX:{tv}", "url": TV_APP_URL}


def deliver_once(book, creds, sender, now, *, score_lookup=None, clock=None):
    if not creds:
        return False
    event = book.pending_bark(now)
    if event is None:
        return False
    prepared = claim_scored_notification(book, event, now, score_lookup=score_lookup, clock=clock, channel="bark")
    if prepared is None:
        return True
    event, score, ready_at = prepared
    try:
        response = sender(SERVER + "/push", json={**message(event, score), "device_key": creds},
                          timeout=(6, 15), allow_redirects=False)
        code = response.status_code
        if code == 429:
            delay = response.headers.get("Retry-After", "30")
            delay = max(5, min(3600, int(delay))) if str(delay).isdigit() else 30
            if event["attempts"] < 5:
                book.finish_bark(event, "pending", "bark_rate_limited", due_ms=ready_at + delay * 1000)
            else:
                book.finish_bark(event, "failed", "bark_rate_limit_exhausted")
            return True
        if 400 <= code < 500:
            book.finish_bark(event, "failed", "bark_rejected_" + str(code))
            return True
        payload = response.json()
        if (code != 200 or not isinstance(payload, dict) or type(payload.get("code")) is not int
                or payload["code"] != 200 or type(payload.get("timestamp")) is not int or payload["timestamp"] <= 0):
            raise ValueError("bark_acceptance_not_confirmed")
        book.finish_bark(event, "sent", server_timestamp=payload["timestamp"])
    except Exception:
        book.finish_bark(event, "unknown", "bark_delivery_uncertain_no_automatic_resend")
    return True
