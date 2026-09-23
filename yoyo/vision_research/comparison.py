"""Paired input comparisons on one immutable, causal replay observation.

Design reference: https://daytradingbench.com/docs/text-vs-vision . Only input
encoding varies: one PNG, the same 120 OHLC/MA rows, or both. No examples,
human labels, outcomes or previous model replies enter any provider request.
Each arm has one durable attempt; errors remain errors, never no-match labels.
This is a descriptive research tool, not a new strategy or a profitability test.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import time
import uuid

from .comparison_input import COMPARISON_VERSION, INPUT_MODES, build_market_packet, packet_sha256
from .replay import ReplayConflict, _json
from .store import utc_now
from .zhipu import ZhipuError


class ReplayComparison:
    def __init__(self, replay):
        self.replay, self.store = replay, replay.store
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS replay_comparisons "
                       "(observation_id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    def _get(self, observation_id):
        with self.store.connect() as db:
            row = db.execute("SELECT record FROM replay_comparisons WHERE observation_id=?",
                             (observation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, group):
        with self.store.connect() as db:
            db.execute("INSERT INTO replay_comparisons VALUES(?,?) ON CONFLICT(observation_id) "
                       "DO UPDATE SET record=excluded.record", (group["observation_id"], _json(group)))

    @staticmethod
    def _bundle(obs):
        try:
            packet = build_market_packet(obs)
        except (ValueError, KeyError, TypeError) as exc:
            raise ReplayConflict("冻结行情的边界或内容校验失败，不能准备图文对照。") from exc
        return packet, packet_sha256(packet)

    def public(self, observation_id):
        group = self._get(observation_id)
        if not group:
            return None
        arms = []
        for mode in INPUT_MODES:
            run_id = group["run_ids"][mode]
            run = self.store.get(run_id) if run_id else None
            arms.append({"mode": mode, "run_id": run_id, "run": run,
                         "status": run["status"] if run else "interrupted" if run_id else "pending"})
        completed = [a["run"] for a in arms if a["status"] == "completed"]
        all_complete = len(completed) == len(INPUT_MODES)
        signatures = {(r["decision"]["verdict"], r["decision"]["current_state"], r["decision"]["side"])
                      for r in completed}
        models = {r["model"] for r in completed}
        return {**{k: v for k, v in group.items() if k != "run_ids"}, "arms": arms,
                "completed_count": len(completed), "agreement": len(signatures) == 1 if all_complete else None,
                "returned_models_consistent": len(models) == 1 if all_complete else None,
                "training_eligible": False, "production_eligible": False}

    def prepare(self, observation_id, review_context):
        with self.replay.lock:
            obs = self.replay._get("replay_observations", observation_id)
            if self._get(observation_id):
                return self.replay.observation(observation_id)
            packet, sha = self._bundle(obs)
            self.replay._stored_image(obs)
            # Rotate all six permutations independently of outcomes. This is a
            # recommended manual order, not a random train/test split.
            orders = list(itertools.permutations(INPUT_MODES))
            order = orders[int(hashlib.sha256(observation_id.encode()).hexdigest(), 16) % len(orders)]
            group = {"id": uuid.uuid4().hex, "observation_id": observation_id, "created_at": utc_now(),
                     "protocol_version": COMPARISON_VERSION, "model": obs["model"],
                     "criteria": obs["criteria"], "criteria_sha256": hashlib.sha256(obs["criteria"].encode()).hexdigest(),
                     "chart_sha256": obs["chart_sha256"], "image_sha256": obs["image_sha256"],
                     "image_url": obs["image_url"], "data_sha256": sha, "market_packet": packet,
                     "context": review_context(obs["chart"]["provenance"], obs["chart"]),
                     "input_window": {"bar_count": len(obs["chart"]["candles"]),
                                      "start_ms": obs["chart"]["candles"][0]["t"], "end_ms": obs["cursor_ms"]},
                     "reference_policy": "none", "recommended_order": list(order), "execution_order": [],
                     "run_ids": {mode: None for mode in INPUT_MODES}}
            self._save(group)
            return self.replay.observation(observation_id)

    def inference(self, observation_id, mode, provider, inference_lock, capture_exchange, review_context):
        if mode not in INPUT_MODES:
            raise ReplayConflict("未知的图文对照输入方式。")
        with self.replay.lock:
            obs = self.replay._get("replay_observations", observation_id)
            group = self._get(observation_id)
            if not group:
                raise ReplayConflict("请先准备这张冻结图的三组对照。")
            if group["run_ids"][mode]:
                return self.replay.observation(observation_id)
            if obs["mode"] == "blind" and obs["human"] is None:
                raise ReplayConflict("请先保存自己的判断，再运行图文对照。")
            packet, sha = self._bundle(obs)
            if (group["protocol_version"] != COMPARISON_VERSION or sha != group["data_sha256"]
                    or group["market_packet"] != packet
                    or group["image_sha256"] != obs["image_sha256"]
                    or group["image_url"] != obs["image_url"]
                    or group["chart_sha256"] != obs["chart_sha256"]
                    or group["criteria_sha256"] != hashlib.sha256(obs["criteria"].encode()).hexdigest()
                    or group["context"] != review_context(obs["chart"]["provenance"], obs["chart"])
                    or group["model"] != obs["model"] or group["criteria"] != obs["criteria"]):
                raise ReplayConflict("对照协议或冻结输入已变化，旧对照保留；请重新冻结新的观察。")
            # Validate the original image for every arm, even if it is not sent
            # by text mode: the shared paired input must still be reproducible.
            image = self.replay._stored_image(obs)
            client = provider(group["model"])
            if not inference_lock.acquire(blocking=False):
                client.close()
                raise ReplayConflict("当前有模型请求运行中；本组尚未调用，请稍后点击。")
            try:
                has_image = mode in {"vision", "hybrid"}
                run = {"id": uuid.uuid4().hex, "created_at": utc_now(), "status": "running",
                       "model": group["model"], "requested_model": group["model"], "provider": "zhipu",
                       "source": "historical_replay_comparison", "replay_observation_id": observation_id,
                       "comparison_id": group["id"], "input_mode": mode,
                       "symbol": obs["symbol"], "timeframe": obs["timeframe"],
                       "image_url": obs["image_url"] if has_image else None,
                       "image_name": "replay-comparison.png" if has_image else None,
                       "image_sha256": image.sha256 if has_image else None,
                       "image_width": image.width if has_image else None, "image_height": image.height if has_image else None,
                       "source_image_sha256": image.sha256, "data_sha256": sha,
                       "criteria": group["criteria"], "criteria_sha256": group["criteria_sha256"],
                       "prompt_version": COMPARISON_VERSION, "schema_version": 2,
                       "analysis_scope": "current_right_edge", "provenance": obs["chart"]["provenance"],
                       "review_context": group["context"], "references": [], "reference_source": "comparison_none",
                       "decision": None, "usage": {}, "latency_ms": None, "error": None,
                       "review": None, "review_history": [], "training_eligible": False, "production_eligible": False}
                run["api_exchange_id"] = capture_exchange(client, "input_comparison", run["id"])
                # No request may be sent before this claim is durable. A crash
                # before claiming creates no spend; after claiming blocks retry.
                self.store.save(run)
                group["run_ids"][mode] = run["id"]
                group["execution_order"].append(mode)
                self._save(group)
                session = self.replay._get("replay_sessions", obs["session_id"])
                session["ai_exposed_until_ms"] = max(session.get("ai_exposed_until_ms", -1), obs["cursor_ms"])
                self.replay._save_session(session)
                obs["comparison_requested"] = True
                if obs["human"] is None:
                    obs["independence"] = "ai_requested_before_human_judgment"
                self.replay._save_obs(obs)
            except BaseException:
                client.close()
                inference_lock.release()
                raise
        started = time.perf_counter()
        try:
            result = client.analyze_comparison(input_mode=mode, image=image if has_image else None,
                                              market_packet=packet, criteria=group["criteria"], context=group["context"])
            run.update(result, status="completed", completed_at=utc_now())
        except ZhipuError as exc:
            run.update(status="failed", error=str(exc), error_details=exc.diagnostics(), completed_at=utc_now())
        except Exception:
            run.update(status="failed", error="对照调用异常，结果未知；没有自动重试。", completed_at=utc_now())
        finally:
            try:
                if run.get("latency_ms") is None:
                    run["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                self.store.finish_exchange(run["api_exchange_id"], run["status"], run.get("error"))
                self.store.save(run)
            finally:
                client.close()
                inference_lock.release()
        return self.replay.observation(observation_id)
