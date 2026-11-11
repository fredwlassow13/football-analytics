from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    MatchAerialDuel,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombAerialDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    aerial_won: bool | None = None


class StatsBombEventWithAerialDetails(StatsBombEvent):
    pass_details: object = Field(default=None, alias="pass")
    clearance_details: object = Field(default=None, alias="clearance")
    shot_details: object = Field(default=None, alias="shot")
    miscontrol_details: object = Field(default=None, alias="miscontrol")


def normalize_aerial_duels(
    payload: object,
    *,
    match_id: int,
) -> list[MatchAerialDuel]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}
    duels = normalize_duels(payload, match_id=match_id)

    try:
        source_events = TypeAdapter(list[StatsBombEventWithAerialDetails]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid aerial event data") from exc

    result: list[MatchAerialDuel] = []

    for duel in duels:
        if duel.duel_type != "aerial_lost":
            continue

        if duel.outcome not in {None, "lost"}:
            raise ProviderUnavailable(
                f"Contradictory aerial loss outcome for event {duel.external_event_id}"
            )

        result.append(
            MatchAerialDuel.model_validate(
                headers_by_id[duel.external_event_id].model_dump()
                | {
                    "outcome": "lost",
                    "evidence": "aerial_lost_subtype",
                }
            )
        )

    for event in source_events:
        details_by_type = {
            "Pass": event.pass_details,
            "Clearance": event.clearance_details,
            "Shot": event.shot_details,
            "Miscontrol": event.miscontrol_details,
        }

        if event.type.name not in details_by_type:
            continue

        raw_details = details_by_type[event.type.name]
        if raw_details is None:
            continue

        try:
            details = StatsBombAerialDetails.model_validate(raw_details)

            if "aerial_won" in details.model_fields_set and details.aerial_won is None:
                raise ValueError("Explicit null aerial_won requires review")

            if details.aerial_won is not True:
                continue

            normalized = MatchAerialDuel.model_validate(
                headers_by_id[event.id].model_dump()
                | {
                    "outcome": "won",
                    "evidence": "aerial_won_flag",
                }
            )
        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid aerial details for StatsBomb event {event.id}"
            ) from exc

        result.append(normalized)

    return sorted(result, key=lambda item: item.source_index)
