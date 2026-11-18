from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from football_analytics.integrations.base import (
    MatchEventPlayMetadata,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombPlayPattern(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1)


class StatsBombEventWithPlayMetadata(StatsBombEvent):
    duration: object = None
    out_flag: object = Field(default=None, alias="out")
    play_pattern_details: object = Field(default=None, alias="play_pattern")


def normalize_event_play_metadata(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEventPlayMetadata]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithPlayMetadata]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid event play metadata") from exc

    result: list[MatchEventPlayMetadata] = []

    for event in sorted(source_events, key=lambda item: item.index):
        try:
            for field in ("duration", "out_flag", "play_pattern_details"):
                if field in event.model_fields_set and getattr(event, field) is None:
                    raise ValueError(f"Explicit null {field} requires review")

            pattern: StatsBombPlayPattern | None = None
            if event.play_pattern_details is not None:
                pattern = StatsBombPlayPattern.model_validate(event.play_pattern_details)

            result.append(
                MatchEventPlayMetadata.model_validate(
                    headers_by_id[event.id].model_dump()
                    | {
                        "duration_seconds": event.duration,
                        "out": event.out_flag,
                        "play_pattern": (
                            pattern.name.lower().replace(" ", "_") if pattern is not None else None
                        ),
                        "external_play_pattern_id": (
                            str(pattern.id) if pattern is not None else None
                        ),
                    }
                )
            )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid play metadata for StatsBomb event {event.id}"
            ) from exc

    return result
