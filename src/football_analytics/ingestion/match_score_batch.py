"""Batch execution of the existing read-only score verifier."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from football_analytics.ingestion.match_score_verification import verify_match_score


class MatchScoreBatchItem(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    match_id: int
    status: Literal["success", "review", "failed"]
    report: dict[str, object] | None = None
    error_type: str | None = None
    error_message: str | None = None


class MatchScoreBatchResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    scope: Literal["score_reconciliation"] = "score_reconciliation"
    requested_match_ids: tuple[int, ...]
    items: tuple[MatchScoreBatchItem, ...]
    success_count: int
    review_count: int
    failed_count: int


def verify_match_scores(
    connection: Connection,
    match_ids: Sequence[int],
) -> MatchScoreBatchResult:
    requested = tuple(match_ids)
    if (
        not requested
        or any(type(match_id) is not int or match_id <= 0 for match_id in requested)
        or len(requested) != len(set(requested))
    ):
        raise ValueError("Provide unique positive integer match IDs")

    items: list[MatchScoreBatchItem] = []
    for match_id in requested:
        try:
            # A failed SQL statement must not poison the next match's transaction.
            with connection.begin_nested():
                report = verify_match_score(connection, match_id=match_id)
                if type(report.get("match_id")) is not int or report["match_id"] != match_id:
                    raise ValueError("Verifier returned another match identity")
                score_status = report.get("status")
                if score_status not in {"matched", "mismatch", "review"}:
                    raise ValueError("Verifier returned an unsupported score status")
                item = MatchScoreBatchItem(
                    match_id=match_id,
                    status="success" if score_status == "matched" else "review",
                    report=report,
                )
        except Exception as exc:
            item = MatchScoreBatchItem(
                match_id=match_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc)[:2000],
            )
        items.append(item)

    return MatchScoreBatchResult(
        requested_match_ids=requested,
        items=tuple(items),
        success_count=sum(item.status == "success" for item in items),
        review_count=sum(item.status == "review" for item in items),
        failed_count=sum(item.status == "failed" for item in items),
    )
