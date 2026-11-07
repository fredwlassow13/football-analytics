import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.lineups import persist_lineups
from football_analytics.integrations.base import MatchLineup


@pytest.mark.parametrize("starter_status", [True, False])
def test_lineup_persistence_is_idempotent(
    starter_status: bool,
) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(database_url)

    competition_id = uuid4()
    season_id = uuid4()
    home_id = uuid4()
    away_id = uuid4()
    other_team_id = uuid4()
    player_id = uuid4()
    match_id = uuid4()
    external_prefix = str(uuid4())

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
                            :id, 'Test competition', 'International', 'male'
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
                        {"id": home_id, "name": "Home"},
                        {"id": away_id, "name": "Away"},
                        {"id": other_team_id, "name": "Other"},
                    ],
                )

                connection.execute(
                    text("""
                        INSERT INTO core.players (id, name)
                        VALUES (:id, 'Test player')
                    """),
                    {"id": player_id},
                )

                connection.execute(
                    text("""
                        INSERT INTO core.matches (
                            id, season_id, home_team_id,
                            away_team_id, match_date
                        )
                        VALUES (
                            :id, :season_id, :home_id,
                            :away_id, DATE '2022-12-18'
                        )
                    """),
                    {
                        "id": match_id,
                        "season_id": season_id,
                        "home_id": home_id,
                        "away_id": away_id,
                    },
                )

                # Os nomes de coluna abaixo são definidos pelo teste.
                mappings = [
                    ("match", "match", "match_id", match_id),
                    ("team", "home", "team_id", home_id),
                    ("team", "other", "team_id", other_team_id),
                    ("player", "player", "player_id", player_id),
                ]

                for entity_type, suffix, column, internal_id in mappings:
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
                            "external_id": f"{external_prefix}-{suffix}",
                            "internal_id": internal_id,
                        },
                    )

                item = MatchLineup(
                    provider="statsbomb",
                    external_match_id=f"{external_prefix}-match",
                    external_team_id=f"{external_prefix}-home",
                    external_player_id=f"{external_prefix}-player",
                    shirt_number=10,
                )

                assert persist_lineups(connection, [item]) == 1

                lookup = text("""
                    SELECT id
                    FROM core.match_lineups
                    WHERE match_id = :match_id
                      AND player_id = :player_id
                """)

                parameters = {
                    "match_id": match_id,
                    "player_id": player_id,
                }

                first_id = connection.execute(lookup, parameters).scalar_one()

                assert persist_lineups(connection, [item]) == 0

                second_id = connection.execute(lookup, parameters).scalar_one()

                assert first_id == second_id

                changed = item.model_copy(update={"shirt_number": 11})

                with pytest.raises(ValueError, match="Lineup data changed"):
                    persist_lineups(connection, [changed])

                wrong_team = item.model_copy(
                    update={"external_team_id": f"{external_prefix}-other"}
                )

                with pytest.raises(ValueError, match="does not belong"):
                    persist_lineups(connection, [wrong_team])

                missing_player = item.model_copy(update={"external_player_id": "missing-player"})

                with pytest.raises(ValueError, match="player mapping not found"):
                    persist_lineups(connection, [missing_player])

                saved = connection.execute(
                    text("""
                        SELECT team_id, shirt_number, is_starter
                        FROM core.match_lineups
                        WHERE id = :id
                    """),
                    {"id": first_id},
                ).one()

                assert saved.team_id == home_id
                assert saved.shirt_number == 10
                assert saved.is_starter is None
                enriched = item.model_copy(update={"is_starter": starter_status})

                # Enriquece o registro existente, sem criar outro.
                assert persist_lineups(connection, [enriched]) == 0

                status_query = text("""
                    SELECT is_starter
                    FROM core.match_lineups
                    WHERE id = :id
                """)

                saved_status = connection.execute(
                    status_query,
                    {"id": first_id},
                ).scalar_one()

                assert saved_status is starter_status

                # Repetir a classificação mantém o resultado.
                assert persist_lineups(connection, [enriched]) == 0

                # Uma carga antiga, sem classificação, não apaga o valor.
                assert persist_lineups(connection, [item]) == 0

                preserved_status = connection.execute(
                    status_query,
                    {"id": first_id},
                ).scalar_one()

                assert preserved_status is starter_status

                # Uma classificação oposta deve ser rejeitada.
                conflicting = item.model_copy(update={"is_starter": not starter_status})

                with pytest.raises(
                    ValueError,
                    match="Starter classification changed",
                ):
                    persist_lineups(connection, [conflicting])

                final_status = connection.execute(
                    status_query,
                    {"id": first_id},
                ).scalar_one()

                assert final_status is starter_status

            finally:
                transaction.rollback()

    finally:
        engine.dispose()
