from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from football_analytics.integrations.base import (
    MatchPass,
    MatchPassCreation,
    MatchShot,
    MatchShotCreation,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import StatsBombEvent
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.shots import normalize_shots

type CreationAction = MatchPassCreation | MatchShotCreation


class StatsBombCreationDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def reject_explicit_null_fields(self) -> "StatsBombCreationDetails":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"Explicit null {name} requires review")
        return self


class StatsBombPassCreationDetails(StatsBombCreationDetails):
    shot_assist: bool | None = None
    goal_assist: bool | None = None
    assisted_shot_id: str | None = Field(default=None, min_length=1)


class StatsBombShotCreationDetails(StatsBombCreationDetails):
    key_pass_id: str | None = Field(default=None, min_length=1)


class StatsBombEventWithCreation(StatsBombEvent):
    pass_details: object = Field(default=None, alias="pass")
    shot_details: object = Field(default=None, alias="shot")


def normalize_creation_events(
    payload: object,
    *,
    match_id: int,
) -> list[CreationAction]:
    passes = normalize_passes(payload, match_id=match_id)
    shots = normalize_shots(payload, match_id=match_id)

    try:
        source_events = TypeAdapter(list[StatsBombEventWithCreation]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid creation event data") from exc

    source_by_id = {event.id: event for event in source_events}
    base_events: list[MatchPass | MatchShot] = [*passes, *shots]
    result: list[CreationAction] = []

    for base_event in sorted(base_events, key=lambda item: item.source_index):
        source = source_by_id[base_event.external_event_id]

        try:
            if isinstance(base_event, MatchPass):
                pass_details = StatsBombPassCreationDetails.model_validate(source.pass_details)
                result.append(
                    MatchPassCreation.model_validate(
                        base_event.model_dump()
                        | {
                            "shot_assist": pass_details.shot_assist,
                            "goal_assist": pass_details.goal_assist,
                            "external_assisted_shot_event_id": (pass_details.assisted_shot_id),
                        }
                    )
                )
            else:
                shot_details = StatsBombShotCreationDetails.model_validate(source.shot_details)
                result.append(
                    MatchShotCreation.model_validate(
                        base_event.model_dump()
                        | {
                            "external_key_pass_event_id": (shot_details.key_pass_id),
                        }
                    )
                )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid creation details for StatsBomb event {base_event.external_event_id}"
            ) from exc

    return result
