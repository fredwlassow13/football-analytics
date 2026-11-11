from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchPass, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)
from football_analytics.integrations.statsbomb.starting_xi import PlayerReference


class StatsBombPassOutcome(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Incomplete", "Out", "Pass Offside", "Unknown"]


class StatsBombPassType(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(min_length=1)


class StatsBombPassDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: StatsBombPassOutcome | None = None
    recipient: PlayerReference | None = None
    type: StatsBombPassType | None = None


class StatsBombEventWithPass(StatsBombEvent):
    pass_details: object = Field(default=None, alias="pass")


def normalize_passes(
    payload: object,
    *,
    match_id: int,
) -> list[MatchPass]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithPass]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid pass event data") from exc

    outcome_mapping = {
        "Incomplete": "incomplete",
        "Out": "out",
        "Pass Offside": "offside",
        "Unknown": "unknown",
    }

    result: list[MatchPass] = []

    for event in sorted(source_events, key=lambda item: item.index):
        if event.type.name != "Pass":
            continue

        try:
            details = StatsBombPassDetails.model_validate(event.pass_details)
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid pass details for StatsBomb event {event.id}"
            ) from exc

        if details.outcome is None:
            if "outcome" in details.model_fields_set:
                raise ProviderUnavailable(f"Null pass outcome for StatsBomb event {event.id}")

            outcome = "complete"
        else:
            outcome = outcome_mapping[details.outcome.name]

        header = headers_by_id[event.id]

        result.append(
            MatchPass.model_validate(
                header.model_dump()
                | {
                    "outcome": outcome,
                    "external_recipient_player_id": (
                        str(details.recipient.id) if details.recipient is not None else None
                    ),
                    "pass_type": (details.type.name if details.type is not None else None),
                }
            )
        )

    return result
