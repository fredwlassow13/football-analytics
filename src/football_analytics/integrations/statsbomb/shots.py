from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchShot, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombShotOutcome(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal[
        "Goal",
        "Saved",
        "Saved to Post",
        "Saved To Post",
        "Saved Off Target",
        "Blocked",
        "Off T",
        "Post",
        "Wayward",
    ]


class StatsBombShotBodyPart(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Left Foot", "Right Foot", "Head", "Other"]


class StatsBombShotAttribute(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(min_length=1)


class StatsBombShotDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: StatsBombShotOutcome
    body_part: StatsBombShotBodyPart | None = None
    technique: StatsBombShotAttribute | None = None
    type: StatsBombShotAttribute | None = None
    statsbomb_xg: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
    )


class StatsBombEventWithShot(StatsBombEvent):
    shot_details: object = Field(default=None, alias="shot")


def normalize_shots(
    payload: object,
    *,
    match_id: int,
) -> list[MatchShot]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithShot]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid shot event data") from exc

    outcome_mapping = {
        "Goal": "goal",
        "Saved": "saved",
        "Saved to Post": "saved_to_post",
        "Saved To Post": "saved_to_post",
        "Saved Off Target": "saved_off_target",
        "Blocked": "blocked",
        "Off T": "off_target",
        "Post": "post",
        "Wayward": "wayward",
    }
    body_part_mapping = {
        "Left Foot": "left_foot",
        "Right Foot": "right_foot",
        "Head": "head",
        "Other": "other",
    }

    result: list[MatchShot] = []

    for event in sorted(source_events, key=lambda item: item.index):
        if event.type.name != "Shot":
            continue

        try:
            details = StatsBombShotDetails.model_validate(event.shot_details)
            header = headers_by_id[event.id]

            shot = MatchShot.model_validate(
                header.model_dump()
                | {
                    "outcome": outcome_mapping[details.outcome.name],
                    "body_part": (
                        body_part_mapping[details.body_part.name]
                        if details.body_part is not None
                        else None
                    ),
                    "technique": (
                        details.technique.name if details.technique is not None else None
                    ),
                    "shot_type": (details.type.name if details.type is not None else None),
                    "statsbomb_xg": details.statsbomb_xg,
                }
            )
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid shot details for StatsBomb event {event.id}"
            ) from exc

        header = headers_by_id[event.id]

        result.append(shot)

    return result
