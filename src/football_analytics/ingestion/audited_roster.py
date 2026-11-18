"""Import roster and participation from audited files without network access."""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, create_engine, text

from football_analytics.ingestion.audited_event_source import (
    AuditedEventSource,
    load_audited_event_source,
)
from football_analytics.ingestion.lineups import persist_lineups, resolve_lineup_id
from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
    reconstruct_match_participation,
)
from football_analytics.ingestion.participation_storage import (
    get_or_create_calculation,
    persist_participation_results,
)
from football_analytics.ingestion.players import persist_players
from football_analytics.ingestion.team_occupancy import build_team_occupancy
from football_analytics.integrations.base import Player
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.players import normalize_players
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi


def load_audited_lineup_source(audit_dir: Path, *, match_id: int) -> AuditedEventSource:
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("Match ID must be a positive integer")
    manifest = json.loads((audit_dir / "downloads.json").read_text(encoding="utf-8"))
    relative = f"lineups/{match_id}.json"
    entry = manifest.get("files", {}).get(relative)
    if not isinstance(entry, dict) or entry.get("status") != 200:
        raise ValueError("Audited lineup source is unavailable")
    endpoint = entry.get("url")
    if not isinstance(endpoint, str) or not endpoint.endswith(f"/{relative}"):
        raise ValueError("Audited lineup endpoint differs from requested match")
    body = (audit_dir / "sources" / relative).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digest != entry.get("sha256"):
        raise ValueError("Audited lineup source hash differs")
    return AuditedEventSource(match_id, body, endpoint, digest)


def _store_source(connection: Connection, source: AuditedEventSource, run_id: UUID) -> UUID:
    existing = connection.execute(
        text("""
            SELECT id, raw_body FROM raw.provider_payloads
            WHERE provider = 'statsbomb' AND endpoint = :endpoint AND sha256 = :sha
            ORDER BY id LIMIT 1
        """),
        {"endpoint": source.endpoint, "sha": source.sha256},
    ).one_or_none()
    if existing is not None:
        if hashlib.sha256(existing.raw_body.encode("utf-8")).hexdigest() != source.sha256:
            raise ValueError("Stored source body differs from its hash")
        return UUID(str(existing.id))
    payload_id = uuid4()
    connection.execute(
        text("""
            INSERT INTO raw.provider_payloads
                (id, provider, endpoint, payload, raw_body, sha256, ingestion_run_id)
            VALUES (:id, 'statsbomb', :endpoint, CAST(:payload AS jsonb), :body, :sha, :run)
        """),
        {
            "id": payload_id,
            "endpoint": source.endpoint,
            "payload": json.dumps(json.loads(source.body)),
            "body": source.body.decode("utf-8"),
            "sha": source.sha256,
            "run": run_id,
        },
    )
    return payload_id


def verify_stored_participation(
    connection: Connection, calculation_id: UUID, players: list[PlayerParticipation]
) -> None:
    """Compare every stored result and interval, including players without evidence."""
    rows = connection.execute(
        text("""
            SELECT pm.external_id AS player, tm.external_id AS team,
                   p.is_starter, p.has_participation_evidence, p.elapsed_on_field_ms,
                   i.interval_number, i.start_ms, i.end_ms
            FROM analytics.player_match_participation p
            JOIN core.match_lineups l ON l.id = p.lineup_id
            JOIN core.external_id_mappings pm ON pm.player_id = l.player_id
                AND pm.provider = 'statsbomb' AND pm.entity_type = 'player'
            JOIN core.external_id_mappings tm ON tm.team_id = l.team_id
                AND tm.provider = 'statsbomb' AND tm.entity_type = 'team'
            LEFT JOIN analytics.player_participation_intervals i ON i.participation_id = p.id
            WHERE p.calculation_id = :id
        """),
        {"id": calculation_id},
    ).all()
    expected: list[tuple[object, ...]] = []
    for player in players:
        base = (
            player.external_player_id,
            player.external_team_id,
            player.is_starter,
            bool(player.intervals),
            player.elapsed_on_field_ms,
        )
        intervals = sorted(player.intervals, key=lambda interval: interval.start_ms)
        if not intervals:
            expected.append((*base, None, None, None))
        for number, interval in enumerate(intervals, 1):
            expected.append((*base, number, interval.start_ms, interval.end_ms))
    if sorted(map(tuple, rows), key=repr) != sorted(expected, key=repr):
        raise ValueError("Stored participation differs from reconstructed results")


