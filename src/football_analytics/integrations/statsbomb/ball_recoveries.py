from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from football_analytics.integrations.base import MatchBallRecovery, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import StatsBombEvent, normalize_events


class StatsBombBallRecoveryDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    recovery_failure: bool | None = None
    offensive: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_null_flags(self) -> "StatsBombBallRecoveryDetails":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"Explicit null {name} requires review")
        return self


class StatsBombEventWithBallRecovery(StatsBombEvent):
    ball_recovery_details: object = Field(default_factory=dict, alias="ball_recovery")


def normalize_ball_recoveries(payload: object, *, match_id: int) -> list[MatchBallRecovery]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        events = TypeAdapter(list[StatsBombEventWithBallRecovery]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid ball recovery event data") from exc

    result: list[MatchBallRecovery] = []
    for event in sorted(events, key=lambda item: item.index):
        if event.type.name != "Ball Recovery":
            continue
        try:
            details = StatsBombBallRecoveryDetails.model_validate(event.ball_recovery_details)
            result.append(
                MatchBallRecovery.model_validate(
                    headers_by_id[event.id].model_dump() | details.model_dump()
                )
            )
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid ball recovery details for StatsBomb event {event.id}"
            ) from exc
    return result
