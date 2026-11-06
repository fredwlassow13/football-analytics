from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.integrations.base import Match


def resolve_match_references(
    connection: Connection,
    match: Match,
) -> tuple[UUID, UUID, UUID]:
    """Return internal season, home team and away team IDs."""
    season_id = connection.execute(
        text("""
            SELECT season.id
            FROM core.external_id_mappings AS mapping
            JOIN core.seasons AS season
                ON season.id = mapping.season_id
            JOIN core.external_id_mappings AS competition_mapping
                ON competition_mapping.competition_id = season.competition_id
            WHERE mapping.provider = :provider
              AND mapping.entity_type = 'season'
              AND mapping.external_id = :season_external_id
              AND competition_mapping.provider = :provider
              AND competition_mapping.entity_type = 'competition'
              AND competition_mapping.external_id = :competition_external_id
        """),
        {
            "provider": match.provider,
            "season_external_id": (f"{match.external_competition_id}:{match.external_season_id}"),
            "competition_external_id": match.external_competition_id,
        },
    ).scalar_one_or_none()

    if season_id is None:
        raise ValueError(
            "Competition/season mapping not found for "
            f"{match.provider}:"
            f"{match.external_competition_id}:"
            f"{match.external_season_id}"
        )

    team_ids: list[UUID] = []

    for external_id in (
        match.external_home_team_id,
        match.external_away_team_id,
    ):
        team_id = connection.execute(
            text("""
                SELECT team_id
                FROM core.external_id_mappings
                WHERE provider = :provider
                  AND entity_type = 'team'
                  AND external_id = :external_id
            """),
            {
                "provider": match.provider,
                "external_id": external_id,
            },
        ).scalar_one_or_none()

        if team_id is None:
            raise ValueError(f"Team mapping not found for {match.provider}:{external_id}")

        team_ids.append(team_id)

    return season_id, team_ids[0], team_ids[1]


def persist_matches(
    connection: Connection,
    matches: list[Match],
) -> int:
    """Persist matches inside a transaction managed by the caller."""
    created = 0

    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CORE_INGESTION_LOCK_ID},
    )

    for match in matches:
        season_id, home_team_id, away_team_id = resolve_match_references(connection, match)

        existing = connection.execute(
            text("""
                SELECT
                    m.season_id,
                    m.home_team_id,
                    m.away_team_id,
                    m.match_date,
                    m.home_score,
                    m.away_score
                FROM core.external_id_mappings AS mapping
                JOIN core.matches AS m
                    ON m.id = mapping.match_id
                WHERE mapping.provider = :provider
                  AND mapping.entity_type = 'match'
                  AND mapping.external_id = :external_id
            """),
            {
                "provider": match.provider,
                "external_id": match.external_match_id,
            },
        ).one_or_none()

        expected = (
            season_id,
            home_team_id,
            away_team_id,
            match.match_date,
            match.home_score,
            match.away_score,
        )

        if existing is not None:
            if tuple(existing) != expected:
                raise ValueError(
                    f"Match data changed for {match.provider}:{match.external_match_id}"
                )

            continue

        match_id = uuid4()

        connection.execute(
            text("""
                INSERT INTO core.matches (
                    id,
                    season_id,
                    home_team_id,
                    away_team_id,
                    match_date,
                    home_score,
                    away_score
                )
                VALUES (
                    :id,
                    :season_id,
                    :home_team_id,
                    :away_team_id,
                    :match_date,
                    :home_score,
                    :away_score
                )
            """),
            {
                "id": match_id,
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "match_date": match.match_date,
                "home_score": match.home_score,
                "away_score": match.away_score,
            },
        )

        connection.execute(
            text("""
                INSERT INTO core.external_id_mappings (
                    id,
                    provider,
                    entity_type,
                    external_id,
                    match_id
                )
                VALUES (
                    :id,
                    :provider,
                    'match',
                    :external_id,
                    :match_id
                )
            """),
            {
                "id": uuid4(),
                "provider": match.provider,
                "external_id": match.external_match_id,
                "match_id": match_id,
            },
        )

        created += 1

    return created
