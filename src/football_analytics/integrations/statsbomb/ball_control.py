from pydantic import Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    MatchBallControlIncident,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombEventWithBallControl(StatsBombEvent):
    under_pressure: object = None
    counterpress: object = None
    out_flag: object = Field(default=None, alias="out")
    related_events: object = None


def normalize_ball_control_incidents(
    payload: object,
    *,
    match_id: int,
) -> list[MatchBallControlIncident]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {header.external_event_id: header for header in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithBallControl]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid ball control event data") from exc

    result: list[MatchBallControlIncident] = []

    for source_event in sorted(source_events, key=lambda item: item.index):
        if source_event.type.name not in {"Dispossessed", "Miscontrol"}:
            continue

        try:
            for field in (
                "under_pressure",
                "counterpress",
                "out_flag",
                "related_events",
            ):
                if field in source_event.model_fields_set and getattr(source_event, field) is None:
                    raise ValueError(f"Explicit null {field} requires review")

            related_ids: tuple[str, ...] | None = None
            if "related_events" in source_event.model_fields_set:
                related = TypeAdapter(list[str]).validate_python(
                    source_event.related_events,
                    strict=True,
                )
                related_ids = tuple(related)

            header = headers_by_id[source_event.id]
            result.append(
                MatchBallControlIncident.model_validate(
                    header.model_dump()
                    | {
                        "under_pressure": source_event.under_pressure,
                        "counterpress": source_event.counterpress,
                        "out": source_event.out_flag,
                        "related_event_ids": related_ids,
                    }
                )
            )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid ball control details for StatsBomb event {source_event.id}"
            ) from exc

    return result
