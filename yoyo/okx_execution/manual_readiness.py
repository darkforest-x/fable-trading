"""Explain the existing manual entry gate without disclosing raw exceptions.

Connectivity, worker readiness and order approval are distinct. This local
read-only projection never clears errors or authorizes an exchange operation.
"""
from .dashboard import token
from .manual_engine import MANUAL_ENGINE_REVISION

READ_RETRY = "GET response unavailable; preview again"

class EntryBlocked(ValueError):
    def __init__(self, details):
        super().__init__("manual executor is stopped" if details["code"] == "worker_stopped"
                         else "manual executor reconciliation pending")
        self.details = details


def entry_status(store, now_ms, locked):
    def result(code, title, message, issues=None):
        return dict(status="ready" if code == "ready" else "blocked", code=code,
                    title=title, message=message, issues=issues or [], checked_ms=now_ms)
    if not locked:
        return result("worker_stopped", "执行服务未运行", "请先恢复账户执行服务，再预览订单。")
    if not 0 <= now_ms-int(store.meta("heartbeat_ms", 0)) < 60_000:
        return result("worker_stale", "执行服务响应中断", "尚未收到最近一分钟内的执行心跳，请检查服务连接。")
    if store.meta("entries_enabled") is not True:
        return result("entries_disabled", "账户未启用新开仓", "当前执行服务只管理已有仓位。")
    errors = store.meta("errors") or []
    if errors:
        trades = {r["id"]: r for r in store.trades()}
        issues = []
        for error in errors if isinstance(errors, list) else []:
            row = trades.get(error.get("id"), {})
            mismatch = error.get("error") == "exchange positions do not match owned signal lots"
            reading = error.get("error") == READ_RETRY
            issues.append(dict(id=token(row.get("id")), symbol=token(row.get("symbol")),
                               state=token(row.get("state")), code="position_mismatch" if mismatch else "read_unavailable" if reading else "order_reconciliation",
                               message="本地仓位与交易所数量不一致" if mismatch else "交易所订单查询暂未返回" if reading else "原订单状态尚未核对完成"))
        if issues and all(i["code"] == "read_unavailable" for i in issues):
            return result("exchange_read_retry", "交易所连接暂不稳定",
                          "订单查询暂未返回，后台正在重试。可继续查看图表，恢复后会更新预览。", issues)
        symbol = next((r["symbol"] for r in issues if r["symbol"]), None)
        prefix = symbol.replace("-USDT-SWAP", "")+" 的旧单" if symbol else "原订单"
        return result("reconciliation_blocked", "旧单对账阻塞新开仓",
                      prefix+"尚未完成对账。反复预览无法解除，请查看持仓与订单；后台会持续核对。", issues)
    if store.meta("worker_error"):
        if store.meta("worker_error") == READ_RETRY:
            return result("exchange_read_retry", "交易所连接暂不稳定",
                          "持仓与订单查询暂未返回，后台正在重试。可继续查看图表，恢复后会更新预览。")
        return result("worker_check_failed", "账户核验暂未完成",
                      "后台本轮账户核验未完成，正在自动重试；恢复后会重新计算预览，无需重复点击。")
    if store.meta("engine_revision") != MANUAL_ENGINE_REVISION:
        return result("worker_version", "执行服务需要更新", "执行服务与当前页面版本不一致，暂不能预览订单。")
    return result("ready", "账户已就绪", "可以计算订单预览，实际开仓仍需逐笔确认。")
