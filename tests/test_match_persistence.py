import os
from datetime import date
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.catalog import ingest_catalog
from football_analytics.ingestion.matches import persist_matches
from football_analytics.ingestion.teams import persist_teams
from football_analytics.integrations.base import Match, Team
from football_analytics.integrations.statsbomb.client import StatsBombClient


def test_match_persistence_is_idempotent() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)
    identifier = str(uuid4().int)

    catalog = [
        {
            "competition_id": int(identifier),
            "season_id": 1,
            "competition_name": f"Test competition {identifier}",
            "season_name": "Test season",
            "country_name": "International",
            "competition_gender": "male",
        }
    ]

    try:
        # Prepara competição e temporada usando um fornecedor simulado.
        with httpx.Client(
            base_url="https://example.test/",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=catalog)),
        ) as http:
            ingest_catalog(
                engine,
                StatsBombClient(http),
                competition=identifier,
                season="1",
            )

        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                persist_teams(
                    connection,
                    [
                        Team(
                            provider="statsbomb",
                            external_team_id=f"{identifier}-home",
                            name="Test Home",
                        ),
                        Team(
                            provider="statsbomb",
                            external_team_id=f"{identifier}-away",
                            name="Test Away",
                        ),
                    ],
                )

                match = Match(
                    provider="statsbomb",
                    external_match_id=identifier,
                    external_competition_id=identifier,
                    external_season_id="1",
                    external_home_team_id=f"{identifier}-home",
                    external_away_team_id=f"{identifier}-away",
                    match_date=date(2022, 11, 20),
                    home_score=0,
                    away_score=None,
                )

                assert persist_matches(connection, [match]) == 1

                lookup = text("""
                    SELECT match_id
                    FROM core.external_id_mappings
                    WHERE provider = 'statsbomb'
                      AND entity_type = 'match'
                      AND external_id = :external_id
                """)

                first_id = connection.execute(
                    lookup,
                    {"external_id": identifier},
                ).scalar_one()

                assert persist_matches(connection, [match]) == 0

                second_id = connection.execute(
                    lookup,
                    {"external_id": identifier},
                ).scalar_one()

                assert first_id == second_id

                saved = connection.execute(
                    text("""
                        SELECT home_score, away_score, kickoff_at
                        FROM core.matches
                        WHERE id = :id
                    """),
                    {"id": first_id},
                ).one()

                assert saved.home_score == 0
                assert saved.away_score is None
                assert saved.kickoff_at is None

                changed_match = match.model_copy(update={"home_score": 3})

                with pytest.raises(
                    ValueError,
                    match="Match data changed",
                ):
                    persist_matches(connection, [changed_match])

                missing_team_match = match.model_copy(
                    update={"external_home_team_id": "missing-team"}
                )

                with pytest.raises(
                    ValueError,
                    match="Team mapping not found",
                ):
                    persist_matches(connection, [missing_team_match])

            finally:
                transaction.rollback()

    finally:
        engine.dispose()
