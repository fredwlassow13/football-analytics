"""Reusable read-only score verification, extracted from the existing CLI."""

from sqlalchemy import Connection, text

from football_analytics.ingestion.goal_score_evidence import (
    build_goal_score_evidence,
)
from football_analytics.ingestion.own_goal_evidence import (
    collect_own_goal_evidence,
)
from football_analytics.ingestion.score_reconciliation import (
    reconcile_match_score,
)
from football_analytics.ingestion.stored_match_sources import (
    load_latest_match_payload,
)
from football_analytics.integrations.statsbomb.event_relations import (
    normalize_event_relations,
)
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.matches import normalize_matches
from football_analytics.integrations.statsbomb.shots import normalize_shots

MATCH_SOURCE_SQL = """
SELECT p.id, p.sha256, item AS match_payload
FROM raw.provider_payloads p,
LATERAL jsonb_array_elements(p.payload) item
WHERE p.provider = 'statsbomb'
  AND p.endpoint LIKE '%/matches/%'
  AND item->>'match_id' = :match_id
ORDER BY p.fetched_at DESC, p.id DESC
LIMIT 1
"""

CORE_SCORE_SQL = """
SELECT
    home_mapping.external_id AS home_team,
    away_mapping.external_id AS away_team,
    m.home_score,
    m.away_score
FROM core.matches m
JOIN core.external_id_mappings match_mapping
    ON match_mapping.match_id = m.id
   AND match_mapping.provider = 'statsbomb'
   AND match_mapping.entity_type = 'match'
JOIN core.external_id_mappings home_mapping
    ON home_mapping.team_id = m.home_team_id
   AND home_mapping.provider = 'statsbomb'
   AND home_mapping.entity_type = 'team'
JOIN core.external_id_mappings away_mapping
    ON away_mapping.team_id = m.away_team_id
   AND away_mapping.provider = 'statsbomb'
   AND away_mapping.entity_type = 'team'
WHERE match_mapping.external_id = :match_id
"""

GOAL_EVENTS_SQL = """
SELECT
    item->>'id' AS event_id,
    item#>>'{type,name}' AS event_type,
    item#>>'{team,id}' AS team_id
FROM raw.provider_payloads p,
LATERAL jsonb_array_elements(p.payload) item
WHERE p.id = :payload_id
  AND (item->>'period')::int BETWEEN 1 AND 4
  AND (
      (
          item#>>'{type,name}' = 'Shot'
          AND item#>>'{shot,outcome,name}' = 'Goal'
      )
      OR item#>>'{type,name}' IN ('Own Goal For', 'Own Goal Against')
  )
ORDER BY (item->>'index')::int
"""


def verify_match_score(
    connection: Connection,
    *,
    match_id: int,
) -> dict[str, object]:
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("Match ID must be a positive integer")

    external_match_id = str(match_id)
    event_source = load_latest_match_payload(
        connection,
        match_id=match_id,
        kind="events",
    )

    match_source = (
        connection.execute(
            text(MATCH_SOURCE_SQL),
            {"match_id": external_match_id},
        )
        .mappings()
        .one()
    )

    core = (
        connection.execute(
            text(CORE_SCORE_SQL),
            {"match_id": external_match_id},
        )
        .mappings()
        .one()
    )

    match = normalize_matches([match_source["match_payload"]])[0]

    if (
        core["home_team"],
        core["away_team"],
        core["home_score"],
        core["away_score"],
    ) != (
        match.external_home_team_id,
        match.external_away_team_id,
        match.home_score,
        match.away_score,
    ):
        raise ValueError("Core match differs from stored match payload")

    team_ids = (
        match.external_home_team_id,
        match.external_away_team_id,
    )
    payload = event_source["payload"]

    events = normalize_events(payload, match_id=match_id)
    shots = normalize_shots(payload, match_id=match_id)
    relations = normalize_event_relations(
        payload,
        match_id=match_id,
    )

    evidence = build_goal_score_evidence(
        events,
        shots,
        relations,
        provider="statsbomb",
        match_id=external_match_id,
        team_ids=team_ids,
        events_sha256=event_source["sha256"],
    )

    own_goals = collect_own_goal_evidence(
        events,
        provider="statsbomb",
        match_id=external_match_id,
        team_ids=team_ids,
    )

    goal_shots = [shot for shot in shots if shot.period != 5 and shot.is_goal]

    source_index = {event.external_event_id: event.source_index for event in events}

    actual = sorted(
        [
            (
                shot.external_event_id,
                shot.event_type,
                shot.external_team_id,
            )
            for shot in goal_shots
        ]
        + [
            (
                own.external_event_id,
                own.event_type,
                own.external_team_id,
            )
            for own in own_goals
        ],
        key=lambda row: source_index[row[0]],
    )

    sql_rows = (
        connection.execute(
            text(GOAL_EVENTS_SQL),
            {"payload_id": event_source["id"]},
        )
        .mappings()
        .all()
    )

    expected = [
        (
            row["event_id"],
            row["event_type"],
            row["team_id"],
        )
        for row in sql_rows
    ]

    if actual != expected:
        raise ValueError("SQL goal evidence differs from normalized data")

    reconciliation = reconcile_match_score(evidence, match)

    summary = {
        "match_id": match_id,
        "events_payload_id": str(event_source["id"]),
        "events_sha256": event_source["sha256"],
        "matches_payload_id": str(match_source["id"]),
        "matches_sha256": match_source["sha256"],
        "sql_goal_evidence_verified": True,
        "core_score_verified": True,
        "status": reconciliation.status,
        "issues": list(reconciliation.issues),
        "shot_goals": len(evidence.shot_goal_event_ids),
        "own_goal_source_records": len(own_goals),
        "usable_own_goal_pairs": sum(
            pair.status == "usable" for pair in evidence.own_goal_links.pairs
        ),
        "unpaired_own_goal_event_ids": list(evidence.own_goal_links.unpaired_event_ids),
        "excluded_shootout_shots": len(evidence.excluded_shootout_shot_ids),
        "teams": [team.model_dump(mode="json") for team in reconciliation.teams],
    }

    report = summary | {"reconciliation": reconciliation.model_dump(mode="json")}
    return report
