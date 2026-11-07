import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.players import persist_players
from football_analytics.integrations.base import Player


def test_player_persistence_is_idempotent() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)

    player = Player(
        provider="statsbomb",
        external_player_id=f"test-{uuid4()}",
        name="Test Player",
        nationality="Argentina",
    )

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                assert persist_players(connection, [player]) == 1

                lookup = text("""
                    SELECT player_id
                    FROM core.external_id_mappings
                    WHERE provider = :provider
                      AND entity_type = 'player'
                      AND external_id = :external_id
                """)

                parameters = {
                    "provider": player.provider,
                    "external_id": player.external_player_id,
                }

                first_id = connection.execute(
                    lookup,
                    parameters,
                ).scalar_one()

                assert persist_players(connection, [player]) == 0

                second_id = connection.execute(
                    lookup,
                    parameters,
                ).scalar_one()

                assert first_id == second_id

                saved = connection.execute(
                    text("""
                        SELECT name, nickname, nationality, birth_date
                        FROM core.players
                        WHERE id = :id
                    """),
                    {"id": first_id},
                ).one()

                assert saved.name == "Test Player"
                assert saved.nickname is None
                assert saved.nationality == "Argentina"
                assert saved.birth_date is None

                changed_player = player.model_copy(update={"name": "Changed Name"})

                with pytest.raises(
                    ValueError,
                    match="Player data changed",
                ):
                    persist_players(connection, [changed_player])

                unchanged_name = connection.execute(
                    text("""
                        SELECT name
                        FROM core.players
                        WHERE id = :id
                    """),
                    {"id": first_id},
                ).scalar_one()

                assert unchanged_name == "Test Player"

            finally:
                transaction.rollback()

    finally:
        engine.dispose()
