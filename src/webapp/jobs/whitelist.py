"""Hard-coded job whitelist: schema validation + argv assembly only.

Client may only send job_type + constrained params. Never accept cmd / shell /
argv free strings. Path-like params are restricted to preset relative templates.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from src.data.bars import BAR_CHOICES

# Relative to repo root; only these dataset paths may be written by build_dataset.
ALLOWED_BUILD_OUT = frozenset(
    {
        "data/judgment_dataset.csv",
        "data/judgment_dataset_v2_strict.csv",
        "data/judgment_dataset_v2_expanded.csv",
    }
)

HORIZON_MIN = 12
HORIZON_MAX = 576

_SAFE_REL_PATH = re.compile(r"^data/[A-Za-z0-9_./-]+$")
_FORBIDDEN_BODY_KEYS = frozenset(
    {"cmd", "shell", "argv", "command", "executable", "env", "cwd", "script"}
)


class JobValidationError(ValueError):
    """Invalid job_type or params (maps to HTTP 400)."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str  # enum | int | path_enum
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] | None = None
    min_value: int | None = None
    max_value: int | None = None
    description: str = ""


@dataclass(frozen=True)
class JobTypeSpec:
    job_type: str
    title_zh: str
    description_zh: str
    timeout_sec: int
    build_argv: Callable[[dict[str, Any]], list[str]] = field(repr=False)
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    artifacts_hint: str = ""
    confirm_zh: str = ""


def _python() -> str:
    return sys.executable or "python3"


def _require_no_forbidden_keys(params: dict[str, Any]) -> None:
    bad = sorted(set(params) & _FORBIDDEN_BODY_KEYS)
    if bad:
        raise JobValidationError(f"forbidden param keys: {', '.join(bad)}")


def _validate_param(spec: ParamSpec, raw: Any) -> Any:
    if raw is None:
        if spec.required:
            raise JobValidationError(f"missing required param: {spec.name}")
        return spec.default

    if spec.kind == "enum":
        if raw not in (spec.choices or ()):
            raise JobValidationError(
                f"param {spec.name} must be one of {list(spec.choices or ())}"
            )
        return raw

    if spec.kind == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise JobValidationError(f"param {spec.name} must be int") from exc
        if spec.min_value is not None and value < spec.min_value:
            raise JobValidationError(
                f"param {spec.name} must be >= {spec.min_value}"
            )
        if spec.max_value is not None and value > spec.max_value:
            raise JobValidationError(
                f"param {spec.name} must be <= {spec.max_value}"
            )
        return value

    if spec.kind == "path_enum":
        if not isinstance(raw, str):
            raise JobValidationError(f"param {spec.name} must be str")
        # Block traversal and absolute paths before enum check.
        if (
            ".." in raw
            or raw.startswith(("/", "\\"))
            or "\\" in raw
            or not _SAFE_REL_PATH.match(raw)
        ):
            raise JobValidationError(
                f"param {spec.name} rejects path traversal / absolute paths"
            )
        if raw not in (spec.choices or ()):
            raise JobValidationError(
                f"param {spec.name} must be one of {list(spec.choices or ())}"
            )
        return raw

    raise JobValidationError(f"unknown param kind: {spec.kind}")


