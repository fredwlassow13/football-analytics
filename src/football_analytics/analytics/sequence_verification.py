"""SQL source membership checks plus deterministic sequence replay."""

from collections import Counter
from typing import Any

from sqlalchemy import Connection, text

from football_analytics.analytics.structured_contracts import (
    AttackData,
    PossessionData,
    StructuredResult,
)
from football_analytics.analytics.structured_sequences import execute_sequences

SEQUENCE_SQL = """
SELECT e AS event
FROM raw.provider_payloads p,
     LATERAL jsonb_array_elements(p.payload) e
WHERE p.id=:payload_id
ORDER BY (e->>'index')::int
"""


def verify_sequence_result(
    connection: Connection,
    result: StructuredResult,
) -> tuple[int, set[str]]:
    """Check source membership independently; replay checks the existing policy."""
    if result.source is None or not isinstance(
        result.data,
        (PossessionData, AttackData),
    ):
        raise ValueError("Sequence verification requires source and sequence data")

    checks = 0

    def same(actual: object, expected: object, label: str) -> None:
        nonlocal checks
        if actual != expected:
            raise ValueError(f"Sequence mismatch: {label}")
        checks += 1

    rows = (
        connection.execute(
            text(SEQUENCE_SQL),
            {"payload_id": result.source.events_payload_id},
        )
        .mappings()
        .all()
    )

    payload = [row["event"] for row in rows]
    if not payload:
        raise ValueError("Sequence source is empty")

    ids = [event["id"] for event in payload]
    indexes = [event["index"] for event in payload]

    same(len(set(ids)), len(ids), "unique source IDs")
    same(len(set(indexes)), len(indexes), "unique source indexes")
    same(
        set(event["period"] for event in payload) - {5},
        set(result.source.available_periods),
        "playing periods",
    )

    playing = [event for event in payload if event["period"] != 5]
    shootout = [event["id"] for event in payload if event["period"] == 5]
    value = result.data.value

    same(
        value.external_event_ids,
        tuple(event["id"] for event in playing),
        "playing event IDs",
    )
    same(
        value.excluded_shootout_event_ids,
        tuple(shootout),
        "excluded shootout IDs",
    )

    groups: list[list[dict[str, Any]]] = []
    previous = None

    for event in playing:
        if (
            previous is None
            or event["period"] != previous["period"]
            or event.get("possession") != previous.get("possession")
            or event["index"] != previous["index"] + 1
        ):
            groups.append([])

        groups[-1].append(event)
        previous = event

    same(len(value.fragments), len(groups), "fragment count")

    for number, (fragment, events) in enumerate(
        zip(value.fragments, groups, strict=True),
        1,
    ):
        same(fragment.fragment_number, number, "fragment number")
        same(fragment.period, events[0]["period"], "fragment period")
        same(
            fragment.possession_sequence,
            events[0].get("possession"),
            "possession label",
        )
        same(
            fragment.external_event_ids,
            tuple(event["id"] for event in events),
            "fragment event IDs",
        )
        same(fragment.event_count, len(events), "fragment event count")
        same(
            (fragment.first_source_index, fragment.last_source_index),
            (events[0]["index"], events[-1]["index"]),
            "fragment source bounds",
        )
        same(
            fragment.event_type_counts,
            dict(Counter(event["type"]["name"] for event in events)),
            "fragment event types",
        )

    if isinstance(result.data, AttackData):
        attacks = result.data.value
        catalogue = {event["id"]: event for event in playing}
        assigned: list[str] = []

        for number, attack in enumerate(attacks.attacks, 1):
            same(attack.attack_number, number, "attack number")

            if not attack.external_event_ids:
                raise ValueError("Sequence mismatch: empty attack")

            if any(key not in catalogue for key in attack.external_event_ids):
                raise ValueError("Sequence mismatch: attack references outside playing source")

            events = [catalogue[key] for key in attack.external_event_ids]

            same(
                {event["period"] for event in events},
                {attack.period},
                "attack period",
            )
            same(
                [event["index"] for event in events],
                sorted(event["index"] for event in events),
                "attack source order",
            )
            same(
                attack.shot_event_ids,
                tuple(event["id"] for event in events if event["type"]["name"] == "Shot"),
                "attack shots",
            )

            assigned.extend(attack.external_event_ids)

        same(
            len(assigned),
            len(set(assigned)),
            "unique attack membership",
        )

        assigned_ids = set(assigned)

        same(
            attacks.unassigned_event_ids,
            tuple(event["id"] for event in playing if event["id"] not in assigned_ids),
            "unassigned event partition",
        )
        same(
            attacks.possession_duration_ms,
            None,
            "possession duration remains undefined",
        )
        same(
            attacks.possession_percentage,
            None,
            "possession percentage remains undefined",
        )

    replay = execute_sequences(
        result.query,
        result.source,
        payload,
    )

    same(result.data, replay.data, "complete deterministic replay")
    same(result.reasons, replay.reasons, "review reasons")
    same(result.status, replay.status, "review status")

    return checks, set(replay.reasons)
