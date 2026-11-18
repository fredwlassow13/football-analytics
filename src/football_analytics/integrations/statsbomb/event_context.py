from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from football_analytics.integrations.base import MatchEventContext, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)
from football_analytics.integrations.statsbomb.starting_xi import TeamReference


class StatsBombEventPosition(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int = Field(ge=0)
    name: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_zero_position(self) -> "StatsBombEventPosition":
        if self.id == 0 and self.name != "Substitute":
            raise ValueError("Position ID zero is only supported for Substitute")

        return self


class StatsBombEventWithContext(StatsBombEvent):
    position: StatsBombEventPosition | None = None
    possession: int | None = Field(default=None, gt=0)
    possession_team: TeamReference | None = None
    under_pressure: bool | None = None


def normalize_event_context(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEventContext]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithContext]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid event context data") from exc

    result: list[MatchEventContext] = []

    for event in sorted(source_events, key=lambda item: item.index):
        header = headers_by_id[event.id]

        position = (
            {
                "external_position_id": str(event.position.id),
                "position_name": event.position.name,
            }
            if event.position is not None
            else None
        )

        result.append(
            MatchEventContext.model_validate(
                header.model_dump()
                | {
                    "position": position,
                    "possession_sequence": event.possession,
                    "external_possession_team_id": (
                        str(event.possession_team.id) if event.possession_team is not None else None
                    ),
                    "under_pressure": event.under_pressure,
                }
            )
        )

    return result
