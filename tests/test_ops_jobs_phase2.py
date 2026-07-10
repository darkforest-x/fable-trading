"""P2.5 Phase 2: job whitelist argv, executor flag, path safety, no free cmd."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.webapp.jobs import runner as runner_mod
from src.webapp.jobs import store as store_mod
from src.webapp.jobs.whitelist import (
    ALLOWED_BUILD_OUT,
    JOB_TYPES,
    JobValidationError,
    build_argv,
    human_summary,
    list_job_types,
    validate_params,
)
from src.webapp.ops_flags import executor_enabled
from src.webapp.server import CreateJobBody, ops_job_types, ops_jobs_create


# ---------------------------------------------------------------------------
# Whitelist / argv building
# ---------------------------------------------------------------------------


def test_whitelist_contains_only_expected_types() -> None:
    expected = {
        "build_dataset",
        "barrier_sweep",
        "swap_replication",
        "update_okx",
        "forward_track",
        "deploy_self",
    }
    assert set(JOB_TYPES) == expected


def test_build_dataset_argv_defaults() -> None:
    argv = build_argv("build_dataset", {})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "src.judgment.build_dataset"]
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "strict"
    assert "--bar" in argv and argv[argv.index("--bar") + 1] == "15m"
    assert "--horizon-bars" in argv
    assert "--out" in argv
    out = argv[argv.index("--out") + 1]
    assert out in ALLOWED_BUILD_OUT
    assert ".." not in out


def test_build_dataset_argv_expanded() -> None:
    argv = build_argv(
        "build_dataset",
        {
            "mode": "expanded",
            "bar": "1H",
            "horizon_bars": 144,
            "out": "data/ma206/judgment_dataset_expanded.csv",
        },
    )
    assert argv[argv.index("--mode") + 1] == "expanded"
    assert argv[argv.index("--bar") + 1] == "1H"
    assert argv[argv.index("--horizon-bars") + 1] == "144"
    assert argv[argv.index("--out") + 1] == "data/ma206/judgment_dataset_expanded.csv"


def test_update_okx_argv() -> None:
    argv = build_argv("update_okx", {"bar": "30m"})
    assert argv[1:3] == ["-m", "src.data.update_okx"]
    assert argv[argv.index("--bar") + 1] == "30m"


def test_no_param_jobs_fixed_argv() -> None:
    assert build_argv("barrier_sweep", {})[1:3] == ["-m", "src.judgment.barrier_sweep"]
    assert build_argv("swap_replication", {})[-1] == "scripts/swap_replication.py"
    assert build_argv("forward_track", {})[-1] == "scripts/forward_track.py"
    assert build_argv("deploy_self", {}) == ["bash", "scripts/deploy_vps.sh"]
    # forward_track must never expose --start
    assert "--start" not in build_argv("forward_track", {})


def test_unknown_job_type_rejected() -> None:
    with pytest.raises(JobValidationError, match="unknown job_type"):
        build_argv("not_in_whitelist", {})


def test_injection_bar_rejected() -> None:
    with pytest.raises(JobValidationError):
        build_argv("update_okx", {"bar": "15m; rm -rf /"})


def test_horizon_out_of_range_rejected() -> None:
    with pytest.raises(JobValidationError):
        build_argv("build_dataset", {"horizon_bars": 5})
    with pytest.raises(JobValidationError):
        build_argv("build_dataset", {"horizon_bars": 9999})


def test_path_traversal_out_rejected() -> None:
    for bad in (
        "../etc/passwd",
        "data/../../../etc/passwd",
        "/etc/passwd",
        "data/foo/../../secret.csv",
        "data/judgment_dataset.csv;rm",
        "data/not_in_allowlist.csv",
    ):
        with pytest.raises(JobValidationError):
            build_argv("build_dataset", {"out": bad})


def test_forbidden_param_keys_rejected() -> None:
    for key in ("cmd", "shell", "argv", "command"):
        with pytest.raises(JobValidationError, match="forbidden"):
            validate_params("update_okx", {key: "evil", "bar": "15m"})


def test_unknown_params_rejected() -> None:
    with pytest.raises(JobValidationError, match="unknown params"):
        validate_params("update_okx", {"bar": "15m", "extra": "nope"})


def test_extra_params_cannot_change_argv_prefix() -> None:
    """Fuzz-ish: arbitrary extra fields never alter fixed module prefix."""
    base = build_argv("update_okx", {"bar": "15m"})
    with pytest.raises(JobValidationError):
        build_argv(
            "update_okx",
            {"bar": "15m", "cmd": "bash", "argv": ["-c", "id"], "shell": True},
        )
    # Valid still stable
    assert base[1:3] == ["-m", "src.data.update_okx"]


def test_list_job_types_schema_for_frontend() -> None:
    items = list_job_types()
    assert len(items) == 6
    ids = {i["job_type"] for i in items}
    assert "update_okx" in ids
    okx = next(i for i in items if i["job_type"] == "update_okx")
    assert okx["params"][0]["name"] == "bar"
    assert "15m" in okx["params"][0]["choices"]


def test_human_summary_uses_python3_label() -> None:
    s = human_summary("update_okx", {"bar": "15m"})
    assert s.startswith("python3 -m src.data.update_okx")
    assert "15m" in s


def test_never_bash_c() -> None:
    for jt in JOB_TYPES:
        argv = build_argv(jt, {})
        assert not (len(argv) >= 2 and argv[0] in {"bash", "sh"} and argv[1] == "-c")


# ---------------------------------------------------------------------------
# Executor flag
# ---------------------------------------------------------------------------


def test_executor_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    assert executor_enabled() is False


def test_executor_enabled_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "1")
    assert executor_enabled() is True
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "true")
    assert executor_enabled() is True
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "0")
    assert executor_enabled() is False


# ---------------------------------------------------------------------------
# Store + runner (no network / no real heavy jobs)
# ---------------------------------------------------------------------------


@pytest.fixture()
def job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "ops_jobs.sqlite"
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("OPS_JOBS_DB", str(db))
    monkeypatch.setenv("OPS_JOB_LOG_DIR", str(log_dir))
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "1")
    monkeypatch.delenv("OPS_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPS_REQUIRE_AUTH", raising=False)
    st = store_mod.reset_store_for_tests(db)
    rn = runner_mod.reset_runner_for_tests(st)
    yield st, rn, tmp_path
    rn.stop()


def test_store_create_and_list(job_env) -> None:
    st, _rn, _ = job_env
    job = st.create(
        job_type="update_okx",
        params={"bar": "15m"},
        argv=["python3", "-m", "src.data.update_okx", "--bar", "15m"],
        summary="test",
        log_path="/tmp/x.log",
    )
    assert job["status"] == "queued"
    listed = st.list_jobs()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == job["id"]


def test_runner_enqueue_refuses_when_executor_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPS_JOBS_DB", str(tmp_path / "j.sqlite"))
    monkeypatch.setenv("OPS_JOB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "0")
    st = store_mod.reset_store_for_tests(tmp_path / "j.sqlite")
    rn = runner_mod.reset_runner_for_tests(st)
    with pytest.raises(PermissionError, match="执行器"):
        rn.enqueue("update_okx", {"bar": "15m"})
    rn.stop()


def test_runner_runs_safe_local_command(job_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration smoke: whitelist path runs a no-network local process via fake argv.

    We do not call real update_okx / deploy / YOLO. Instead monkeypatch
    build_argv for a whitelisted type to a harmless python -c print.
    """
    st, rn, _ = job_env
    # Patch only for this test: still goes through enqueue validation of job_type.
    # Use swap_replication type name but override build_argv after validate.
    original_build = runner_mod.build_argv

    def fake_build(job_type, params=None):
        # Keep validation
        from src.webapp.jobs.whitelist import validate_params

        validate_params(job_type, params)
        return [sys.executable, "-c", "print('ops-job-smoke-ok')"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    # Also patch human_summary dependency path used in enqueue
    monkeypatch.setattr(
        runner_mod,
        "human_summary",
        lambda job_type, params=None: "python3 -c print",
    )

    job = rn.enqueue("swap_replication", {})
    assert job["status"] == "queued"
    # Wait for worker
    import time

    deadline = time.time() + 5
    final = None
    while time.time() < deadline:
        final = st.get(job["id"])
        if final and final["status"] in {"succeeded", "failed", "timeout"}:
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "succeeded"
    assert final["exit_code"] == 0
    log = rn.read_log_tail(job["id"])
    assert "ops-job-smoke-ok" in log
    monkeypatch.setattr(runner_mod, "build_argv", original_build)


def test_orphaned_running_marked_failed(job_env) -> None:
    st, _rn, _ = job_env
    job = st.create(
        job_type="update_okx",
        params={"bar": "15m"},
        argv=["python3", "-m", "src.data.update_okx"],
        summary="x",
        log_path="",
    )
    with st._lock, st._conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE jobs SET status='running', started_at=created_at WHERE id=?",
            (job["id"],),
        )
    n = st.mark_orphaned_running()
    assert n == 1
    assert st.get(job["id"])["status"] == "failed"
    assert "orphaned" in (st.get(job["id"])["error_summary"] or "")


