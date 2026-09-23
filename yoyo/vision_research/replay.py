"""Persistent, causal historical observations, independent labels and followups.

Historical rows stay server-side. Only 120 already-disclosed closed bars reach
the browser; the exact fixed PNG shown at freeze is sent to the visual model.
Labels and future observations are separate from provider decisions and never
become gold or trade outcomes automatically. Sources for the API composition:
https://fastapi.tiangolo.com/tutorial/bigger-applications/
https://www.starlette.io/threadpool/
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from .images import MAX_TOTAL_IMAGE_BYTES, image_from_bytes
from .schemas import DEFAULT_CRITERIA
from .source import CHART_COLORS, SourceError, chart_sha256, render_chart
from .store import utc_now
from .zhipu import PROMPT_VERSION, ZhipuError
from .replay_cases import PUBLIC_KEYS, V128, HistoricalCaseCatalog, in_bucket


class ReplayConflict(ValueError):
    pass


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewSession(Input):
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str = Field(min_length=1, max_length=8)
    start_ms: int = Field(ge=0)
    mode: Literal["free", "blind"] = "free"


class CaseSession(Input):
    mode: Literal["free", "blind"] = "blind"
    selection_bucket: Literal["all", "ge3", "ge5", "gt10", "loss", "other", "open"] = "all"


class Move(Input):
    expected_cursor_ms: int = Field(ge=0)
    steps: int | None = None
    target_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def one_movement(self):
        if (self.steps is None) == (self.target_ms is None):
            raise ValueError("Choose steps or target time")
        if self.steps is not None and self.steps not in (-10, -5, -1, 1, 5, 10):
            raise ValueError("Invalid step count")
        return self


class Freeze(Input):
    expected_cursor_ms: int = Field(ge=0)
    criteria: str = Field(default=DEFAULT_CRITERIA, min_length=10, max_length=8000)
    reference_revision: int | None = Field(default=None, ge=0)


class Judgment(Input):
    current_state: Literal["converging", "launching", "extended", "no_setup", "unclear"]
    side: Literal["long", "short", "unknown"]
    note: str = Field(default="", max_length=4000)


class Followup(Input):
    expected_cursor_ms: int = Field(ge=0)
    note: str = Field(default="", max_length=4000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ReplayResearch:
    def __init__(self, store, history, cases=None):
        self.store, self.history = store, history
        self.cases = cases or HistoricalCaseCatalog()
        self.lock = threading.RLock()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS replay_case_exposures "
                       "(event_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, reason TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS replay_sessions "
                       "(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS replay_observations "
                       "(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, cursor_ms INTEGER NOT NULL, "
                       "record TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS replay_session_observations ON replay_observations(session_id,cursor_ms)")
            for oid, raw in db.execute("SELECT id,record FROM replay_observations").fetchall():
                obs = json.loads(raw)
                if obs.get("status") == "running":
                    run = store.get(obs.get("run_id"))
                    obs["status"] = run["status"] if run and run.get("status") in {"completed", "failed"} else "interrupted"
                    db.execute("UPDATE replay_observations SET record=? WHERE id=?", (_json(obs), oid))

    def _get(self, table, identity):
        # Table names are internal constants, never client input.
        with self.store.connect() as db:
            row = db.execute(f"SELECT record FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    def _save_session(self, session):
        with self.store.connect() as db:
            db.execute("INSERT INTO replay_sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                       (session["id"], session["created_at"], _json(session)))

    def _save_obs(self, obs):
        with self.store.connect() as db:
            db.execute("INSERT INTO replay_observations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                       (obs["id"], obs["session_id"], obs["cursor_ms"], _json(obs)))

    def _cursor(self, session):
        return session["rows"][session["cursor_index"]]["t"] + session["duration_ms"]

    def _expect(self, session, expected):
        if self._cursor(session) != expected:
            raise ReplayConflict("回放位置已在其他操作中改变，请刷新当前会话后再试。")

    def _exposed(self, case):
        if not case:
            return False
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM replay_case_exposures WHERE event_key=?",
                              (case["exposure_key"],)).fetchone() is not None

    def _expose(self, case, reason):
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO replay_case_exposures VALUES(?,?,?)",
                       (case["exposure_key"], utc_now(), reason))

    def _case_view(self, session):
        case = session.get("case_record")
        if not case:
            return None
        known = self._exposed(case)
        return {**{k: case[k] for k in PUBLIC_KEYS}, "dataset": case["dataset"],
                "ledger_sha256": case["ledger_sha256"], "original_event_key": case["native_key"],
                "selection_bucket": session["selection_bucket"], "outcome_known": known,
                "outcome_revealed": bool(session.get("outcome_revealed_at")),
                "outcome_revealed_at": session.get("outcome_revealed_at"),
                "outcome": case["outcome"] if session.get("outcome_revealed_at") else None,
                "warning": ("该行情曾显示结果分类或后续信息；新判断仅作学习，不计独立盲审。" if known
                            else "结果暂未揭晓；先在信号收盘时保存判断。")}

    def case_list(self, dataset=V128, bucket="all", symbol="", timeframe="", offset=0, limit=20):
        with self.lock:
            result = self.cases.list(dataset, bucket, symbol, timeframe, offset, limit)
            if bucket != "all":
                for item in result["items"]:
                    self._expose(self.cases.get(item["id"]), "result_bucket:" + bucket)
            return result

    def create_case(self, identity, body):
        with self.lock:
            case = self.cases.get(identity)
            if not in_bucket(case, body.selection_bucket):
                raise ReplayConflict("该案例不在所选结果分组内，请刷新列表。")
            if body.selection_bucket != "all":
                self._expose(case, "result_bucket:" + body.selection_bucket)
            data = self.cases.load_history(case)
            session = {**data, "id": uuid.uuid4().hex, "created_at": utc_now(),
                       "symbol": case["symbol"], "timeframe": case["timeframe"],
                       "mode": "free" if self._exposed(case) else body.mode,
                       "case_record": case, "selection_bucket": body.selection_bucket,
                       "outcome_revealed_at": None}
            session["seen_until_ms"] = self._cursor(session)
            if self._cursor(session) != case["signal_close_ms"]:
                raise ReplayConflict("行情没有精确停在信号收盘，已拒绝创建替代时点。")
            self._save_session(session)
            return self.public_session(session)

    def reveal_outcome(self, identity):
        with self.lock:
            session = self._get("replay_sessions", identity)
            case = session.get("case_record")
            if not case:
                raise ReplayConflict("这个会话没有关联历史信号结果。")
            if session["mode"] == "blind":
                with self.store.connect() as db:
                    rows = db.execute("SELECT record FROM replay_observations WHERE session_id=? AND cursor_ms=?",
                                      (identity, case["signal_close_ms"])).fetchall()
                if not any(json.loads(r[0]).get("human") for r in rows):
                    raise ReplayConflict("请先冻结信号时点并保存自己的判断，再揭晓历史结果。")
            if not session.get("outcome_revealed_at"):
                session["outcome_revealed_at"] = utc_now()
                self._expose(case, "historical_outcome_revealed")
                self._save_session(session)
            return self.public_session(session)

    def _chart(self, session):
        index = session["cursor_index"]
        rows = session["rows"][index - 119:index + 1]
        cutoff = self._cursor(session)
        return {"candles": rows, "chart_sha256": chart_sha256(rows), "colors": CHART_COLORS,
                "provenance": {"symbol": session["symbol"], "timeframe": session["timeframe"],
                               "time_boundary": "historical_replay", "observed_at_ms": cutoff,
                               "visible_end_ms": cutoff, "visible_start_ms": rows[0]["t"],
                               "last_bar_closed": True, "bar_count": len(rows),
                               "render_version": "replay-fixed-v1", "source_label": session["source_label"]}}

    def public_session(self, session):
        with self.store.connect() as db:
            raw = db.execute("SELECT record FROM replay_observations WHERE session_id=? ORDER BY cursor_ms,id",
                             (session["id"],)).fetchall()
        observations = []
        for (value,) in raw:
            obs = json.loads(value)
            observations.append({k: obs.get(k) for k in ("id", "cursor_ms", "image_url", "human", "run_id", "status")})
        return {**{k: session[k] for k in ("id", "symbol", "timeframe", "mode", "created_at", "seen_until_ms", "source_label")},
                "cursor_ms": self._cursor(session),
                "first_cursor_ms": session["rows"][session["first_cursor_index"]]["t"] + session["duration_ms"],
                "last_cursor_ms": session["rows"][-1]["t"] + session["duration_ms"],
                "chart": self._chart(session), "observations": observations, "case": self._case_view(session)}

    def sessions(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT record FROM replay_sessions ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"items": [{**{k: s[k] for k in ("id", "symbol", "timeframe", "mode", "created_at")},
                            "cursor_ms": self._cursor(s)} for s in (json.loads(r[0]) for r in rows)]}

    def create(self, body):
        data = self.history.load(body.symbol, body.timeframe, body.start_ms)
        session = {**data, "id": uuid.uuid4().hex, "created_at": utc_now(), "symbol": body.symbol,
                   "timeframe": body.timeframe, "mode": body.mode}
        session["seen_until_ms"] = self._cursor(session)
        with self.lock:
            self._save_session(session)
            return self.public_session(session)

    def session(self, identity):
        with self.lock:
            return self.public_session(self._get("replay_sessions", identity))

    def move(self, identity, body):
        with self.lock:
            session = self._get("replay_sessions", identity)
            self._expect(session, body.expected_cursor_ms)
            if body.steps is not None:
                target = session["cursor_index"] + body.steps
            else:
                target = next((i for i in range(len(session["rows"]) - 1, -1, -1)
                               if session["rows"][i]["t"] + session["duration_ms"] <= body.target_ms), -1)
                if body.target_ms > session["rows"][-1]["t"] + session["duration_ms"]:
                    raise ReplayConflict("目标时间超出本次已加载的连续历史范围，请新建回放。")
            if target < session["first_cursor_index"] or target >= len(session["rows"]):
                raise ReplayConflict("已到本次连续历史范围边界，播放已停止；可以换时间新建回放。")
            if session["mode"] == "blind" and target <= session["cursor_index"]:
                raise ReplayConflict("先独立判断模式只允许向前推进，回看请另建自由回放。")
            session["cursor_index"] = target
            session["seen_until_ms"] = max(session["seen_until_ms"], self._cursor(session))
            if session.get("case_record") and self._cursor(session) > session["case_record"]["signal_close_ms"]:
                self._expose(session["case_record"], "later_market_bars")
            self._save_session(session)
            return self.public_session(session)

    def observation(self, identity):
        with self.lock:
            obs = self._get("replay_observations", identity)
            session = self._get("replay_sessions", obs["session_id"])
            return {**obs, "case": self._case_view(session),
                    "run": self.store.get(obs["run_id"]) if obs.get("run_id") else None}

    def freeze(self, identity, body, model):
        with self.lock:
            session = self._get("replay_sessions", identity)
            self._expect(session, body.expected_cursor_ms)
            refs = self.store.get_references()
            if body.reference_revision is not None and refs["revision"] != body.reference_revision:
                raise ReplayConflict("参考图已变化，请重新载入参考版本后冻结。")
            criteria = body.criteria.strip()
            if len(criteria) < 10:
                raise ReplayConflict("请填写至少 10 个字符的形态标准。")
            chart = self._chart(session)
            identity_key = hashlib.sha256(_json([identity, chart["chart_sha256"], criteria, refs, model, PROMPT_VERSION]).encode()).hexdigest()
            with self.store.connect() as db:
                existing = db.execute("SELECT record FROM replay_observations WHERE session_id=? AND cursor_ms=?",
                                      (identity, self._cursor(session))).fetchall()
            for (raw,) in existing:
                prior = json.loads(raw)
                if prior.get("identity_key") == identity_key:
                    return self.observation(prior["id"])
            image = image_from_bytes(render_chart(chart["candles"], session["symbol"], session["timeframe"], self._cursor(session)), "replay.png")
            obs = {"id": uuid.uuid4().hex, "session_id": identity, "identity_key": identity_key,
                   "symbol": session["symbol"], "timeframe": session["timeframe"], "cursor_ms": self._cursor(session),
                   "mode": session["mode"], "created_at": utc_now(), "image_url": self.store.put_image(image),
                   "image_sha256": image.sha256, "chart_sha256": chart["chart_sha256"], "chart": chart,
                   "criteria": criteria, "model": model, "prompt_version": PROMPT_VERSION,
                   "reference_revision": refs["revision"], "references": refs["items"],
                   "human": None, "run_id": None, "followups": [], "status": "frozen",
                   "case_id": session.get("case_record", {}).get("id"),
                   "independence": ("outcome_known_before_judgment" if self._exposed(session.get("case_record"))
                                    else "future_seen_in_session" if session["seen_until_ms"] > self._cursor(session)
                                    else "ai_requested_before_human_judgment" if session.get("ai_exposed_until_ms", -1) >= self._cursor(session)
                                    else "not_yet_judged"),
                   "training_eligible": False, "production_eligible": False}
            self._save_obs(obs)
            return self.observation(obs["id"])

    def judge(self, identity, body):
        with self.lock:
            obs = self._get("replay_observations", identity)
            if obs["human"] is not None:
                if all(obs["human"][k] == v for k, v in body.model_dump().items()):
                    return self.observation(identity)
                raise ReplayConflict("首次独立判断已经保存，不可覆盖；可在回访备注中补充更正。")
            session = self._get("replay_sessions", obs["session_id"])
            if (obs["run_id"] or session["seen_until_ms"] > obs["cursor_ms"]
                    or session.get("ai_exposed_until_ms", -1) >= obs["cursor_ms"]):
                raise ReplayConflict("已请求过 AI 或看过后续行情，不能再补记为首次独立判断。")
            obs["human"] = {**body.model_dump(), "created_at": utc_now()}
            obs["independence"] = ("outcome_known_before_judgment" if self._exposed(session.get("case_record"))
                                   else "before_ai_and_later_bars_in_this_session")
            self._save_obs(obs)
            return self.observation(identity)

    def followup(self, identity, body):
        with self.lock:
            obs = self._get("replay_observations", identity)
            session = self._get("replay_sessions", obs["session_id"])
            self._expect(session, body.expected_cursor_ms)
            if self._cursor(session) <= obs["cursor_ms"]:
                raise ReplayConflict("请先向前回放至少一根 K 线，再保存后续观察。")
            if not obs["human"] and not obs["run_id"]:
                raise ReplayConflict("请先完成当时的判断，再追加回访。")
            chart = self._chart(session)
            previous = next((f for f in obs["followups"] if f["cursor_ms"] == self._cursor(session) and f["note"] == body.note), None)
            if previous:
                return self.observation(identity)
            image = image_from_bytes(render_chart(chart["candles"], session["symbol"], session["timeframe"], self._cursor(session)), "replay-followup.png")
            obs["followups"].append({"id": uuid.uuid4().hex, "created_at": utc_now(), "cursor_ms": self._cursor(session),
                "image_url": self.store.put_image(image), "image_sha256": image.sha256, "chart_sha256": chart["chart_sha256"],
                "bars_elapsed": (self._cursor(session) - obs["cursor_ms"]) // session["duration_ms"],
                "close_change_pct": (chart["candles"][-1]["c"] / obs["chart"]["candles"][-1]["c"] - 1) * 100,
                "note": body.note, "meaning": "descriptive_price_change_not_trade_pnl"})
            self._save_obs(obs)
            return self.observation(identity)

    def inference(self, identity, provider, inference_lock, capture_exchange, review_context):
        # Claim one attempt durably. A timeout or restart is never auto-retried.
        with self.lock:
            obs = self._get("replay_observations", identity)
            if obs.get("run_id"):
                return self.observation(identity)
            if obs["mode"] == "blind" and obs["human"] is None:
                raise ReplayConflict("请先保存你自己的判断，再交给 AI。")
            if obs["prompt_version"] != PROMPT_VERSION:
                raise ReplayConflict("识别协议已升级；请新建观察使用新版本，旧记录保留。")
            client = provider(obs["model"])
            if not inference_lock.acquire(blocking=False):
                client.close()
                raise ReplayConflict("当前有识别请求运行中，请稍后点击；尚未发起本次调用。")
            try:
                image = self._stored_image(obs)
                references = [self._stored_image(ref, reference=True) for ref in obs["references"]]
                if sum(len(item.data) for item in [image, *references]) > MAX_TOTAL_IMAGE_BYTES:
                    raise ReplayConflict("冻结输入和参考图合计超过 12 MB，请减少参考图后重新冻结。")
                context = review_context(obs["chart"]["provenance"], obs["chart"])
                run = {"id": uuid.uuid4().hex, "created_at": utc_now(), "status": "running", "model": client.model,
                       "provider": "zhipu", "symbol": obs["symbol"], "timeframe": obs["timeframe"],
                       "source": "historical_replay", "replay_observation_id": identity, "image_url": obs["image_url"],
                       "image_name": image.name, "image_sha256": image.sha256, "image_width": image.width, "image_height": image.height,
                       "criteria": obs["criteria"], "criteria_sha256": hashlib.sha256(obs["criteria"].encode()).hexdigest(),
                       "prompt_version": PROMPT_VERSION, "schema_version": 2, "provenance": obs["chart"]["provenance"],
                       "analysis_scope": "current_right_edge", "review_context": context,
                       "references": obs["references"], "reference_source": "replay_frozen", "reference_revision": obs["reference_revision"],
                       "decision": None, "usage": {}, "latency_ms": None, "error": None, "review": None,
                       "review_history": [], "training_eligible": False, "production_eligible": False}
                run["api_exchange_id"] = capture_exchange(client, "recognition", run["id"])
                self.store.save(run)
                obs.update(run_id=run["id"], status="running")
                session = self._get("replay_sessions", obs["session_id"])
                session["ai_exposed_until_ms"] = max(session.get("ai_exposed_until_ms", -1), obs["cursor_ms"])
                self._save_session(session)
                if obs["human"] is None:
                    obs["independence"] = "ai_requested_before_human_judgment"
                self._save_obs(obs)
            except BaseException:
                client.close()
                inference_lock.release()
                raise
        started = time.perf_counter()
        try:
            result = client.analyze(image=image, references=references, criteria=obs["criteria"], context=context)
            run.update(result, status="completed", completed_at=utc_now())
        except ZhipuError as exc:
            run.update(status="failed", error=str(exc), error_details=exc.diagnostics(), completed_at=utc_now())
        except Exception:
            run.update(status="failed", error="识别异常，结果未知；未自动重试。", completed_at=utc_now())
        finally:
            try:
                if run.get("latency_ms") is None:
                    run["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                self.store.finish_exchange(run["api_exchange_id"], run["status"], run.get("error"))
                self.store.save(run)
                with self.lock:
                    latest = self._get("replay_observations", identity)
                    latest["status"] = run["status"]
                    self._save_obs(latest)
            finally:
                client.close()
                inference_lock.release()
        return self.observation(identity)

    def _stored_image(self, item, reference=False):
        sha = item["sha256"] if reference else item["image_sha256"]
        if item["image_url"] != "/api/images/" + sha + ".png":
            raise ReplayConflict("冻结图片身份不一致，已拒绝调用。")
        path = self.store.image_path(sha + ".png")
        image = image_from_bytes(path.read_bytes(), item.get("name", "replay.png"))
        if image.sha256 != sha:
            raise ReplayConflict("冻结图片内容已变化，已拒绝调用。")
        return image


def install_replay_routes(app, store, provider, inference_lock, read_json, capture_exchange, review_context,
                          model_name, history=None, cases=None):
    from .replay_data import LocalReplayHistory
    replay = ReplayResearch(store, history or LocalReplayHistory(), cases=cases)
    app.state.replay = replay

    async def body(request, model):
        try:
            return model.model_validate(await read_json(request, 24_000))
        except ValidationError:
            raise HTTPException(400, "回放参数不完整或格式错误，请检查时间、方向和判断。")

    async def call(fn, *args):
        try:
            return await run_in_threadpool(fn, *args)
        except KeyError:
            raise HTTPException(404, "回放会话或观察记录不存在。")
        except (ReplayConflict, SourceError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/replay/catalog")
    async def catalog():
        return await call(replay.history.catalog)

    @app.get("/api/replay/cases")
    async def case_list(dataset: str = V128, bucket: str = "all", symbol: str = "", timeframe: str = "",
                        offset: int = 0, limit: int = 20):
        return await call(replay.case_list, dataset, bucket, symbol, timeframe, offset, limit)

    @app.post("/api/replay/cases/{identity}/sessions")
    async def create_case(identity: str, request: Request):
        return await call(replay.create_case, identity, await body(request, CaseSession))

    @app.post("/api/replay/sessions/{identity}/outcome")
    async def outcome(identity: str, request: Request):
        await body(request, Input)
        return await call(replay.reveal_outcome, identity)

    @app.get("/api/replay/coverage")
    async def coverage(symbol: str, timeframe: str):
        return await call(replay.history.coverage, symbol, timeframe)

    @app.get("/api/replay/sessions")
    async def sessions():
        return await call(replay.sessions)

    @app.post("/api/replay/sessions")
    async def create(request: Request):
        return await call(replay.create, await body(request, NewSession))

    @app.get("/api/replay/sessions/{identity}")
    async def session(identity: str):
        return await call(replay.session, identity)

    @app.post("/api/replay/sessions/{identity}/move")
    async def move(identity: str, request: Request):
        return await call(replay.move, identity, await body(request, Move))

    @app.post("/api/replay/sessions/{identity}/freeze")
    async def freeze(identity: str, request: Request):
        return await call(replay.freeze, identity, await body(request, Freeze), model_name())

    @app.get("/api/replay/observations/{identity}")
    async def observation(identity: str):
        return await call(replay.observation, identity)

    @app.post("/api/replay/observations/{identity}/judgment")
    async def judgment(identity: str, request: Request):
        return await call(replay.judge, identity, await body(request, Judgment))

    @app.post("/api/replay/observations/{identity}/analyze")
    async def analyze(identity: str, request: Request):
        await body(request, Input)
        return await call(replay.inference, identity, provider, inference_lock, capture_exchange, review_context)

    @app.post("/api/replay/observations/{identity}/followup")
    async def followup(identity: str, request: Request):
        return await call(replay.followup, identity, await body(request, Followup))

    @app.get("/api/replay/observations/{identity}/export")
    async def export(identity: str):
        value = await call(replay.observation, identity)
        return Response(_json(value), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="replay-{value["id"]}.json"'})

    return replay