def reconcile_roster_players(
    connection: Connection, players: list[Player]
) -> tuple[int, list[dict[str, object]]]:
    """Preserve existing metadata and expose source variants for the same provider ID.

    This does not resolve which spelling or nationality is correct. The caller
    must retain source provenance and mark differing metadata for review.
    """
    variants: list[dict[str, object]] = []
    missing = []
    fields = ("name", "nickname", "nationality", "birth_date")
    for player in players:
        existing = (
            connection.execute(
                text("""
            SELECT p.name, p.nickname, p.nationality, p.birth_date
            FROM core.players p JOIN core.external_id_mappings m ON m.player_id = p.id
            WHERE m.provider = :provider AND m.entity_type = 'player'
                AND m.external_id = :id
        """),
                {"provider": player.provider, "id": player.external_player_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            missing.append(player)
            continue
        differences = {
            field: {"stored": existing[field], "incoming": getattr(player, field)}
            for field in fields
            if existing[field] != getattr(player, field)
        }
        if differences:
            variants.append(
                {
                    "external_player_id": player.external_player_id,
                    "differences": differences,
                    "policy": "preserve_stored_metadata_for_same_provider_id",
                }
            )
    return persist_players(connection, missing), variants


def import_audited_roster(
    engine: Engine, *, audit_dir: Path, match_id: int, apply: bool = False
) -> dict[str, object]:
    events_source = load_audited_event_source(audit_dir, match_id=match_id)
    lineup_source = load_audited_lineup_source(audit_dir, match_id=match_id)
    events = json.loads(events_source.body)
    lineup_payload = json.loads(lineup_source.body)
    players = normalize_players(lineup_payload)
    lineups = normalize_lineups(lineup_payload, match_id, starting_xi=normalize_starting_xi(events))
    participation = reconstruct_match_participation(lineup_payload, events, match_id)
    duration = normalize_period_clock(events)[-1].end_elapsed_ms
    build_team_occupancy(participation, duration)
    if not players or {p.external_player_id for p in players} != {
        p.external_player_id for p in participation
    }:
        raise ValueError("Player catalogue differs from participation roster")
    report: dict[str, object] = {
        "match_id": match_id,
        "status": "prepared",
        "events_sha256": events_source.sha256,
        "lineups_sha256": lineup_source.sha256,
        "listed_players": len(players),
        "intervals": sum(len(p.intervals) for p in participation),
        "players_with_evidence": sum(bool(p.intervals) for p in participation),
        "players_without_evidence": sum(not p.intervals for p in participation),
        "elapsed_match_ms": duration,
    }
    if not apply:
        return report
    run_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO raw.ingestion_runs (id, provider, pipeline_name, status)
            VALUES (:id, 'statsbomb', 'audited_roster_participation', 'running')
        """),
            {"id": run_id},
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:id)"), {"id": CORE_INGESTION_LOCK_ID}
            )
            internal_match = resolve_lineup_id(connection, "statsbomb", "match", str(match_id))
            created_players, variants = reconcile_roster_players(connection, players)
            created_lineups = persist_lineups(connection, lineups)
            events_id = _store_source(connection, events_source, run_id)
            lineups_id = _store_source(connection, lineup_source, run_id)
            calculation_id, created = get_or_create_calculation(
                connection, internal_match, lineups_id, events_id, run_id
            )
            if created:
                persist_participation_results(connection, calculation_id, participation, duration)
            verify_stored_participation(connection, calculation_id, participation)
            connection.execute(
                text("""
                UPDATE raw.ingestion_runs SET status = 'success', finished_at = now(),
                    records_processed = :count WHERE id = :id
            """),
                {"count": len(participation), "id": run_id},
            )
        report.update(
            status="success",
            run_id=str(run_id),
            calculation_id=str(calculation_id),
            calculation_created=created,
            players_created=created_players,
            lineups_created=created_lineups,
            events_payload_id=str(events_id),
            lineups_payload_id=str(lineups_id),
            stored_results_verified=True,
            metadata_status="review" if variants else "consistent",
            player_metadata_variants=variants,
        )
    except Exception as exc:
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE raw.ingestion_runs SET status = 'failed', finished_at = now(),
                    records_failed = 1 WHERE id = :id
            """),
                {"id": run_id},
            )
            connection.execute(
                text("""
                INSERT INTO raw.ingestion_errors (id, ingestion_run_id, message)
                VALUES (:id, :run, :message)
            """),
                {
                    "id": uuid4(),
                    "run": run_id,
                    "message": f"match_id={match_id}; {type(exc).__name__}: {exc}",
                },
            )
        raise
    return report


def run_roster_batch(
    engine: Engine, *, audit_dir: Path, match_ids: list[int], apply: bool = False
) -> dict[str, object]:
    if not match_ids or any(type(mid) is not int or mid <= 0 for mid in match_ids):
        raise ValueError("Match IDs must be positive integers")
    if len(match_ids) != len(set(match_ids)):
        raise ValueError("Duplicate match IDs")
    items = []
    for mid in match_ids:
        try:
            items.append(
                import_audited_roster(engine, audit_dir=audit_dir, match_id=mid, apply=apply)
            )
        except Exception as exc:
            items.append(
                {"match_id": mid, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )
    return {
        "scope": "roster_and_participation",
        "apply": apply,
        "requested": len(match_ids),
        "success": sum(i["status"] == "success" for i in items),
        "prepared": sum(i["status"] == "prepared" for i in items),
        "failed": sum(i["status"] == "failed" for i in items),
        "metadata_review_matches": sum(i.get("metadata_status") == "review" for i in items),
        "policy_version": "1.0.0",
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--match-ids", type=int, nargs="+")
    selection.add_argument("--all-audited", action="store_true")
    parser.add_argument(
        "--apply", action="store_true", help="Persist; default only validates files"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    match_ids = args.match_ids
    if args.all_audited:
        manifest = json.loads((args.audit_dir / "downloads.json").read_text(encoding="utf-8"))
        match_ids = sorted(
            int(Path(name).stem)
            for name, entry in manifest["files"].items()
            if name.startswith("events/") and entry.get("status") == 200
        )
    engine = create_engine(args.database_url)
    try:
        report = run_roster_batch(
            engine, audit_dir=args.audit_dir, match_ids=match_ids, apply=args.apply
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "items"}, indent=2))
        if report["failed"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
