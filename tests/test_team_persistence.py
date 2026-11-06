import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.teams import persist_teams
from football_analytics.integrations.base import Team


def test_team_persistence_is_idempotent() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)

    team = Team(
        provider="statsbomb",
        external_team_id=f"test-{uuid4()}",
        name="Test Team",
    )

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                first_created = persist_teams(connection, [team])

                first_id = connection.execute(
                    text("""
                        SELECT team_id
                        FROM core.external_id_mappings
                        WHERE provider = :provider
                          AND entity_type = 'team'
                          AND external_id = :external_id
                    """),
                    {
                        "provider": team.provider,
                        "external_id": team.external_team_id,
                    },
                ).scalar_one()

                second_created = persist_teams(connection, [team])

                second_id = connection.execute(
                    text("""
                        SELECT team_id
                        FROM core.external_id_mappings
                        WHERE provider = :provider
                          AND entity_type = 'team'
                          AND external_id = :external_id
                    """),
                    {
                        "provider": team.provider,
                        "external_id": team.external_team_id,
                    },
                ).scalar_one()

                assert first_created == 1
                assert second_created == 0
                assert first_id == second_id

                saved_name = connection.execute(
                    text("""
                        SELECT name FROM core.teams WHERE id = :id
                    """),
                    {"id": first_id},
                ).scalar_one()

                assert saved_name == "Test Team"

                changed_team = team.model_copy(update={"name": "Changed Name"})

                with pytest.raises(ValueError, match="Team name changed"):
                    persist_teams(connection, [changed_team])

            finally:
                transaction.rollback()
    finally:
        engine.dispose()
