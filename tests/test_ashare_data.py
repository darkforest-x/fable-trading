"""Offline source integrity and past-universe selection checks."""
import hashlib
import json
import signal
from types import SimpleNamespace

import pandas as pd
import pytest

from yoyo.evaluation.ashare_data import (
    AShareDataError, DAILY_FIELDS, board_for_code, cached_query,
    merge_adjusted, query_timeout, result_frame, select_universe, validate_daily,
)


class Result:
    error_code = "0"
    error_msg = "success"

    def __init__(self, frame):
        self.fields = list(frame.columns)
        self.rows = frame.astype(str).values.tolist()
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


def daily(adjustment="3"):
    factor = 2 if adjustment == "1" else 1
    rows = []
    for date, close in [("2020-01-02", 10), ("2020-01-03", 11)]:
        values = [date, "sh.600000", 10*factor, 12*factor, 9*factor,
                  close*factor, 10*factor, 100, 1000, adjustment, 1, 1, 0, 0]
        rows.append(list(map(str, values)))
    return pd.DataFrame(rows, columns=DAILY_FIELDS.split(","))


def valid(frame, adjustment="3"):
    return validate_daily(frame, code="sh.600000", start="2020-01-01", end="2020-01-10", adjustment=adjustment)


def test_past_universe_selection_is_order_status_name_independent():
    codes = ["sh.600000", "sh.600001", "sz.000001", "sz.000002", "sz.300001", "sh.688001", "sh.000300", "sh.900901"]
    frame = pd.DataFrame({"code": codes, "tradeStatus": "1", "code_name": "old"})
    kwargs = dict(day="2020-01-02", quotas=dict(main_sh=1, main_sz=1, chinext=1, star=1), seed="spike-ashare-v1")
    first = select_universe(frame, **kwargs)
    changed = frame.iloc[::-1].copy()
    changed["code_name"] = "future renamed ST"
    changed["tradeStatus"] = "0"
    second = select_universe(changed, **kwargs)
    assert first.code.tolist() == second.code.tolist()
    assert len(first) == 4
    assert not {"sh.000300", "sh.900901"} & set(first.code)
    assert first[first.board == "main_sh"].code.iloc[0] == min(codes[:2], key=lambda code: hashlib.sha256(f"spike-ashare-v1|{code}".encode()).hexdigest())
    assert board_for_code("sz.301001") == "chinext"
    assert board_for_code("sh.605001") == "main_sh"


def test_cache_freezes_request_and_detects_tampering(tmp_path):
    path = tmp_path / "source.csv"
    request = {"end": "2025-12-31"}
    first = cached_query(path, request, lambda: Result(daily()))
    second = cached_query(path, request, lambda: pytest.fail("no refetch"))
    pd.testing.assert_frame_equal(first, second)
    with pytest.raises(AShareDataError, match="mismatch"):
        cached_query(path, {"end": "2026-01-01"}, lambda: None)
    path.write_text(path.read_text().replace("2020-01-02", "2020-01-04"))
    with pytest.raises(AShareDataError, match="mismatch"):
        cached_query(path, request, lambda: None)


@pytest.mark.parametrize("mutation", ["outside", "duplicate", "order", "price", "code", "status"])
def test_invalid_source_fails_closed(mutation):
    frame = daily()
    if mutation == "outside": frame.loc[1, "date"] = "2026-01-02"
    if mutation == "duplicate": frame.loc[1, "date"] = "2020-01-02"
    if mutation == "order": frame = frame.iloc[::-1]
    if mutation == "price": frame.loc[1, "high"] = "5"
    if mutation == "code": frame.loc[1, "code"] = "sz.000001"
    if mutation == "status": frame.loc[1, "isST"] = ""
    with pytest.raises(AShareDataError): valid(frame)


def test_same_date_adjustment_alignment_and_metadata_preserved():
    raw, adjusted = valid(daily()), valid(daily("1"), "1")
    merged = merge_adjusted(raw, adjusted)
    assert merged.close.tolist() == [20, 22]
    assert merged.raw_close.tolist() == [10, 11]
    assert merged.factor.tolist() == [2, 2]
    assert merged.volume.tolist() == [100, 100]
    pd.testing.assert_frame_equal(merge_adjusted(raw.iloc[:1], adjusted.iloc[:1]), merged.iloc[:1])
    adjusted.loc[1, "open"] = 19
    with pytest.raises(AShareDataError, match="ratios"):
        merge_adjusted(raw, adjusted)


