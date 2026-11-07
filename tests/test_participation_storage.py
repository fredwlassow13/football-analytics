import hashlib
import json
import os
from contextlib import nullcontext
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.participation import (
    ParticipationInterval,
)
from football_analytics.ingestion.participation_storage import (
    get_or_create_calculation,
    persist_participation_results,
)
from football_analytics.main import app


def test_participation_storage() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)

    ids = {
        key: uuid4()
        for key in (
            "competition",
            "season",
            "home",
            "away",
            "match",
            "player_a",
            "player_b",
            "player_unknown",
            "lineup_a",
            "lineup_b",
            "lineup_unknown",
            "run",
            "lineups_payload",
            "events_payload",
        )
    }

    external = {
        key: str(uuid4().int)
        for key in (
            "match",
            "home",
            "away",
            "player_a",
            "player_b",
            "player_unknown",
        )
    }

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                statements = [
                    """
                    INSERT INTO core.competitions
                        (id, name, country_name, gender)
                    VALUES
                        (:competition, 'Storage Test', 'International', 'male')
                    """,
                    """
                    INSERT INTO core.seasons (id, competition_id, name)
                    VALUES (:season, :competition, 'Test season')
                    """,
                    """
                    INSERT INTO core.teams (id, name)
                    VALUES (:home, 'Home'), (:away, 'Away')
                    """,
                    """
                    INSERT INTO core.players (id, name)
                    VALUES
                        (:player_a, 'Player A'),
                        (:player_b, 'Player B'),
                        (:player_unknown, 'Unknown participation')
                    """,
                    """
                    INSERT INTO core.matches (
                        id, season_id, home_team_id,
                        away_team_id, match_date
                    )
                    VALUES (
                        :match, :season, :home,
                        :away, DATE '2022-12-18'
                    )
                    """,
                    """
                    INSERT INTO core.match_lineups (
                        id, match_id, team_id, player_id
                    )
                    VALUES
                        (:lineup_a, :match, :home, :player_a),
                        (:lineup_b, :match, :away, :player_b),
                        (:lineup_unknown, :match, :home, :player_unknown)
                    """,
                    """
                    INSERT INTO raw.ingestion_runs (
                        id, provider, pipeline_name, status
                    )
                    VALUES (
                        :run, 'statsbomb', 'participation_test', 'running'
                    )
                    """,
                ]

                for statement in statements:
                    connection.execute(text(statement), ids)

                mappings = [
                    ("match", "match", "match_id"),
                    ("home", "team", "team_id"),
                    ("away", "team", "team_id"),
                    ("player_a", "player", "player_id"),
                    ("player_b", "player", "player_id"),
                    ("player_unknown", "player", "player_id"),
                ]

                for key, entity_type, column in mappings:
                    # Os nomes de coluna são definidos na lista acima.
                    connection.execute(
                        text(f"""
                            INSERT INTO core.external_id_mappings (
                                id, provider, entity_type,
                                external_id, {column}
                            )
                            VALUES (
                                :id, 'statsbomb', :entity_type,
                                :external_id, :internal_id
                            )
                        """),
                        {
                            "id": uuid4(),
                            "entity_type": entity_type,
                            "external_id": external[key],
                            "internal_id": ids[key],
                        },
                    )

                def store_source(source: str, revision: int = 1):
                    payload_id = ids[f"{source}_payload"] if revision == 1 else uuid4()
                    body = json.dumps(
                        {
                            "fixture": source,
                            "revision": revision,
                        }
                    )

                    connection.execute(
                        text("""
                            INSERT INTO raw.provider_payloads (
                                id, provider, endpoint, payload,
                                raw_body, sha256, ingestion_run_id
                            )
                            VALUES (
                                :id, 'statsbomb', :endpoint,
                                CAST(:payload AS jsonb),
                                :body, :sha256, :run
                            )
                        """),
                        {
                            "id": payload_id,
                            "endpoint": (
                                f"https://example.test/data/{source}/{external['match']}.json"
                            ),
                            "payload": body,
                            "body": body,
                            "sha256": hashlib.sha256(body.encode()).hexdigest(),
                            "run": ids["run"],
                        },
                    )

                    return payload_id

                store_source("lineups")
                store_source("events")

                def result(
                    player_key: str,
                    team_key: str,
                    bounds: list[tuple[int, int]],
                ) -> PlayerParticipation:
                    return PlayerParticipation(
                        external_match_id=external["match"],
                        external_team_id=external[team_key],
                        external_player_id=external[player_key],
                        is_starter=None,
                        intervals=[
                            ParticipationInterval(
                                start_ms=start,
                                end_ms=end,
                            )
                            for start, end in bounds
                        ],
                        elapsed_on_field_ms=(
                            sum(end - start for start, end in bounds) if bounds else None
                        ),
                    )

                players = [
                    result(
                        "player_a",
                        "home",
                        [(0, 40_000), (45_000, 100_000)],
                    ),
                    result("player_b", "away", [(0, 100_000)]),
                    result("player_unknown", "home", []),
                ]

                calculation_id, created = get_or_create_calculation(
                    connection,
                    ids["match"],
                    ids["lineups_payload"],
                    ids["events_payload"],
                    ids["run"],
                )

                assert created is True

                assert persist_participation_results(
                    connection,
                    calculation_id,
                    players,
                    match_duration_ms=100_000,
                ) == (3, 3)

                reused_id, created = get_or_create_calculation(
                    connection,
                    ids["match"],
                    ids["lineups_payload"],
                    ids["events_payload"],
                    ids["run"],
                )

                assert created is False
                assert reused_id == calculation_id

                saved = connection.execute(
                    text("""
                        SELECT has_participation_evidence,
                               elapsed_on_field_ms
                        FROM analytics.player_match_participation
                        WHERE calculation_id = :calculation_id
                          AND lineup_id = :lineup_id
                    """),
                    {
                        "calculation_id": calculation_id,
                        "lineup_id": ids["lineup_unknown"],
                    },
                ).one()

                assert saved.has_participation_evidence is False
                assert saved.elapsed_on_field_ms is None

                with patch(
                    "football_analytics.modules.matches.participation_router.get_engine"
                ) as mocked_engine:
                    mocked_engine.return_value.connect.side_effect = lambda: nullcontext(connection)

                    with TestClient(app) as client:
                        path = f"/api/v1/matches/{ids['match']}/participation"

                        response = client.get(
                            path,
                            params={"calculation_id": str(calculation_id)},
                        )

                        assert response.status_code == 200

                        body = response.json()

                        assert body["total"] == 3
                        assert body["calculation"]["id"] == str(calculation_id)
                        assert body["calculation"]["match_id"] == str(ids["match"])
                        assert body["calculation"]["metric_name"] == ("elapsed_on_field_ms")
                        assert body["calculation"]["formula_version"] == ("1.0.0")

                        by_player = {item["player_id"]: item for item in body["items"]}

                        player_a = by_player[str(ids["player_a"])]

                        assert player_a["player_name"] == "Player A"
                        assert player_a["team_name"] == "Home"
                        assert player_a["elapsed_on_field_ms"] == 95_000
                        assert player_a["elapsed_on_field_display"] == ("1 min 35,000 s")
                        assert [
                            interval["interval_number"] for interval in player_a["intervals"]
                        ] == [1, 2]
                        assert [interval["duration_ms"] for interval in player_a["intervals"]] == [
                            40_000,
                            55_000,
                        ]

                        unknown = by_player[str(ids["player_unknown"])]

                        assert unknown["has_participation_evidence"] is False
                        assert unknown["elapsed_on_field_ms"] is None
                        assert unknown["elapsed_on_field_display"] is None
                        assert unknown["intervals"] == []

                        missing = client.get(
                            path,
                            params={"calculation_id": str(uuid4())},
                        )
                        assert missing.status_code == 404

                        wrong_match = client.get(
                            f"/api/v1/matches/{uuid4()}/participation",
                            params={"calculation_id": str(calculation_id)},
                        )
                        assert wrong_match.status_code == 404

                        invalid_id = client.get(
                            path,
                            params={"calculation_id": "invalid"},
                        )
                        assert invalid_id.status_code == 422

                        missing_parameter = client.get(path)
                        assert missing_parameter.status_code == 422

                with pytest.raises(ValueError, match="already has participation results"):
                    persist_participation_results(
                        connection,
                        calculation_id,
                        players,
                        match_duration_ms=100_000,
                    )

                with pytest.raises(ValueError, match="expected match"):
                    get_or_create_calculation(
                        connection,
                        ids["match"],
                        ids["lineups_payload"],
                        ids["lineups_payload"],
                        ids["run"],
                    )

                # Uma fonte diferente cria outro cálculo.
                # Uma falha deve desfazer esse novo conjunto.
                with pytest.raises(ValueError, match="Duration differs"):
                    with connection.begin_nested():
                        changed_events = store_source("events", revision=2)

                        new_id, created = get_or_create_calculation(
                            connection,
                            ids["match"],
                            ids["lineups_payload"],
                            changed_events,
                            ids["run"],
                        )

                        assert created is True
                        assert new_id != calculation_id

                        invalid_players = [
                            players[0].model_copy(update={"elapsed_on_field_ms": 1}),
                            *players[1:],
                        ]

                        persist_participation_results(
                            connection,
                            new_id,
                            invalid_players,
                            match_duration_ms=100_000,
                        )

                calculation_count = connection.execute(
                    text("""
                        SELECT count(*)
                        FROM analytics.participation_calculations
                        WHERE match_id = :match_id
                    """),
                    {"match_id": ids["match"]},
                ).scalar_one()

                assert calculation_count == 1

            finally:
                transaction.rollback()

    finally:
        engine.dispose()
