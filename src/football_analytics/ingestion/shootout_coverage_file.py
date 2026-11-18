from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from football_analytics.ingestion.shootout_coverage import (
    ShootoutCoverageAudit,
)

ReviewText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ShootoutCoverageReview(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    justification: ReviewText
    evidence: tuple[ReviewText, ...] = Field(min_length=1)
    audit: ShootoutCoverageAudit


def load_shootout_coverage_review(
    path: Path,
) -> ShootoutCoverageReview:
    return ShootoutCoverageReview.model_validate_json(path.read_text(encoding="utf-8"))
