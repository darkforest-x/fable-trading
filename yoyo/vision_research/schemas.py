"""Bounded request/result contracts for manual visual pattern research.

Gemini's JSON mode is only a transport contract, not a correctness guarantee.
Source: https://ai.google.dev/gemini-api/docs/structured-output
No market feature is computed here; uploaded images have unverified time bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_CRITERIA = (
    "只判断当前可见的双均线密集启动形态，不能利用未来涨跌。"
    "观察均线是否先持续收拢，再出现有方向的启动。"
    "区分收拢核心、启动和事后确认；标准或画面不清楚则回答不确定。"
    "逐项说明图中可观察的支持和反对证据，不猜测看不清的精确价格。"
)


@dataclass(frozen=True)
class ImageInput:
    name: str
    mime_type: str
    data: bytes
    sha256: str
    width: int
    height: int


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Literal["match", "no_match", "uncertain"]
    side: Literal["long", "short", "unknown"]
    summary: str = Field(min_length=1, max_length=3000)
    evidence: List[str] = Field(max_length=20)
    risks: List[str] = Field(max_length=20)
    box_2d: Optional[List[int]] = Field(
        default=None,
        description="0..1000 normalized bounding box in [ymin, xmin, ymax, xmax] order (top, left, bottom, right); null when boundaries are unclear.",
    )

    @model_validator(mode="after")
    def validate_box(self):
        if any(len(item) > 1500 for item in self.evidence + self.risks):
            raise ValueError("Evidence item is too long")
        if self.box_2d is not None:
            box = self.box_2d
            if len(box) != 4 or any(v < 0 or v > 1000 for v in box):
                raise ValueError("box_2d must contain four coordinates within 0..1000")
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("box_2d must have positive area")
        return self


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="reference", max_length=160)
    data_url: str = Field(max_length=12_000_000)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_id: Optional[str] = Field(default=None, max_length=100)
    image_data_url: Optional[str] = Field(default=None, max_length=12_000_000)
    image_name: Optional[str] = Field(default=None, max_length=160)
    references: List[ReferenceRequest] = Field(default_factory=list, max_length=4)
    criteria: str = Field(default=DEFAULT_CRITERIA, min_length=10, max_length=8000)
    model: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def one_image_source(self):
        if bool(self.signal_id) == bool(self.image_data_url):
            raise ValueError("Choose either a SPIKE candidate or an uploaded image")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["accepted", "rejected", "uncertain"]
    note: str = Field(default="", max_length=4000)


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=100)
    api_key: Optional[str] = Field(default=None, min_length=1, max_length=512)
