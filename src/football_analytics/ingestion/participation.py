from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ParticipationTransition(BaseModel):
    model_config = ConfigDict(strict=True)

    action: Literal["enter", "leave"]
    elapsed_ms: int = Field(ge=0)
    event_index: int = Field(gt=0)


class ParticipationInterval(BaseModel):
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def build_player_intervals(
    transitions: list[ParticipationTransition],
    match_duration_ms: int,
) -> list[ParticipationInterval]:
    if match_duration_ms <= 0:
        raise ValueError("Match duration must be positive")

    ordered = sorted(
        transitions,
        key=lambda item: item.event_index,
    )

    indexes = [item.event_index for item in ordered]

    if len(indexes) != len(set(indexes)):
        raise ValueError("Repeated event index for player")

    intervals: list[ParticipationInterval] = []
    active_start: int | None = None
    previous_time = 0

    for transition in ordered:
        current_time = transition.elapsed_ms

        if current_time < previous_time:
            raise ValueError("Player events move backwards in time")

        if current_time > match_duration_ms:
            raise ValueError("Player event occurs after match end")

        previous_time = current_time

        if transition.action == "enter":
            if active_start is not None:
                raise ValueError("Player entered while already active")

            active_start = current_time

        else:
            if active_start is None:
                raise ValueError("Player left while not active")

            intervals.append(
                ParticipationInterval(
                    start_ms=active_start,
                    end_ms=current_time,
                )
            )

            active_start = None

    if active_start is not None:
        intervals.append(
            ParticipationInterval(
                start_ms=active_start,
                end_ms=match_duration_ms,
            )
        )

    return intervals
