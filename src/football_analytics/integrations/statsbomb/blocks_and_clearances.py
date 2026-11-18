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
    MatchBlock,
    MatchClearance,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombOptionalDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def reject_explicit_null_fields(self) -> "StatsBombOptionalDetails":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"Explicit null {name} requires review")
        return self


class StatsBombBlockDetails(StatsBombOptionalDetails):
    deflection: bool | None = None
    offensive: bool | None = None
    save_block: bool | None = None


class StatsBombClearanceBodyPart(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Head", "Left Foot", "Right Foot", "Other"]


class StatsBombClearanceDetails(StatsBombOptionalDetails):
    body_part: StatsBombClearanceBodyPart | None = None
    aerial_won: bool | None = None
    head: bool | None = None
    left_foot: bool | None = None
    right_foot: bool | None = None
    other: bool | None = None


class StatsBombEventWithBlockOrClearance(StatsBombEvent):
    block_details: object = Field(default_factory=dict, alias="block")
    clearance_details: object = Field(default_factory=dict, alias="clearance")
    counterpress: object = None


def normalize_blocks_and_clearances(
    payload: object,
    *,
    match_id: int,
) -> list[MatchBlock | MatchClearance]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        events = TypeAdapter(list[StatsBombEventWithBlockOrClearance]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable(
            "StatsBomb returned invalid block or clearance event data"
        ) from exc

    result: list[MatchBlock | MatchClearance] = []

    for event in sorted(events, key=lambda item: item.index):
        if event.type.name not in {"Block", "Clearance"}:
            continue

        try:
            header = headers_by_id[event.id].model_dump()

            if event.type.name == "Block":
                block = StatsBombBlockDetails.model_validate(event.block_details)

                if "counterpress" in event.model_fields_set and event.counterpress is None:
                    raise ValueError("Explicit null counterpress requires review")

                result.append(
                    MatchBlock.model_validate(
                        header | block.model_dump() | {"counterpress": event.counterpress}
                    )
                )
            else:
                clearance = StatsBombClearanceDetails.model_validate(event.clearance_details)
                attributes = clearance.model_dump(exclude={"body_part"})
                attributes["body_part"] = (
                    clearance.body_part.name.lower().replace(" ", "_")
                    if clearance.body_part is not None
                    else None
                )

                result.append(MatchClearance.model_validate(header | attributes))

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid block or clearance details for StatsBomb event {event.id}"
            ) from exc

    return result
