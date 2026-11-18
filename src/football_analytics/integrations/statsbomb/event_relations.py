from pydantic import Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    MatchEventRelations,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombEventWithRelations(StatsBombEvent):
    related_ids: object = Field(default=None, alias="related_events")


def normalize_event_relations(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEventRelations]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithRelations]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid event relation data") from exc

    result: list[MatchEventRelations] = []

    for event in sorted(source_events, key=lambda item: item.index):
        try:
            related_ids: tuple[str, ...] | None = None

            if "related_ids" in event.model_fields_set:
                if event.related_ids is None:
                    raise ValueError("Explicit null related_events requires review")

                parsed = TypeAdapter(list[str]).validate_python(
                    event.related_ids,
                    strict=True,
                )
                related_ids = tuple(parsed)

            result.append(
                MatchEventRelations.model_validate(
                    headers_by_id[event.id].model_dump() | {"related_event_ids": related_ids}
                )
            )

        except ValueError as exc:
            raise ProviderUnavailable(f"Invalid relations for StatsBomb event {event.id}") from exc

    return result
