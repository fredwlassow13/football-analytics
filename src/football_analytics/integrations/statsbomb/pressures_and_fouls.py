from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from football_analytics.integrations.base import (
    MatchDribbledPast,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchPressure,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)

type PressureOrFoul = MatchPressure | MatchDribbledPast | MatchFoulCommitted | MatchFoulWon


class StatsBombNamedAttribute(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(min_length=1)


class StatsBombFoulCard(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Yellow Card", "Second Yellow", "Red Card"]


class StatsBombFoulDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    advantage: bool | None = None
    penalty: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_null_fields(self) -> "StatsBombFoulDetails":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"Explicit null {name} requires review")
        return self


class StatsBombFoulCommittedDetails(StatsBombFoulDetails):
    offensive: bool | None = None
    foul_type: StatsBombNamedAttribute | None = Field(default=None, alias="type")
    card: StatsBombFoulCard | None = None


class StatsBombFoulWonDetails(StatsBombFoulDetails):
    defensive: bool | None = None


class StatsBombEventWithPressureOrFoul(StatsBombEvent):
    counterpress: object = None
    duration: object = None
    related_events: object = None
    foul_committed_details: object = Field(default_factory=dict, alias="foul_committed")
    foul_won_details: object = Field(default_factory=dict, alias="foul_won")


def normalize_pressures_and_fouls(
    payload: object,
    *,
    match_id: int,
) -> list[PressureOrFoul]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        events = TypeAdapter(list[StatsBombEventWithPressureOrFoul]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid pressure or foul event data") from exc

    models: dict[
        str,
        type[MatchPressure]
        | type[MatchDribbledPast]
        | type[MatchFoulCommitted]
        | type[MatchFoulWon],
    ] = {
        "Pressure": MatchPressure,
        "Dribbled Past": MatchDribbledPast,
        "Foul Committed": MatchFoulCommitted,
        "Foul Won": MatchFoulWon,
    }

    result: list[PressureOrFoul] = []

    for event in sorted(events, key=lambda item: item.index):
        event_type = event.type.name
        if event_type not in models:
            continue

        try:
            attributes: dict[str, object] = {}

            if "related_events" in event.model_fields_set:
                related = TypeAdapter(list[str]).validate_python(event.related_events, strict=True)
                if any(not event_id for event_id in related):
                    raise ValueError("Related event IDs must be nonempty")
                attributes["related_event_ids"] = tuple(related)

            if event_type != "Foul Won":
                if "counterpress" in event.model_fields_set and event.counterpress is None:
                    raise ValueError("Explicit null counterpress requires review")
                attributes["counterpress"] = event.counterpress

            if event_type == "Pressure":
                if "duration" in event.model_fields_set and event.duration is None:
                    raise ValueError("Explicit null duration requires review")
                attributes["duration_seconds"] = event.duration

            elif event_type == "Foul Committed":
                committed = StatsBombFoulCommittedDetails.model_validate(
                    event.foul_committed_details
                )
                attributes.update(committed.model_dump(exclude={"foul_type", "card"}))
                attributes["foul_type"] = (
                    committed.foul_type.name if committed.foul_type is not None else None
                )
                attributes["card"] = (
                    committed.card.name.lower().replace(" ", "_")
                    if committed.card is not None
                    else None
                )

            elif event_type == "Foul Won":
                won = StatsBombFoulWonDetails.model_validate(event.foul_won_details)
                attributes.update(won.model_dump())

            result.append(
                models[event_type].model_validate(headers_by_id[event.id].model_dump() | attributes)
            )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid pressure or foul details for StatsBomb event {event.id}"
            ) from exc

    return result
