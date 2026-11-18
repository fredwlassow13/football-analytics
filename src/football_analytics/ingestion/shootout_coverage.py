from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.shootout_inputs import (
    ShootoutPreparedPlayer,
)


class ShootoutCoverageAudit(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineups_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    sent_off_player_ids: tuple[str, ...] | None = None
    excluded_player_ids: tuple[str, ...] | None = None


def apply_shootout_coverage_audit(
    prepared: Sequence[ShootoutPreparedPlayer],
    audit: ShootoutCoverageAudit,
    *,
    events_sha256: str,
    lineups_sha256: str,
) -> tuple[ShootoutPreparedPlayer, ...]:
    """Apply declared source review; do not infer coverage completeness."""
    if audit.events_sha256 != events_sha256 or audit.lineups_sha256 != lineups_sha256:
        raise ValueError("Coverage audit source hashes differ")

    player_ids = [item.presence.external_player_id for item in prepared]
    roster_ids = set(player_ids)

    if len(player_ids) != len(roster_ids):
        raise ValueError("Duplicate prepared player")

    for reviewed_ids in (
        audit.sent_off_player_ids,
        audit.excluded_player_ids,
    ):
        if reviewed_ids is None:
            continue

        if len(reviewed_ids) != len(set(reviewed_ids)):
            raise ValueError("Duplicate audited player")

        if not set(reviewed_ids).issubset(roster_ids):
            raise ValueError("Audit references an unlisted player")

    result: list[ShootoutPreparedPlayer] = []

    for item in prepared:
        player_id = item.presence.external_player_id
        context = item.eligibility_context
        updates: dict[str, bool] = {}

        if audit.sent_off_player_ids is not None:
            sent_off = player_id in audit.sent_off_player_ids

            if (
                context.sent_off is True
                or context.last_exit_reason in {"Red Card", "Second Yellow"}
            ) and not sent_off:
                raise ValueError("Audit contradicts recorded sending off")

            updates["sent_off"] = sent_off

        if audit.excluded_player_ids is not None:
            excluded = player_id in audit.excluded_player_ids

            if context.excluded_to_equalise is True and not excluded:
                raise ValueError("Audit contradicts recorded exclusion")

            updates["excluded_to_equalise"] = excluded

        result.append(
            ShootoutPreparedPlayer(
                presence=item.presence,
                eligibility_context=context.model_copy(update=updates),
            )
        )

    return tuple(result)
