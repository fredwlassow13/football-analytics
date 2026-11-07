from uuid import uuid4

from sqlalchemy import Connection, text

from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.integrations.base import Player


def persist_players(
    connection: Connection,
    players: list[Player],
) -> int:
    """Persist players inside a transaction managed by the caller."""
    created = 0

    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CORE_INGESTION_LOCK_ID},
    )

    for player in players:
        existing = connection.execute(
            text("""
                SELECT
                    p.name,
                    p.nickname,
                    p.nationality,
                    p.birth_date
                FROM core.external_id_mappings AS mapping
                JOIN core.players AS p
                    ON p.id = mapping.player_id
                WHERE mapping.provider = :provider
                  AND mapping.entity_type = 'player'
                  AND mapping.external_id = :external_id
            """),
            {
                "provider": player.provider,
                "external_id": player.external_player_id,
            },
        ).one_or_none()

        expected = (
            player.name,
            player.nickname,
            player.nationality,
            player.birth_date,
        )

        if existing is not None:
            if tuple(existing) != expected:
                raise ValueError(
                    f"Player data changed for {player.provider}:{player.external_player_id}"
                )

            continue

        player_id = uuid4()

        connection.execute(
            text("""
                INSERT INTO core.players (
                    id,
                    name,
                    nickname,
                    nationality,
                    birth_date
                )
                VALUES (
                    :id,
                    :name,
                    :nickname,
                    :nationality,
                    :birth_date
                )
            """),
            {
                "id": player_id,
                "name": player.name,
                "nickname": player.nickname,
                "nationality": player.nationality,
                "birth_date": player.birth_date,
            },
        )

        connection.execute(
            text("""
                INSERT INTO core.external_id_mappings (
                    id,
                    provider,
                    entity_type,
                    external_id,
                    player_id
                )
                VALUES (
                    :id,
                    :provider,
                    'player',
                    :external_id,
                    :player_id
                )
            """),
            {
                "id": uuid4(),
                "provider": player.provider,
                "external_id": player.external_player_id,
                "player_id": player_id,
            },
        )

        created += 1

    return created
