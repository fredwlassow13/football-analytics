"""Read-only reconciliation of shootout participation from stored sources."""

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text

from football_analytics.ingestion.match_participation import (
    reconstruct_match_participation,
)
from football_analytics.ingestion.shootout_assessment import (
    build_shootout_assessment,
)
from football_analytics.ingestion.shootout_coverage import (
    apply_shootout_coverage_audit,
)
from football_analytics.ingestion.shootout_coverage_file import (
    load_shootout_coverage_review,
)
from football_analytics.ingestion.shootout_inputs import (
    prepare_shootout_inputs,
)
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.participation_events import (
    normalize_participation_events,
)
from football_analytics.integrations.statsbomb.period_clock import (
    normalize_period_clock,
)
from football_analytics.integrations.statsbomb.starting_xi import (
    normalize_starting_xi,
)

SOURCE_SQL = """
SELECT
    c.id AS calculation_id,
    c.events_payload_id,
    c.events_sha256,
    c.lineups_sha256,
    e.payload AS events,
    l.payload AS lineups
FROM analytics.participation_calculations c
JOIN raw.provider_payloads e ON e.id = c.events_payload_id
JOIN raw.provider_payloads l ON l.id = c.lineups_payload_id
JOIN core.external_id_mappings m ON m.match_id = c.match_id
WHERE m.provider = 'statsbomb'
  AND m.entity_type = 'match'
  AND m.external_id = :match_id
  AND c.metric_name = 'elapsed_on_field_ms'
  AND c.formula_version = '1.0.0'
  AND e.provider = 'statsbomb'
  AND l.provider = 'statsbomb'
  AND c.events_sha256 = e.sha256
  AND c.lineups_sha256 = l.sha256
ORDER BY c.created_at DESC, c.id DESC
LIMIT 1
"""

EVIDENCE_SQL = """
SELECT
    e->>'id' AS event_id,
    (e->>'index')::int AS source_index,
    e#>>'{team,id}' AS team_id,
    e#>>'{player,id}' AS player_id,
    CASE
        WHEN e#>>'{type,name}' = 'Shot' THEN 'shooter'
        ELSE 'goalkeeper'
    END AS role
FROM raw.provider_payloads p,
LATERAL jsonb_array_elements(p.payload) e
WHERE p.id = :payload_id
  AND (e->>'period')::int = 5
  AND e#>>'{type,name}' IN ('Shot', 'Goal Keeper')
ORDER BY (e->>'index')::int
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--coverage-audit",
        type=Path,
        help="Optional JSON containing a source-specific coverage review",
    )
    args = parser.parse_args()

    if args.match_id <= 0:
        parser.error("--match-id must be positive")

    engine = create_engine(args.database_url)

    try:
        with engine.connect() as connection:
            source = (
                connection.execute(
                    text(SOURCE_SQL),
                    {"match_id": str(args.match_id)},
                )
                .mappings()
                .one()
            )

            events_payload = source["events"]
            lineups_payload = source["lineups"]

            events = normalize_events(
                events_payload,
                match_id=args.match_id,
            )
            lineups = normalize_lineups(
                lineups_payload,
                args.match_id,
                starting_xi=normalize_starting_xi(events_payload),
            )
            players = reconstruct_match_participation(
                lineups_payload,
                events_payload,
                args.match_id,
            )

            prepared = prepare_shootout_inputs(
                players,
                normalize_participation_events(events_payload),
                normalize_period_clock(events_payload),
                match_id=str(args.match_id),
            )

            coverage_review = None

            if args.coverage_audit is not None:
                coverage_review = load_shootout_coverage_review(args.coverage_audit)

                prepared = apply_shootout_coverage_audit(
                    prepared,
                    coverage_review.audit,
                    events_sha256=source["events_sha256"],
                    lineups_sha256=source["lineups_sha256"],
                )

            result = build_shootout_assessment(
                events,
                lineups,
                [item.presence for item in prepared],
                {item.presence.external_player_id: item.eligibility_context for item in prepared},
                provider="statsbomb",
                match_id=str(args.match_id),
            )

            sql_rows = (
                connection.execute(
                    text(EVIDENCE_SQL),
                    {"payload_id": source["events_payload_id"]},
                )
                .mappings()
                .all()
            )

            evidence = sorted(
                [item for player in result for item in player.roster.evidence],
                key=lambda item: item.source_index,
            )

            actual = [
                (
                    item.external_event_id,
                    item.source_index,
                    item.external_team_id,
                    item.external_player_id,
                    item.role,
                )
                for item in evidence
            ]
            expected = [
                (
                    row["event_id"],
                    row["source_index"],
                    row["team_id"],
                    row["player_id"],
                    row["role"],
                )
                for row in sql_rows
            ]

            if actual != expected:
                raise ValueError("SQL shootout evidence mismatch")

            result_ids = [item.roster.lineup.external_player_id for item in result]
            lineup_ids = {item.external_player_id for item in lineups}

            if len(result_ids) != len(set(result_ids)) or set(result_ids) != lineup_ids:
                raise ValueError("Shootout roster population mismatch")

            observed_players = [
                player for player in result if player.roster.participation_status == "observed"
            ]

            observed_eligible = sum(
                player.eligibility.status == "eligible" for player in observed_players
            )

            observed_divergences = [
                {
                    "external_player_id": (player.roster.lineup.external_player_id),
                    "external_team_id": (player.roster.lineup.external_team_id),
                    "eligibility": player.eligibility.status,
                    "reason": player.eligibility.reason,
                    "issues": list(player.issues),
                    "evidence_event_ids": [
                        evidence.external_event_id for evidence in player.roster.evidence
                    ],
                }
                for player in observed_players
                if (player.eligibility.status != "eligible" or player.status != "consistent")
            ]

            summary = {
                "match_id": args.match_id,
                "calculation_id": str(source["calculation_id"]),
                "events_sha256": source["events_sha256"],
                "lineups_sha256": source["lineups_sha256"],
                "sql_evidence_verified": True,
                "listed_players": len(result),
                "evidence_records": len(evidence),
                "observed_roles": dict(Counter(item.role for item in evidence)),
                "participation": dict(Counter(item.roster.participation_status for item in result)),
                "match_end_presence": dict(
                    Counter(item.match_end_presence.status for item in result)
                ),
                "eligibility": dict(Counter(item.eligibility.status for item in result)),
                "eligibility_reasons": dict(Counter(item.eligibility.reason for item in result)),
                "assessment_status": dict(Counter(item.status for item in result)),
                "coverage_review": (
                    coverage_review.model_dump(mode="json") if coverage_review is not None else None
                ),
                "observed_participant_check": {
                    "observed_players": len(observed_players),
                    "eligible_players": observed_eligible,
                    "all_observed_eligible": all(
                        player.eligibility.status == "eligible" for player in observed_players
                    )
                    if observed_players
                    else None,
                    "divergences": observed_divergences,
                },
            }

            report = summary | {"players": [item.model_dump(mode="json") for item in result]}
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