def _validate_against_specs(
    specs: tuple[ParamSpec, ...], params: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise JobValidationError("params must be an object")
    _require_no_forbidden_keys(params)
    allowed = {s.name for s in specs}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise JobValidationError(f"unknown params: {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for spec in specs:
        raw = params.get(spec.name, None)
        if raw is None and not spec.required:
            if spec.default is not None:
                out[spec.name] = spec.default
            continue
        out[spec.name] = _validate_param(spec, raw)
    return out


def _argv_build_dataset(p: dict[str, Any]) -> list[str]:
    argv = [
        _python(),
        "-m",
        "src.judgment.build_dataset",
        "--mode",
        str(p.get("mode", "strict")),
        "--bar",
        str(p.get("bar", "15m")),
        "--horizon-bars",
        str(int(p.get("horizon_bars", 96))),
    ]
    out = p.get("out")
    if out:
        argv.extend(["--out", str(out)])
    return argv


def _argv_barrier_sweep(_p: dict[str, Any]) -> list[str]:
    return [_python(), "-m", "src.judgment.barrier_sweep"]


def _argv_swap_replication(_p: dict[str, Any]) -> list[str]:
    return [_python(), "scripts/swap_replication.py"]


def _argv_update_okx(p: dict[str, Any]) -> list[str]:
    return [
        _python(),
        "-m",
        "src.data.update_okx",
        "--bar",
        str(p.get("bar", "15m")),
    ]


def _argv_forward_track(_p: dict[str, Any]) -> list[str]:
    # No --start exposure (design D6): UI must not alter formal window.
    return [_python(), "scripts/forward_track.py"]


def _argv_deploy_self(_p: dict[str, Any]) -> list[str]:
    return ["bash", "scripts/deploy_vps.sh"]


JOB_TYPES: dict[str, JobTypeSpec] = {
    "build_dataset": JobTypeSpec(
        job_type="build_dataset",
        title_zh="构建判断层数据集",
        description_zh="python3 -m src.judgment.build_dataset（strict/expanded）",
        timeout_sec=2 * 3600,
        artifacts_hint="data/judgment_dataset_*.csv",
        confirm_zh="将在仓库根目录构建判断层数据集（可能覆盖已有 CSV）。",
        params=(
            ParamSpec(
                "mode",
                "enum",
                default="strict",
                choices=("strict", "expanded"),
                description="candidate pool mode",
            ),
            ParamSpec(
                "bar",
                "enum",
                default="15m",
                choices=tuple(BAR_CHOICES),
                description="candle bar",
            ),
            ParamSpec(
                "horizon_bars",
                "int",
                default=96,
                min_value=HORIZON_MIN,
                max_value=HORIZON_MAX,
                description="label horizon in bars",
            ),
            ParamSpec(
                "out",
                "path_enum",
                default="data/judgment_dataset_v2_strict.csv",
                choices=tuple(sorted(ALLOWED_BUILD_OUT)),
                description="output CSV under data/ (preset names only)",
            ),
        ),
        build_argv=_argv_build_dataset,
    ),
    "barrier_sweep": JobTypeSpec(
        job_type="barrier_sweep",
        title_zh="障碍参数扫描",
        description_zh="python3 -m src.judgment.barrier_sweep（无参固定扫描）",
        timeout_sec=4 * 3600,
        artifacts_hint="analysis/output/*sweep*.json / data/sweep_v3/",
        confirm_zh="将运行固定 barrier 扫描配置（耗 CPU/磁盘，默认 Mac 串行）。",
        params=(),
        build_argv=_argv_barrier_sweep,
    ),
    "swap_replication": JobTypeSpec(
        job_type="swap_replication",
        title_zh="合约复制实验",
        description_zh="python3 scripts/swap_replication.py",
        timeout_sec=2 * 3600,
        artifacts_hint="analysis/output/swap_replication.json",
        confirm_zh="将复跑 swap replication 实验并写 analysis/output。",
        params=(),
        build_argv=_argv_swap_replication,
    ),
    "update_okx": JobTypeSpec(
        job_type="update_okx",
        title_zh="增量更新 OKX K 线",
        description_zh="python3 -m src.data.update_okx",
        timeout_sec=3600,
        artifacts_hint="data/kline_fetched/",
        confirm_zh="将访问 OKX 增量更新本机 kline（有真实网络副作用）。",
        params=(
            ParamSpec(
                "bar",
                "enum",
                default="15m",
                choices=tuple(BAR_CHOICES),
                description="candle bar",
            ),
        ),
        build_argv=_argv_update_okx,
    ),
    "forward_track": JobTypeSpec(
        job_type="forward_track",
        title_zh="前向跟踪刷新",
        description_zh="python3 scripts/forward_track.py（正式窗口，不改 --start）",
        timeout_sec=30 * 60,
        artifacts_hint="data/forward_log.csv",
        confirm_zh="将刷新前向日志（不暴露正式窗口 --start）。",
        params=(),
        build_argv=_argv_forward_track,
    ),
    "deploy_self": JobTypeSpec(
        job_type="deploy_self",
        title_zh="部署看板到 VPS",
        description_zh="bash scripts/deploy_vps.sh（仅 Mac 有意义）",
        timeout_sec=15 * 60,
        artifacts_hint="VPS rsync + systemd restart",
        confirm_zh="将 rsync 代码/产物到 VPS 并重启 fable-dashboard（二次确认）。",
        params=(),
        build_argv=_argv_deploy_self,
    ),
}


def list_job_types() -> list[dict[str, Any]]:
    """Metadata for GET /api/ops/job-types (frontend form schema)."""
    items = []
    for spec in JOB_TYPES.values():
        items.append(
            {
                "job_type": spec.job_type,
                "title_zh": spec.title_zh,
                "description_zh": spec.description_zh,
                "timeout_sec": spec.timeout_sec,
                "artifacts_hint": spec.artifacts_hint,
                "confirm_zh": spec.confirm_zh,
                "params": [
                    {
                        "name": p.name,
                        "kind": p.kind,
                        "required": p.required,
                        "default": p.default,
                        "choices": list(p.choices) if p.choices else None,
                        "min": p.min_value,
                        "max": p.max_value,
                        "description": p.description,
                    }
                    for p in spec.params
                ],
            }
        )
    return items


def validate_params(job_type: str, params: dict[str, Any] | None) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise JobValidationError(f"unknown job_type: {job_type!r}")
    return _validate_against_specs(JOB_TYPES[job_type].params, params or {})


def build_argv(job_type: str, params: dict[str, Any] | None = None) -> list[str]:
    """Validate params and return fixed argv list (never free shell)."""
    if job_type not in JOB_TYPES:
        raise JobValidationError(f"unknown job_type: {job_type!r}")
    clean = validate_params(job_type, params)
    argv = JOB_TYPES[job_type].build_argv(clean)
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise RuntimeError("whitelist build_argv must return list[str]")
    # Invariant: never start with shell -c free form from user input.
    if len(argv) >= 2 and argv[0] in {"bash", "sh", "/bin/bash", "/bin/sh"}:
        if argv[1] in {"-c", "-lc"}:
            raise RuntimeError("whitelist must not use bash -c")
    return argv


def human_summary(job_type: str, params: dict[str, Any] | None = None) -> str:
    """Human-readable command summary for UI confirm (not editable shell)."""
    argv = build_argv(job_type, params)
    # Replace absolute python path with python3 for display stability.
    display = list(argv)
    if display and display[0] == _python():
        display[0] = "python3"
    return " ".join(display)
