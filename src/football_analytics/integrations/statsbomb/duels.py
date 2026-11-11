from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchDuel, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombDuelType(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Tackle", "Aerial Lost"]


class StatsBombDuelOutcome(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal[
        "Lost",
        "Won",
        "Lost In Play",
        "Lost Out",
        "Success",
        "Success In Play",
        "Success Out",
    ]


class StatsBombDuelDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    type: StatsBombDuelType
    outcome: StatsBombDuelOutcome | None = None


class StatsBombEventWithDuel(StatsBombEvent):
    duel_details: object = Field(default=None, alias="duel")


def normalize_duels(
    payload: object,
    *,
    match_id: int,
) -> list[MatchDuel]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithDuel]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid duel event data") from exc

    type_mapping = {
        "Tackle": "tackle",
        "Aerial Lost": "aerial_lost",
    }
    outcome_mapping = {
        "Lost": "lost",
        "Won": "won",
        "Lost In Play": "lost_in_play",
        "Lost Out": "lost_out",
        "Success": "success",
        "Success In Play": "success_in_play",
        "Success Out": "success_out",
    }

    result: list[MatchDuel] = []

    for event in sorted(source_events, key=lambda item: item.index):
        if event.type.name != "Duel":
            continue

        try:
            details = StatsBombDuelDetails.model_validate(event.duel_details)
            header = headers_by_id[event.id]

            normalized = MatchDuel.model_validate(
                header.model_dump()
                | {
                    "duel_type": type_mapping[details.type.name],
                    "outcome": (
                        outcome_mapping[details.outcome.name]
                        if details.outcome is not None
                        else None
                    ),
                }
            )
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid duel details for StatsBomb event {event.id}"
            ) from exc

        result.append(normalized)

    return result
