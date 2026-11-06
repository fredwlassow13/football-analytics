from uuid import uuid4

from sqlalchemy import Connection, text

from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.integrations.base import Team


def persist_teams(
    connection: Connection,
    teams: list[Team],
) -> int:
    """Persist teams inside a transaction managed by the caller."""
    created = 0

    # Mesmo lock usado na ingestão do catálogo.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CORE_INGESTION_LOCK_ID},
    )

    for team in teams:
        existing = connection.execute(
            text("""
                SELECT t.id, t.name
                FROM core.external_id_mappings AS mapping
                JOIN core.teams AS t ON t.id = mapping.team_id
                WHERE mapping.provider = :provider
                  AND mapping.entity_type = 'team'
                  AND mapping.external_id = :external_id
            """),
            {
                "provider": team.provider,
                "external_id": team.external_team_id,
            },
        ).one_or_none()

        if existing is not None:
            if existing.name != team.name:
                raise ValueError(
                    f"Team name changed for "
                    f"{team.provider}:{team.external_team_id}: "
                    f"{existing.name!r} -> {team.name!r}"
                )

            continue

        team_id = uuid4()

        connection.execute(
            text("""
                INSERT INTO core.teams (id, name)
                VALUES (:id, :name)
            """),
            {
                "id": team_id,
                "name": team.name,
            },
        )

        connection.execute(
            text("""
                INSERT INTO core.external_id_mappings (
                    id,
                    provider,
                    entity_type,
                    external_id,
                    team_id
                )
                VALUES (
                    :id,
                    :provider,
                    'team',
                    :external_id,
                    :team_id
                )
            """),
            {
                "id": uuid4(),
                "provider": team.provider,
                "external_id": team.external_team_id,
                "team_id": team_id,
            },
        )

        created += 1

    return created
