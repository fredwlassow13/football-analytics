from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.integrations.base import MatchLineup


def resolve_lineup_id(
    connection: Connection,
    provider: str,
    entity_type: str,
    external_id: str,
) -> UUID:
    columns = {
        "match": "match_id",
        "team": "team_id",
        "player": "player_id",
    }

    if entity_type not in columns:
        raise ValueError(f"Unsupported entity type: {entity_type}")

    # O nome da coluna vem exclusivamente da lista acima.
    column = columns[entity_type]

    internal_id: UUID | None = connection.execute(
        text(f"""
            SELECT {column}
            FROM core.external_id_mappings
            WHERE provider = :provider
              AND entity_type = :entity_type
              AND external_id = :external_id
        """),
        {
            "provider": provider,
            "entity_type": entity_type,
            "external_id": external_id,
        },
    ).scalar_one_or_none()

    if internal_id is None:
        raise ValueError(f"{entity_type} mapping not found for {provider}:{external_id}")

    return internal_id


def persist_lineups(
    connection: Connection,
    lineups: list[MatchLineup],
) -> int:
    """Persist lineups inside a transaction managed by the caller."""
    created = 0

    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CORE_INGESTION_LOCK_ID},
    )

    for item in lineups:
        match_id = resolve_lineup_id(
            connection,
            item.provider,
            "match",
            item.external_match_id,
        )

        team_id = resolve_lineup_id(
            connection,
            item.provider,
            "team",
            item.external_team_id,
        )

        player_id = resolve_lineup_id(
            connection,
            item.provider,
            "player",
            item.external_player_id,
        )

        match_teams = connection.execute(
            text("""
                SELECT home_team_id, away_team_id
                FROM core.matches
                WHERE id = :id
            """),
            {"id": match_id},
        ).one()

        if team_id not in (
            match_teams.home_team_id,
            match_teams.away_team_id,
        ):
            raise ValueError(
                f"Team {item.external_team_id} does not belong to match {item.external_match_id}"
            )

        existing = connection.execute(
            text("""
                SELECT team_id, shirt_number, is_starter
                FROM core.match_lineups
                WHERE match_id = :match_id
                  AND player_id = :player_id
            """),
            {
                "match_id": match_id,
                "player_id": player_id,
            },
        ).one_or_none()

        if existing is not None:
            existing_identity = (
                existing.team_id,
                existing.shirt_number,
            )

            incoming_identity = (
                team_id,
                item.shirt_number,
            )

            if existing_identity != incoming_identity:
                raise ValueError(
                    "Lineup data changed for "
                    f"{item.provider}:"
                    f"{item.external_match_id}:"
                    f"{item.external_player_id}"
                )

            if item.is_starter is not None:
                if existing.is_starter is not None and existing.is_starter != item.is_starter:
                    raise ValueError(
                        "Starter classification changed for "
                        f"{item.provider}:"
                        f"{item.external_match_id}:"
                        f"{item.external_player_id}"
                    )

                if existing.is_starter is None:
                    connection.execute(
                        text("""
                            UPDATE core.match_lineups
                            SET is_starter = :is_starter
                            WHERE match_id = :match_id
                              AND player_id = :player_id
                              AND is_starter IS NULL
                        """),
                        {
                            "is_starter": item.is_starter,
                            "match_id": match_id,
                            "player_id": player_id,
                        },
                    )

            continue

        connection.execute(
            text("""
                INSERT INTO core.match_lineups (
                    id,
                    match_id,
                    team_id,
                    player_id,
                    shirt_number,
                    is_starter
                )
                VALUES (
                    :id,
                    :match_id,
                    :team_id,
                    :player_id,
                    :shirt_number,
                    :is_starter
                )
            """),
            {
                "id": uuid4(),
                "match_id": match_id,
                "team_id": team_id,
                "player_id": player_id,
                "shirt_number": item.shirt_number,
                "is_starter": item.is_starter,
            },
        )

        created += 1

    return created
