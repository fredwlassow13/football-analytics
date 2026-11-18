import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.audited_roster import (
    import_audited_roster,
    load_audited_lineup_source,
    run_roster_batch,
    verify_stored_participation,
)
from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.participation import ParticipationInterval


def sources(root: Path, mid=123):
    lineups = []
    events = []
    for team in (1, 2):
        ids = [team * 100 + n for n in range(1, 12)]
        lineups.append(
            {
                "team_id": team,
                "lineup": [
                    {"player_id": p, "player_name": f"Player {p}", "jersey_number": n}
                    for n, p in enumerate(ids, 1)
                ],
            }
        )
        events.append(
            {
                "id": f"start-{team}",
                "index": team,
                "period": 1,
                "timestamp": "00:00:00.000",
                "type": {"name": "Starting XI"},
                "team": {"id": team},
                "tactics": {"lineup": [{"player": {"id": p}} for p in ids]},
            }
        )
    events.extend(
        [
            {
                "id": "end1",
                "index": 3,
                "period": 1,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
            {
                "id": "end2",
                "index": 4,
                "period": 2,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
        ]
    )
    manifest = {"files": {}}
    for kind, payload in (("events", events), ("lineups", lineups)):
        relative = f"{kind}/{mid}.json"
        body = json.dumps(payload).encode()
        path = root / "sources" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        manifest["files"][relative] = {
            "status": 200,
            "url": f"https://test.invalid/data/{relative}",
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    (root / "downloads.json").write_text(json.dumps(manifest))


def test_preflight_without_database_or_network(tmp_path):
    sources(tmp_path)
    engine = Mock()
    report = import_audited_roster(engine, audit_dir=tmp_path, match_id=123)
    assert report["status"] == "prepared"
    assert report["listed_players"] == 22
    assert report["players_with_evidence"] == 22
    assert report["intervals"] == 22
    assert report["elapsed_match_ms"] == 5_400_000
    engine.begin.assert_not_called()


@pytest.mark.parametrize(
    "change, message",
    [
        ("body", "hash differs"),
        ("endpoint", "endpoint differs"),
        ("status", "unavailable"),
    ],
)
def test_rejects_untrusted_lineups(tmp_path, change, message):
    sources(tmp_path)
    manifest = json.loads((tmp_path / "downloads.json").read_text())
    entry = manifest["files"]["lineups/123.json"]
    if change == "body":
        (tmp_path / "sources/lineups/123.json").write_text("[]")
    elif change == "endpoint":
        entry["url"] = "https://test.invalid/data/lineups/124.json"
    else:
        entry["status"] = 404
    (tmp_path / "downloads.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        load_audited_lineup_source(tmp_path, match_id=123)


@pytest.mark.parametrize("ids", [[], [1, 1], [0], [True]])
def test_invalid_selection(ids, tmp_path):
    with pytest.raises(ValueError):
        run_roster_batch(Mock(), audit_dir=tmp_path, match_ids=ids)


def test_batch_continues_after_failure(tmp_path):
    with patch(
        "football_analytics.ingestion.audited_roster.import_audited_roster",
        side_effect=[ValueError("bad roster"), {"match_id": 2, "status": "success"}],
    ):
        result = run_roster_batch(Mock(), audit_dir=tmp_path, match_ids=[1, 2], apply=True)
    assert result["failed"] == 1
    assert result["success"] == 1


def test_same_counts_but_different_interval_rejected():
    player = PlayerParticipation(
        external_match_id="123",
        external_team_id="1",
        external_player_id="101",
        is_starter=True,
        intervals=[ParticipationInterval(start_ms=0, end_ms=100)],
        elapsed_on_field_ms=100,
    )
    connection = Mock()
    connection.execute.return_value.all.return_value = [("101", "1", True, True, 100, 1, 1, 101)]
    with pytest.raises(ValueError, match="differs from reconstructed"):
        verify_stored_participation(connection, uuid4(), [player])
    connection.execute.return_value.all.return_value = [("101", "1", True, True, 100, 1, 0, 100)]
    verify_stored_participation(connection, uuid4(), [player])


def test_postgres_atomicity_and_reexecution(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")
    engine = create_engine(url)
    mid = uuid4().int
    sources(tmp_path, mid)
    # All writes, including the job's separate transactions, stay inside this rollback.
    with engine.connect() as connection:
        transaction = connection.begin()
        bound = Mock()

        @contextmanager
        def begin():
            with connection.begin_nested():
                yield connection

        bound.begin = begin
        ids = {key: uuid4() for key in ("competition", "season", "home", "away", "match")}
        try:
            for sql in [
                "INSERT INTO core.competitions (id,name,country_name,gender) "
                "VALUES (:competition,'Roster test','Test','male')",
                "INSERT INTO core.seasons (id,competition_id,name) "
                "VALUES (:season,:competition,'T')",
                "INSERT INTO core.teams (id,name) VALUES (:home,'H'),(:away,'A')",
                "INSERT INTO core.matches (id,season_id,home_team_id,away_team_id,match_date) "
                "VALUES (:match,:season,:home,:away,DATE '2022-01-01')",
            ]:
                connection.execute(text(sql), ids)
            for key, kind, external in (
                ("home", "team", "1"),
                ("away", "team", "2"),
                ("match", "match", str(mid)),
            ):
                connection.execute(
                    text(f"""
                    INSERT INTO core.external_id_mappings
                        (id,provider,entity_type,external_id,{kind}_id)
                    VALUES (:id,'statsbomb',:kind,:external,:internal)
                """),
                    {"id": uuid4(), "kind": kind, "external": external, "internal": ids[key]},
                )
            # A failure after core/source writes rolls back the entire match.
            before = connection.execute(
                text("SELECT count(*) FROM raw.provider_payloads")
            ).scalar_one()
            with patch(
                "football_analytics.ingestion.audited_roster.get_or_create_calculation",
                side_effect=ValueError("forced failure"),
            ):
                with pytest.raises(ValueError, match="forced failure"):
                    import_audited_roster(bound, audit_dir=tmp_path, match_id=mid, apply=True)
            assert (
                connection.execute(text("SELECT count(*) FROM raw.provider_payloads")).scalar_one()
                == before
            )
            first = import_audited_roster(bound, audit_dir=tmp_path, match_id=mid, apply=True)
            second = import_audited_roster(bound, audit_dir=tmp_path, match_id=mid, apply=True)
            assert first["calculation_created"] is True
            assert first["players_created"] == first["lineups_created"] == 22
            assert second["calculation_created"] is False
            assert second["players_created"] == second["lineups_created"] == 0
            assert first["calculation_id"] == second["calculation_id"]
            assert (
                connection.execute(
                    text("SELECT count(*) FROM raw.provider_payloads WHERE id IN (:e,:l)"),
                    {"e": first["events_payload_id"], "l": first["lineups_payload_id"]},
                ).scalar_one()
                == 2
            )
            connection.execute(
                text(
                    "UPDATE analytics.player_participation_intervals "
                    "SET end_ms=end_ms-1 WHERE participation_id IN "
                    "(SELECT id FROM analytics.player_match_participation "
                    "WHERE calculation_id=:id)"
                ),
                {"id": first["calculation_id"]},
            )
            with pytest.raises(ValueError, match="differs from reconstructed"):
                import_audited_roster(bound, audit_dir=tmp_path, match_id=mid, apply=True)
        finally:
            transaction.rollback()
    engine.dispose()


def test_metadata_variant_preserves_existing_player():
    from football_analytics.ingestion.audited_roster import reconcile_roster_players
    from football_analytics.integrations.base import Player

    connection = Mock()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "name": "Original name",
        "nickname": None,
        "nationality": "Country A",
        "birth_date": None,
    }
    player = Player(
        provider="statsbomb", external_player_id="99", name="Source name", nationality="Country B"
    )
    with patch(
        "football_analytics.ingestion.audited_roster.persist_players", return_value=0
    ) as save:
        created, variants = reconcile_roster_players(connection, [player])
    assert created == 0
    save.assert_called_once_with(connection, [])
    assert variants[0]["external_player_id"] == "99"
    assert variants[0]["differences"] == {
        "name": {"stored": "Original name", "incoming": "Source name"},
        "nationality": {"stored": "Country A", "incoming": "Country B"},
    }
