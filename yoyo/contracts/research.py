"""Versioned research composition contracts shared by management and runners.

A composition binds existing registered assets; it is not executable Python.
Execution remains with explicit adapters, following Qlib's dataset/model/task
separation. A saved composition never grants training or production admission.
https://qlib.readthedocs.io/en/stable/component/workflow.html
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PipelineRef(ResearchContract):
    id: str = Field(pattern=r"^pipeline-[a-f0-9]{16}$")
    revision: int = Field(ge=1)


class PipelineDefinition(ResearchContract):
    name: str = Field(min_length=2, max_length=120)
    route: Literal["rules", "yolo_lgbm", "vlm"]
    dataset_ids: list[str] = Field(default_factory=list, max_length=20)
    factor_ids: list[str] = Field(default_factory=list, max_length=40)
    model_ids: list[str] = Field(default_factory=list, max_length=20)
    strategy_id: str = Field(default="", max_length=100)
    experiment_id: str = Field(default="", max_length=180)
    notes: str = Field(default="", max_length=12000)
    stage: Literal["research", "archived"] = "research"
    expected_revision: int = Field(default=0, ge=0)

    @field_validator("dataset_ids", "factor_ids", "model_ids")
    @classmethod
    def unique_references(cls, value):
        if any(not x or len(x) > 240 for x in value) or len(value) != len(set(value)):
            raise ValueError("Asset references must be nonempty, bounded and unique.")
        return value


class PipelineRun(ResearchContract):
    expected_revision: int = Field(ge=1)
    mode: Literal["backtest", "paper"]
    symbols: list[str] = Field(min_length=1, max_length=20)
    timeframes: Optional[list[str]] = Field(default=None, min_length=1, max_length=4)
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