def test_suspension_is_retained_without_invented_volume():
    frame = daily()
    frame.loc[0, ["tradestatus", "isST"]] = ["0", "1"]
    frame.loc[0, ["volume", "amount", "turn"]] = ""
    result = valid(frame)
    assert len(result) == 2 and result.tradestatus.iloc[0] == 0 and result.isST.iloc[0] == 1
    assert pd.isna(result.volume.iloc[0])


def test_errors_after_paging_are_not_partial_success():
    class Broken(Result):
        def next(self):
            if self.index == 0:
                self.error_code = "99"
                return False
            return super().next()
    with pytest.raises(AShareDataError, match="final paging"):
        result_frame(Broken(daily()))


def test_empty_history_is_preserved_not_replaced_with_other_stock():
    raw = valid(daily().iloc[:0])
    adjusted = valid(daily("1").iloc[:0], "1")
    result = merge_adjusted(raw, adjusted)
    assert result.empty
    assert {"raw_close", "factor", "board"}.issubset(result.columns)


def test_adjusted_source_dates_must_match_raw_even_if_prices_are_valid():
    raw, adjusted = valid(daily()), valid(daily("1"), "1")
    adjusted.loc[1, "date"] = "2020-01-06"
    with pytest.raises(AShareDataError, match="do not align"):
        merge_adjusted(raw, adjusted)


def test_sdk_silent_full_page_failure_is_not_success():
    result = Result(daily())
    result.data = result.rows
    result.per_page_count = str(len(result.rows))
    with pytest.raises(AShareDataError, match="full page"):
        result_frame(result)


def test_wall_timeout_escapes_sdk_exception_handler_without_partial_cache(tmp_path):
    def swallowed_exception_query():
        try:
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        except Exception:
            return Result(daily())
    with pytest.raises(AShareDataError, match="wall-clock timeout"):
        with query_timeout():
            cached_query(tmp_path / "partial.csv", {}, swallowed_exception_query)
    assert not (tmp_path / "partial.csv").exists()


def test_worker_reuses_one_session_without_per_stock_logout(monkeypatch):
    import yoyo.evaluation.ashare_data as source
    calls = []
    client = SimpleNamespace(login=lambda: (calls.append("login") or SimpleNamespace(error_code="0")))
    monkeypatch.setattr(source, "_PROCESS_CLIENT", None)
    monkeypatch.setattr(source.importlib.metadata, "version", lambda _: source.BAOSTOCK_VERSION)
    monkeypatch.setattr(source.importlib, "import_module", lambda _: client)
    monkeypatch.setattr(source.atexit, "register", lambda callback: calls.append("register_socket_close"))
    assert source._worker_client() is client
    assert source._worker_client() is client
    assert calls == ["login", "register_socket_close"]


def test_worker_finalizer_closes_only_its_socket(monkeypatch):
    import yoyo.evaluation.ashare_data as source
    calls = []
    connection = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(source.importlib, "import_module", lambda _: SimpleNamespace(default_socket=connection))
    source._close_worker_socket()
    assert calls == ["close"]


def test_mainboard_only_subset_preserves_same_past_code_selection():
    frame = pd.DataFrame({"code": ["sh.600000", "sh.600001", "sz.000001", "sz.000002", "sz.300001", "sh.688001"],
                          "tradeStatus": "1", "code_name": "name"})
    kwargs = dict(day="2020-01-02", seed="spike-ashare-v1")
    broad = select_universe(frame, quotas=dict(main_sh=1, main_sz=1, chinext=1, star=1), **kwargs)
    main = select_universe(frame, quotas=dict(main_sh=1, main_sz=1), **kwargs)
    assert main.code.tolist() == broad[broad.board.isin(["main_sh", "main_sz"])].code.tolist()
    assert len(main) == 2
    with pytest.raises(AShareDataError): select_universe(frame, quotas={}, **kwargs)
    with pytest.raises(AShareDataError): select_universe(frame, quotas={"unknown": 1}, **kwargs)


def test_failed_worker_connection_is_discarded_without_logout(monkeypatch):
    import yoyo.evaluation.ashare_data as source
    client, calls = object(), []
    monkeypatch.setattr(source, "_PROCESS_CLIENT", client)
    monkeypatch.setattr(source, "_close_worker_socket", lambda: calls.append("close"))
    monkeypatch.setattr(source, "fetch_daily", lambda *args, **kwargs: (_ for _ in ()).throw(AShareDataError("timeout")))
    result = source._fetch_worker(("/tmp", "sh.600000", "2020-01-01", "2025-12-31"))
    assert "timeout" in result["error"]
    assert source._PROCESS_CLIENT is None
    assert calls == ["close"]