# ---------------------------------------------------------------------------
# HTTP / route contract (no TestClient — avoid httpx dep in CI)
# ---------------------------------------------------------------------------


def _req(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


@pytest.fixture()
def route_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPS_JOBS_DB", str(tmp_path / "ops.sqlite"))
    monkeypatch.setenv("OPS_JOB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "0")
    monkeypatch.setenv("OPS_AUTH_MODE", "off")
    monkeypatch.delenv("OPS_REQUIRE_AUTH", raising=False)
    store_mod.reset_store_for_tests(tmp_path / "ops.sqlite")
    rn = runner_mod.reset_runner_for_tests()
    yield
    rn.stop()


def test_post_jobs_forbidden_when_executor_off(route_env) -> None:
    body = CreateJobBody(job_type="update_okx", params={"bar": "15m"})
    res = ops_jobs_create(body, _req())
    assert res.status_code == 403
    assert "执行器" in res.body.decode("utf-8")


def test_post_jobs_rejects_free_cmd_body(route_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "1")
    # Free top-level cmd must be rejected by pydantic extra=forbid
    with pytest.raises(ValidationError):
        CreateJobBody(
            job_type="update_okx",
            params={"bar": "15m"},
            cmd="rm -rf /",  # type: ignore[call-arg]
        )

    body = CreateJobBody(job_type="update_okx", params={"bar": "15m", "cmd": "evil"})
    res = ops_jobs_create(body, _req())
    assert res.status_code == 400
    assert b"free cmd" in res.body or b"forbidden" in res.body or b"cmd" in res.body


def test_post_jobs_unknown_type(route_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "1")
    body = CreateJobBody(job_type="yolo_train", params={})
    res = ops_jobs_create(body, _req())
    assert res.status_code == 400
    assert b"unknown job_type" in res.body


def test_job_types_endpoint(route_env) -> None:
    body = ops_job_types(_req())
    assert body["executor_enabled"] is False
    assert len(body["items"]) == 6


def test_post_jobs_with_auth_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from src.webapp.auth import verify_ops_request

    monkeypatch.setenv("OPS_JOBS_DB", str(tmp_path / "ops.sqlite"))
    monkeypatch.setenv("OPS_JOB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ENABLE_JOB_EXECUTOR", "1")
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "phase2-test-token")
    store_mod.reset_store_for_tests(tmp_path / "ops.sqlite")
    rn = runner_mod.reset_runner_for_tests()

    def fake_build(job_type, params=None):
        validate_params(job_type, params)
        return [sys.executable, "-c", "pass"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    monkeypatch.setattr(runner_mod, "human_summary", lambda *a, **k: "noop")

    with pytest.raises(HTTPException) as ei:
        verify_ops_request(_req())
    assert ei.value.status_code == 401

    # Auth OK path then create
    verify_ops_request(_req({"X-Ops-Token": "phase2-test-token"}))
    body = CreateJobBody(job_type="forward_track", params={})
    res = ops_jobs_create(body, _req({"X-Ops-Token": "phase2-test-token"}))
    assert res.status_code == 201
    payload = json_loads_response(res)
    assert payload["status"] == "queued"
    assert payload["job_type"] == "forward_track"
    rn.stop()


def json_loads_response(res) -> dict:
    import json

    return json.loads(res.body.decode("utf-8"))


def test_ops_status_reports_phase2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    from src.webapp import ops_flags

    body = ops_flags.ops_status_payload()
    assert body["executor_enabled"] is False
    assert "2" in body["phase"]
