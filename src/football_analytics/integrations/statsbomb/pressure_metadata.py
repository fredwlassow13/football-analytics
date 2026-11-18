from pydantic import TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    MatchEventPressureMetadata,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombEventWithPressureMetadata(StatsBombEvent):
    under_pressure: object = None
    counterpress: object = None


def normalize_event_pressure_metadata(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEventPressureMetadata]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {header.external_event_id: header for header in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithPressureMetadata]).validate_python(
            payload
        )
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid pressure metadata") from exc

    result: list[MatchEventPressureMetadata] = []

    for source_event in sorted(source_events, key=lambda item: item.index):
        try:
            for field in ("under_pressure", "counterpress"):
                if field in source_event.model_fields_set and getattr(source_event, field) is None:
                    raise ValueError(f"Explicit null {field} requires review")

            result.append(
                MatchEventPressureMetadata.model_validate(
                    headers_by_id[source_event.id].model_dump()
                    | {
                        "under_pressure": source_event.under_pressure,
                        "counterpress": source_event.counterpress,
                    }
                )
            )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid pressure metadata for StatsBomb event {source_event.id}"
            ) from exc

    return result
