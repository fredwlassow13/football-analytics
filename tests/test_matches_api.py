import os
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from football_analytics.main import app


def test_matches_api_filter_and_pagination() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)
    competition_id = uuid4()
    season_id = uuid4()
    home_id = uuid4()
    away_id = uuid4()

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                connection.execute(
                    text("""
                        INSERT INTO core.competitions (
                            id, name, country_name, gender
                        )
                        VALUES (
                            :id, 'API Test', 'International', 'male'
                        )
                    """),
                    {"id": competition_id},
                )

                connection.execute(
                    text("""
                        INSERT INTO core.seasons (
                            id, competition_id, name
                        )
                        VALUES (:id, :competition_id, 'Test season')
                    """),
                    {
                        "id": season_id,
                        "competition_id": competition_id,
                    },
                )

                connection.execute(
                    text("""
                        INSERT INTO core.teams (id, name)
                        VALUES (:id, :name)
                    """),
                    [
                        {"id": home_id, "name": "Test Home"},
                        {"id": away_id, "name": "Test Away"},
                    ],
                )

                for day in (20, 21, 22):
                    connection.execute(
                        text("""
                            INSERT INTO core.matches (
                                id, season_id,
                                home_team_id, away_team_id,
                                match_date, home_score, away_score
                            )
                            VALUES (
                                :id, :season_id,
                                :home_id, :away_id,
                                :match_date, 0, NULL
                            )
                        """),
                        {
                            "id": uuid4(),
                            "season_id": season_id,
                            "home_id": home_id,
                            "away_id": away_id,
                            "match_date": date(2022, 11, day),
                        },
                    )

                with patch("football_analytics.modules.matches.router.get_engine") as mocked_engine:
                    mocked_engine.return_value.connect.side_effect = lambda: nullcontext(connection)

                    with TestClient(app) as client:
                        response = client.get(
                            "/api/v1/matches",
                            params={
                                "season_id": str(season_id),
                                "limit": 1,
                                "offset": 1,
                            },
                        )

                        assert response.status_code == 200

                        body = response.json()

                        assert body["total"] == 3
                        assert body["limit"] == 1
                        assert body["offset"] == 1
                        assert len(body["items"]) == 1

                        match = body["items"][0]

                        assert match["season_id"] == str(season_id)
                        assert match["match_date"] == "2022-11-21"
                        assert match["home_team_name"] == "Test Home"
                        assert match["away_team_name"] == "Test Away"
                        assert match["home_score"] == 0
                        assert match["away_score"] is None
                        assert match["kickoff_at"] is None

                        empty = client.get(
                            "/api/v1/matches",
                            params={"season_id": str(uuid4())},
                        )

                        assert empty.status_code == 200
                        assert empty.json()["total"] == 0
                        assert empty.json()["items"] == []

                        for parameters in (
                            {"limit": 0},
                            {"limit": 101},
                            {"offset": -1},
                            {"season_id": "invalid"},
                        ):
                            invalid = client.get(
                                "/api/v1/matches",
                                params=parameters,
                            )

                            assert invalid.status_code == 422

            finally:
                transaction.rollback()

    finally:
        engine.dispose()
