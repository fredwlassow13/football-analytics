from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine
from football_analytics.integrations.statsbomb.clock import format_duration_ms
from football_analytics.modules.matches.participation_schemas import (
    MatchParticipationResponse,
    ParticipationCalculationResponse,
    ParticipationIntervalResponse,
    PlayerParticipationResponse,
)

router = APIRouter(
    prefix="/api/v1/matches",
    tags=["matches"],
)


@router.get(
    "/{match_id}/participation",
    response_model=MatchParticipationResponse,
)
def get_match_participation(
    match_id: UUID,
    calculation_id: UUID,
) -> MatchParticipationResponse:
    with get_engine().connect() as connection:
        calculation = (
            connection.execute(
                text("""
                SELECT
                    id,
                    match_id,
                    metric_name,
                    formula_version,
                    created_at
                FROM analytics.participation_calculations
                WHERE id = :calculation_id
                  AND match_id = :match_id
            """),
                {
                    "calculation_id": calculation_id,
                    "match_id": match_id,
                },
            )
            .mappings()
            .one_or_none()
        )

        if calculation is None:
            raise HTTPException(
                status_code=404,
                detail="Participation calculation not found for this match",
            )

        player_rows = (
            connection.execute(
                text("""
                SELECT
                    result.id AS participation_id,
                    player.id AS player_id,
                    player.name AS player_name,
                    team.id AS team_id,
                    team.name AS team_name,
                    result.is_starter,
                    result.has_participation_evidence,
                    result.elapsed_on_field_ms
                FROM analytics.player_match_participation AS result
                JOIN core.match_lineups AS lineup
                    ON lineup.id = result.lineup_id
                JOIN core.players AS player
                    ON player.id = lineup.player_id
                JOIN core.teams AS team
                    ON team.id = lineup.team_id
                WHERE result.calculation_id = :calculation_id
                ORDER BY team.name, player.name, player.id
            """),
                {"calculation_id": calculation_id},
            )
            .mappings()
            .all()
        )

        interval_rows = (
            connection.execute(
                text("""
                SELECT
                    interval.participation_id,
                    interval.interval_number,
                    interval.start_ms,
                    interval.end_ms,
                    interval.end_ms - interval.start_ms AS duration_ms
                FROM analytics.player_participation_intervals AS interval
                JOIN analytics.player_match_participation AS result
                    ON result.id = interval.participation_id
                WHERE result.calculation_id = :calculation_id
                ORDER BY interval.participation_id, interval.interval_number
            """),
                {"calculation_id": calculation_id},
            )
            .mappings()
            .all()
        )

    intervals_by_result: dict[
        UUID,
        list[ParticipationIntervalResponse],
    ] = defaultdict(list)

    for row in interval_rows:
        intervals_by_result[row["participation_id"]].append(
            ParticipationIntervalResponse(
                interval_number=row["interval_number"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                duration_ms=row["duration_ms"],
            )
        )

    items: list[PlayerParticipationResponse] = []

    for row in player_rows:
        duration = row["elapsed_on_field_ms"]

        items.append(
            PlayerParticipationResponse(
                player_id=row["player_id"],
                player_name=row["player_name"],
                team_id=row["team_id"],
                team_name=row["team_name"],
                is_starter=row["is_starter"],
                has_participation_evidence=row["has_participation_evidence"],
                elapsed_on_field_ms=duration,
                elapsed_on_field_display=(
                    format_duration_ms(duration) if duration is not None else None
                ),
                intervals=intervals_by_result[row["participation_id"]],
            )
        )

    return MatchParticipationResponse(
        calculation=ParticipationCalculationResponse.model_validate(dict(calculation)),
        items=items,
        total=len(items),
    )
