"""Synthetic checks for the independent scalar support auditor."""
from datetime import datetime, timedelta, timezone

from yoyo.evaluation.hourly_impulse_volume_wave_audit import scalar, states, same


def rows(n=110, direction=1, volume=10):
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    return [dict(open_time=(start+timedelta(hours=i)).isoformat(), open="100", high="102",
                 low="98", close=str(100+direction), volume=str(volume)) for i in range(n)]


def test_constant_doji_zero_wave_and_no_selection():
    data = rows(direction=0)
    trace = scalar(data)
    assert all(x["wave"]==0 for x in trace.values())
    t = data[-1]["open_time"]
    req = [dict(event_id="x", signal_time=t,
        decision_time=(datetime.fromisoformat(t)+timedelta(hours=1)).isoformat(),direction="1")]
    assert states(req, trace)["x"]["state"] == "abstain"


def test_zero_volume_uses_one_and_stays_zero():
    trace = scalar(rows(volume=0))
    assert all(x["total"]==1 and x["wave"]==0 for x in trace.values())


def test_gap_resets_scalar():
    data = rows()
    data.pop(100)
    trace = scalar(data)
    assert trace[datetime.fromisoformat(data[-1]["open_time"])]["count"]==9


def test_warmup_boundary():
    data = rows()
    trace = scalar(data)
    requests = [dict(event_id=str(i),signal_time=data[i]["open_time"],
        decision_time=(datetime.fromisoformat(data[i]["open_time"])+timedelta(hours=1)).isoformat(),
        direction="1") for i in (100,101)]
    result = states(requests, trace)
    assert result["100"]["state"]=="unknown"
    assert result["101"]["known"] is True


def test_scalar_buy_ema_first_two():
    data = rows(2)
    data[1]["close"]="99"
    values=list(scalar(data).values())
    same(10*19/21,values[1]["buy"])
    same(10*2/21,values[1]["sell"])
