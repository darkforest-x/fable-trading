"""Fail-closed contract for offline Kronos research.

Kronos is an external probabilistic K-line foundation model.  It is useful as
an offline representation candidate, but it is not part of the active detector,
judgment bundle, forward scanner, or executor.  This module deliberately imports
neither Torch nor the upstream checkout; it only fixes the identity and the
research conditions that must be true before a separate sidecar may run.

Sources (pinned on 2026-09-01):

* Upstream code: https://github.com/shiyu-coder/Kronos/tree/67b630e67f6a18c9e9be918d9b4337c960db1e9a
* Paper: https://arxiv.org/abs/2508.02739
* Model: https://huggingface.co/NeoQuasar/Kronos-small/tree/901c26c1332695a2a8f243eb2f37243a37bea320
* Tokenizer: https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base/tree/0e0117387f39004a9016484a186a908917e22426

The paper's forecasting profile uses T=0.6, top-p=0.9 and ten sampled paths.
The public README example instead uses T=1.0 and one path.  Because sampling
settings materially change the generated features, the research contract pins
the paper profile and requires a seed plus explicit ``eval()`` mode.  A future
economic experiment must still change one variable at a time; this contract is
an execution preflight, not an acceptance result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, Tuple

import yaml

from yoyo.contracts.holdout import HOLDOUT_START_ISO, assert_pre_holdout


UPSTREAM_REPOSITORY = "https://github.com/shiyu-coder/Kronos.git"
UPSTREAM_COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_LICENSE_SHA256 = "acb2d194d378204e5f2be4dcd24d39ecac437903620c790c3315a96dab388fdc"

MODEL_ID = "NeoQuasar/Kronos-small"
MODEL_REVISION = "901c26c1332695a2a8f243eb2f37243a37bea320"
MODEL_CONFIG_SHA256 = "5e0f6a605d5f81b5c9b559fe5cf716a1acb041c744e6f41bd05b097b7a685396"
MODEL_WEIGHTS_SHA256 = "b082dfcbd8e8c142a725c8bbb99781802f38fec81210e13479effb32b3c3e020"
MODEL_WEIGHTS_SIZE = 98_980_656

TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
TOKENIZER_REVISION = "0e0117387f39004a9016484a186a908917e22426"
TOKENIZER_CONFIG_SHA256 = "2366e7ccfec76cbc19cf3c4c1b9c5d901be336ca1e83f2d2292c9bff381b77a2"
TOKENIZER_WEIGHTS_SHA256 = "59d85f6af76a2c3b8240ea06cb21db4213b4eeca053f246b23e29cf832fc6bee"
TOKENIZER_WEIGHTS_SIZE = 15_842_368

PAPER_FORECAST_TEMPERATURE = 0.6
PAPER_FORECAST_TOP_P = 0.9
PAPER_FORECAST_SAMPLE_COUNT = 10
MAX_CONTEXT = 512

REQUIRED_MATCHED_CONTROL_AXES: Tuple[str, ...] = (
    "symbol",
    "time_bucket",
    "volatility_bucket",
    "horizon",
    "cost",
)


class KronosResearchContractError(ValueError):
    """A Kronos research plan that could be mistaken for reproducible evidence."""


def _required(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise KronosResearchContractError(f"{where} is missing {key!r}")
    return mapping[key]


def _sha(value: Any, where: str) -> str:
    text = str(value).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise KronosResearchContractError(f"{where} must be a 64-character SHA-256")
    return text


def _revision(value: Any, where: str) -> str:
    text = str(value).lower()
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise KronosResearchContractError(f"{where} must be a pinned 40-character revision")
    return text


def minimum_permutations_for_alpha(alpha: float) -> int:
    """Smallest ``n`` for which the corrected minimum p-value is below alpha.

    The repository uses ``(r + 1) / (n + 1)``.  With 30 permutations the best
    possible p-value is 1/31=0.0323, so a test with alpha=0.01 cannot possibly
    pass honestly.  One hundred permutations is the mathematical minimum;
    the project contract uses 10,000 for useful resolution.
    """

    if not math.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise KronosResearchContractError("alpha must be finite and strictly between 0 and 1")
    return int(math.floor(1.0 / alpha))


@dataclass(frozen=True)
class KronosResearchContract:
    """Identity, inference, causality and evaluation constraints for one sidecar."""

    source_repository: str
    source_commit: str
    source_license: str
    source_license_sha256: str
    model_id: str
    model_revision: str
    model_config_sha256: str
    model_weights_sha256: str
    model_weights_size: int
    tokenizer_id: str
    tokenizer_revision: str
    tokenizer_config_sha256: str
    tokenizer_weights_sha256: str
    tokenizer_weights_size: int
    max_context: int
    lookback: int
    pred_len: int
    temperature: float
    top_p: float
    sample_count: int
    torch_seed: int
    explicit_eval: bool
    timestamp_timezone: str
    amount_policy: str
    alpha: float
    n_permutations: int
    matched_control_axes: Tuple[str, ...]
    time_split: str
    one_variable_at_a_time: bool
    holdout_read_allowed: bool
    holdout_start_exclusive: str
    training_eligible: bool
    production_eligible: bool
    active_bundle_eligible: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KronosResearchContract":
        if int(_required(raw, "schema_version", "contract")) != 1:
            raise KronosResearchContractError("only schema_version=1 is supported")
        if _required(raw, "role", "contract") != "offline_research_only":
            raise KronosResearchContractError("role must remain offline_research_only")

        source = _required(raw, "source", "contract")
        model = _required(raw, "model", "contract")
        tokenizer = _required(raw, "tokenizer", "contract")
        inference = _required(raw, "inference", "contract")
        evaluation = _required(raw, "evaluation", "contract")
        holdout = _required(raw, "holdout", "contract")
        eligibility = _required(raw, "eligibility", "contract")
        for value, name in (
            (source, "source"),
            (model, "model"),
            (tokenizer, "tokenizer"),
            (inference, "inference"),
            (evaluation, "evaluation"),
            (holdout, "holdout"),
            (eligibility, "eligibility"),
        ):
            if not isinstance(value, Mapping):
                raise KronosResearchContractError(f"{name} must be a mapping")

        contract = cls(
            source_repository=str(_required(source, "repository", "source")),
            source_commit=_revision(_required(source, "commit", "source"), "source.commit"),
            source_license=str(_required(source, "license", "source")),
            source_license_sha256=_sha(
                _required(source, "license_sha256", "source"), "source.license_sha256"
            ),
            model_id=str(_required(model, "repo_id", "model")),
            model_revision=_revision(_required(model, "revision", "model"), "model.revision"),
            model_config_sha256=_sha(
                _required(model, "config_sha256", "model"), "model.config_sha256"
            ),
            model_weights_sha256=_sha(
                _required(model, "weights_sha256", "model"), "model.weights_sha256"
            ),
            model_weights_size=int(_required(model, "weights_size_bytes", "model")),
            tokenizer_id=str(_required(tokenizer, "repo_id", "tokenizer")),
            tokenizer_revision=_revision(
                _required(tokenizer, "revision", "tokenizer"), "tokenizer.revision"
            ),
            tokenizer_config_sha256=_sha(
                _required(tokenizer, "config_sha256", "tokenizer"),
                "tokenizer.config_sha256",
            ),
            tokenizer_weights_sha256=_sha(
                _required(tokenizer, "weights_sha256", "tokenizer"),
                "tokenizer.weights_sha256",
            ),
            tokenizer_weights_size=int(
                _required(tokenizer, "weights_size_bytes", "tokenizer")
            ),
            max_context=int(_required(inference, "max_context", "inference")),
            lookback=int(_required(inference, "lookback", "inference")),
            pred_len=int(_required(inference, "pred_len", "inference")),
            temperature=float(_required(inference, "temperature", "inference")),
            top_p=float(_required(inference, "top_p", "inference")),
            sample_count=int(_required(inference, "sample_count", "inference")),
            torch_seed=int(_required(inference, "torch_seed", "inference")),
            explicit_eval=bool(_required(inference, "explicit_eval", "inference")),
            timestamp_timezone=str(
                _required(inference, "timestamp_timezone", "inference")
            ),
            amount_policy=str(_required(inference, "amount_policy", "inference")),
            alpha=float(_required(evaluation, "alpha", "evaluation")),
            n_permutations=int(
                _required(evaluation, "n_permutations", "evaluation")
            ),
            matched_control_axes=tuple(
                str(value)
                for value in _required(
                    evaluation, "matched_control_axes", "evaluation"
                )
            ),
            time_split=str(_required(evaluation, "time_split", "evaluation")),
            one_variable_at_a_time=bool(
                _required(evaluation, "one_variable_at_a_time", "evaluation")
            ),
            holdout_read_allowed=bool(
                _required(holdout, "read_allowed", "holdout")
            ),
            holdout_start_exclusive=str(
                _required(holdout, "start_exclusive", "holdout")
            ),
            training_eligible=bool(
                _required(eligibility, "training", "eligibility")
            ),
            production_eligible=bool(
                _required(eligibility, "production", "eligibility")
            ),
            active_bundle_eligible=bool(
                _required(eligibility, "active_bundle", "eligibility")
            ),
        )
        contract.validate()
        return contract

    def validate(self) -> None:
        expected = {
            "source.repository": (self.source_repository, UPSTREAM_REPOSITORY),
            "source.commit": (self.source_commit, UPSTREAM_COMMIT),
            "source.license": (self.source_license, UPSTREAM_LICENSE),
            "source.license_sha256": (
                self.source_license_sha256,
                UPSTREAM_LICENSE_SHA256,
            ),
            "model.repo_id": (self.model_id, MODEL_ID),
            "model.revision": (self.model_revision, MODEL_REVISION),
            "model.config_sha256": (self.model_config_sha256, MODEL_CONFIG_SHA256),
            "model.weights_sha256": (
                self.model_weights_sha256,
                MODEL_WEIGHTS_SHA256,
            ),
            "model.weights_size_bytes": (self.model_weights_size, MODEL_WEIGHTS_SIZE),
            "tokenizer.repo_id": (self.tokenizer_id, TOKENIZER_ID),
            "tokenizer.revision": (self.tokenizer_revision, TOKENIZER_REVISION),
            "tokenizer.config_sha256": (
                self.tokenizer_config_sha256,
                TOKENIZER_CONFIG_SHA256,
            ),
            "tokenizer.weights_sha256": (
                self.tokenizer_weights_sha256,
                TOKENIZER_WEIGHTS_SHA256,
            ),
            "tokenizer.weights_size_bytes": (
                self.tokenizer_weights_size,
                TOKENIZER_WEIGHTS_SIZE,
            ),
        }
        mismatches = {
            name: {"actual": actual, "expected": wanted}
            for name, (actual, wanted) in expected.items()
            if actual != wanted
        }
        if mismatches:
            raise KronosResearchContractError(f"unpinned Kronos identity: {mismatches}")

        if self.max_context != MAX_CONTEXT:
            raise KronosResearchContractError(f"max_context must remain {MAX_CONTEXT}")
        if not 1 < self.lookback <= self.max_context:
            raise KronosResearchContractError("lookback must be in [2, max_context]")
        if self.pred_len < 1:
            raise KronosResearchContractError("pred_len must be positive")
        if self.temperature != PAPER_FORECAST_TEMPERATURE:
            raise KronosResearchContractError(
                f"forecast temperature must be paper profile {PAPER_FORECAST_TEMPERATURE}"
            )
        if self.top_p != PAPER_FORECAST_TOP_P:
            raise KronosResearchContractError(
                f"forecast top_p must be paper profile {PAPER_FORECAST_TOP_P}"
            )
        if self.sample_count != PAPER_FORECAST_SAMPLE_COUNT:
            raise KronosResearchContractError(
                f"forecast sample_count must be paper profile {PAPER_FORECAST_SAMPLE_COUNT}"
            )
        if self.torch_seed < 0:
            raise KronosResearchContractError("torch_seed must be a non-negative integer")
        if not self.explicit_eval:
            raise KronosResearchContractError(
                "explicit_eval must be true; upstream from_pretrained() returns training mode"
            )
        if self.timestamp_timezone != "UTC":
            raise KronosResearchContractError("timestamp_timezone must be explicit UTC")
        if self.amount_policy != "volume_times_mean_ohlc":
            raise KronosResearchContractError(
                "amount_policy must record the upstream predictor's derived amount semantics"
            )

        if not 0.0 < self.alpha < 1.0:
            raise KronosResearchContractError("alpha must be strictly between 0 and 1")
        minimum = minimum_permutations_for_alpha(self.alpha)
        if self.n_permutations < minimum:
            raise KronosResearchContractError(
                f"{self.n_permutations} permutations cannot resolve p < {self.alpha}; "
                f"minimum is {minimum}"
            )
        if self.matched_control_axes != REQUIRED_MATCHED_CONTROL_AXES:
            raise KronosResearchContractError(
                "matched controls must use symbol, time, volatility, horizon and cost"
            )
        if self.time_split != "walk_forward":
            raise KronosResearchContractError("Kronos evaluation must use walk_forward")
        if not self.one_variable_at_a_time:
            raise KronosResearchContractError("one_variable_at_a_time must remain true")

        if self.holdout_read_allowed:
            raise KronosResearchContractError(
                "the research contract cannot authorize a holdout read"
            )
        if self.holdout_start_exclusive != HOLDOUT_START_ISO:
            raise KronosResearchContractError(
                f"holdout boundary must remain {HOLDOUT_START_ISO}"
            )
        if self.training_eligible or self.production_eligible or self.active_bundle_eligible:
            raise KronosResearchContractError(
                "Kronos remains research-only: all eligibility flags must be false"
            )

    def assert_context_end(self, when: Any) -> None:
        """Refuse any unapproved market context at or beyond the holdout boundary."""

        assert_pre_holdout(when, what="Kronos context end")


def load_kronos_research_contract(path: Path) -> KronosResearchContract:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise KronosResearchContractError("contract root must be a mapping")
    return KronosResearchContract.from_mapping(raw)

