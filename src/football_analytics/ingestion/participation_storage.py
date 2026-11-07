from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.team_occupancy import (
    build_team_occupancy,
)

PARTICIPATION_METRIC = "elapsed_on_field_ms"
PARTICIPATION_FORMULA_VERSION = "1.0.0"


def get_or_create_calculation(
    connection: Connection,
    match_id: UUID,
    lineups_payload_id: UUID,
    events_payload_id: UUID,
    ingestion_run_id: UUID,
) -> tuple[UUID, bool]:
    """Return calculation ID and whether it was created.

    The caller must manage the transaction and persist results
    before committing a newly created calculation.
    """
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CORE_INGESTION_LOCK_ID},
    )

    external_match_id: str | None = connection.execute(
        text("""
            SELECT external_id
            FROM core.external_id_mappings
            WHERE provider = 'statsbomb'
              AND entity_type = 'match'
              AND match_id = :match_id
        """),
        {"match_id": match_id},
    ).scalar_one_or_none()

    if external_match_id is None:
        raise ValueError("StatsBomb match mapping not found")

    source_hashes: dict[str, str] = {}

    for source, payload_id in (
        ("lineups", lineups_payload_id),
        ("events", events_payload_id),
    ):
        payload = (
            connection.execute(
                text("""
                SELECT provider, endpoint, sha256
                FROM raw.provider_payloads
                WHERE id = :id
            """),
                {"id": payload_id},
            )
            .mappings()
            .one_or_none()
        )

        if payload is None:
            raise ValueError(f"{source} payload not found")

        if payload["provider"] != "statsbomb":
            raise ValueError(f"Unexpected provider for {source} payload")

        expected_suffix = f"/{source}/{external_match_id}.json"
        source_path = urlsplit(payload["endpoint"]).path

        if not source_path.endswith(expected_suffix):
            raise ValueError(f"{source} payload does not belong to the expected match")

        source_hashes[source] = payload["sha256"]

    parameters = {
        "match_id": match_id,
        "metric_name": PARTICIPATION_METRIC,
        "formula_version": PARTICIPATION_FORMULA_VERSION,
        "lineups_sha256": source_hashes["lineups"],
        "events_sha256": source_hashes["events"],
    }

    existing_id: UUID | None = connection.execute(
        text("""
            SELECT id
            FROM analytics.participation_calculations
            WHERE match_id = :match_id
              AND metric_name = :metric_name
              AND formula_version = :formula_version
              AND lineups_sha256 = :lineups_sha256
              AND events_sha256 = :events_sha256
        """),
        parameters,
    ).scalar_one_or_none()

    if existing_id is not None:
        return existing_id, False

    calculation_id = uuid4()

    connection.execute(
        text("""
            INSERT INTO analytics.participation_calculations (
                id,
                match_id,
                metric_name,
                formula_version,
                lineups_payload_id,
                events_payload_id,
                lineups_sha256,
                events_sha256,
                ingestion_run_id
            )
            VALUES (
                :id,
                :match_id,
                :metric_name,
                :formula_version,
                :lineups_payload_id,
                :events_payload_id,
                :lineups_sha256,
                :events_sha256,
                :ingestion_run_id
            )
        """),
        {
            **parameters,
            "id": calculation_id,
            "lineups_payload_id": lineups_payload_id,
            "events_payload_id": events_payload_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )

    return calculation_id, True


def persist_participation_results(
    connection: Connection,
    calculation_id: UUID,
    players: list[PlayerParticipation],
    match_duration_ms: int,
) -> tuple[int, int]:
    """Persist a new calculation's results inside the caller's transaction."""
    calculation = connection.execute(
        text("""
            SELECT
                c.match_id,
                mapping.external_id AS external_match_id
            FROM analytics.participation_calculations AS c
            JOIN core.external_id_mappings AS mapping
                ON mapping.match_id = c.match_id
               AND mapping.provider = 'statsbomb'
               AND mapping.entity_type = 'match'
            WHERE c.id = :id
              AND c.metric_name = :metric_name
              AND c.formula_version = :formula_version
        """),
        {
            "id": calculation_id,
            "metric_name": PARTICIPATION_METRIC,
            "formula_version": PARTICIPATION_FORMULA_VERSION,
        },
    ).one_or_none()

    if calculation is None:
        raise ValueError("Compatible participation calculation not found")

    if any(player.external_match_id != calculation.external_match_id for player in players):
        raise ValueError("Participation results belong to another match")

    # Valida limites, sobreposições e contagem coletiva.
    build_team_occupancy(players, match_duration_ms)

    existing_count: int = connection.execute(
        text("""
            SELECT count(*)
            FROM analytics.player_match_participation
            WHERE calculation_id = :id
        """),
        {"id": calculation_id},
    ).scalar_one()

    if existing_count:
        raise ValueError("Calculation already has participation results")

    lineup_rows = (
        connection.execute(
            text("""
            SELECT
                l.id AS lineup_id,
                player_mapping.external_id AS external_player_id,
                team_mapping.external_id AS external_team_id
            FROM core.match_lineups AS l
            LEFT JOIN core.external_id_mappings AS player_mapping
                ON player_mapping.player_id = l.player_id
               AND player_mapping.provider = 'statsbomb'
               AND player_mapping.entity_type = 'player'
            LEFT JOIN core.external_id_mappings AS team_mapping
                ON team_mapping.team_id = l.team_id
               AND team_mapping.provider = 'statsbomb'
               AND team_mapping.entity_type = 'team'
            WHERE l.match_id = :match_id
        """),
            {"match_id": calculation.match_id},
        )
        .mappings()
        .all()
    )

    if any(
        row["external_player_id"] is None or row["external_team_id"] is None for row in lineup_rows
    ):
        raise ValueError("Stored lineup has missing external mappings")

    roster = {row["external_player_id"]: row for row in lineup_rows}

    if len(roster) != len(lineup_rows):
        raise ValueError("Ambiguous external mappings in stored lineup")

    incoming_ids = {player.external_player_id for player in players}

    if incoming_ids != set(roster):
        raise ValueError("Participation results differ from stored roster")

    result_rows: list[dict[str, object]] = []
    interval_rows: list[dict[str, object]] = []

    for player in players:
        lineup = roster[player.external_player_id]

        if player.external_team_id != lineup["external_team_id"]:
            raise ValueError("Participation team differs from stored lineup")

        expected_duration = (
            sum(interval.duration_ms for interval in player.intervals) if player.intervals else None
        )

        if player.elapsed_on_field_ms != expected_duration:
            raise ValueError("Duration differs from interval sum")

        if player.is_starter is True and not player.intervals:
            raise ValueError("Starter has no participation intervals")

        participation_id = uuid4()

        result_rows.append(
            {
                "id": participation_id,
                "calculation_id": calculation_id,
                "lineup_id": lineup["lineup_id"],
                "match_id": calculation.match_id,
                "is_starter": player.is_starter,
                "has_evidence": bool(player.intervals),
                "duration": player.elapsed_on_field_ms,
            }
        )

        ordered_intervals = sorted(
            player.intervals,
            key=lambda interval: interval.start_ms,
        )

        for number, interval in enumerate(ordered_intervals, start=1):
            interval_rows.append(
                {
                    "id": uuid4(),
                    "participation_id": participation_id,
                    "interval_number": number,
                    "start_ms": interval.start_ms,
                    "end_ms": interval.end_ms,
                }
            )

    connection.execute(
        text("""
            INSERT INTO analytics.player_match_participation (
                id, calculation_id, lineup_id, match_id,
                is_starter, has_participation_evidence,
                elapsed_on_field_ms
            )
            VALUES (
                :id, :calculation_id, :lineup_id, :match_id,
                :is_starter, :has_evidence, :duration
            )
        """),
        result_rows,
    )

    if interval_rows:
        connection.execute(
            text("""
                INSERT INTO analytics.player_participation_intervals (
                    id, participation_id, interval_number,
                    start_ms, end_ms
                )
                VALUES (
                    :id, :participation_id, :interval_number,
                    :start_ms, :end_ms
                )
            """),
            interval_rows,
        )

    return len(result_rows), len(interval_rows)
