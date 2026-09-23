"""Bounded request/result contracts for manual visual pattern research.

Structured model output is a transport contract, not a correctness guarantee.
Source: https://docs.bigmodel.cn/api-reference/模型-api/对话补全
No market feature is computed here; uploaded images have unverified time bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODEL = "glm-5.3-flash"
# Documented vision families below accept 50 total images, including the candidate.
VISION_MODELS = frozenset({"glm-5.3-flash", "glm-5.3-flashx", "glm-5v-turbo",
                           "glm-4.6v", "glm-4.6v-flash", "glm-4.6v-flashx"})
MAX_REFERENCES = 49
DEFAULT_CRITERIA = (
    "只判断图表最右端的当前盘口：均线仍在密集、正在启动，还是已经离开密集区。"
    "左侧历史形态只作背景，不能因为以前出现过密集启动就判断当前符合。"
    "只有右端正在发生先收拢后启动才可判符合；已经明显发散或远离则不符合。"
    "仍在收拢而未启动、标准或画面不清楚时回答不确定。"
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


class CurrentDecision(Decision):
    """Require a present-tense state; old whole-chart decisions stay unchanged."""

    assessment_scope: Literal["current_right_edge"]
    current_state: Literal["converging", "launching", "extended", "no_setup", "unclear"]

    @model_validator(mode="after")
    def validate_current_state(self):
        if self.verdict == "match" and self.current_state != "launching":
            raise ValueError("Only a current right-edge launch can match")
        if self.current_state in {"extended", "no_setup"} and self.verdict != "no_match":
            raise ValueError("An old or absent setup is not a current match")
        if self.current_state in {"converging", "unclear"} and self.verdict != "uncertain":
            raise ValueError("Convergence alone or unclear evidence cannot confirm a launch")
        if self.current_state in {"extended", "no_setup", "unclear"} and self.box_2d is not None:
            raise ValueError("Do not box a historical setup when no current core is identified")
        return self


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="reference", max_length=160)
    data_url: str = Field(max_length=12_000_000)


class ReferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: List[ReferenceRequest] = Field(max_length=MAX_REFERENCES)
    expected_revision: Optional[int] = Field(default=None, ge=0)


class ChartViewport(BaseModel):
    """Browser-declared logical range; never a cryptographic pixel attestation."""
    model_config = ConfigDict(extra="forbid", strict=True)

    from_: float = Field(alias="from")
    to: float
    bar_count: int = Field(ge=1, le=120)
    last_bar_open_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if not math.isfinite(self.from_) or not math.isfinite(self.to) or self.from_ >= self.to:
            raise ValueError("Invalid visible logical range")
        last = self.bar_count - 1
        if self.from_ > last - 0.5 or self.to < last + 0.5:
            raise ValueError("The complete rightmost candle must be visible")
        return self


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_id: Optional[str] = Field(default=None, max_length=100)
    image_data_url: Optional[str] = Field(default=None, max_length=12_000_000)
    chart_capture_data_url: Optional[str] = Field(default=None, max_length=12_000_000)
    image_name: Optional[str] = Field(default=None, max_length=160)
    expected_image_sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_chart_sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    chart_snapshot_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    chart_viewport: Optional[ChartViewport] = None
    reference_revision: Optional[int] = Field(default=None, ge=0)
    references: Optional[List[ReferenceRequest]] = Field(default=None, max_length=MAX_REFERENCES)
    criteria: str = Field(default=DEFAULT_CRITERIA, min_length=10, max_length=8000)
    model: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def one_image_source(self):
        if bool(self.signal_id) == bool(self.image_data_url):
            raise ValueError("Choose either a SPIKE candidate or an uploaded image")
        if self.chart_capture_data_url and not self.signal_id:
            raise ValueError("A browser chart capture must include a SPIKE candidate")
        if self.expected_chart_sha256 and not self.signal_id:
            raise ValueError("A chart hash must include a SPIKE candidate")
        if self.chart_viewport is not None and not self.chart_capture_data_url:
            raise ValueError("A viewport must describe a browser chart capture")
        if self.chart_snapshot_id and not (self.signal_id and self.chart_capture_data_url and self.expected_chart_sha256):
            raise ValueError("A live snapshot requires a candidate, capture and chart hash")
        if self.references is not None and self.reference_revision is not None:
            raise ValueError("reference_revision applies only when using saved global references")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["accepted", "rejected", "uncertain"]
    note: str = Field(default="", max_length=4000)


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=100)
    api_key: Optional[str] = Field(default=None, min_length=1, max_length=512)
