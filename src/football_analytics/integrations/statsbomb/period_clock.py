from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.clock import (
    timestamp_to_milliseconds,
)


class ClockEventType(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str


class ClockEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    type: ClockEventType
    period: int = Field(ge=1, le=5)
    timestamp: str


class PeriodBoundary(BaseModel):
    period: int
    duration_ms: int
    start_elapsed_ms: int
    end_elapsed_ms: int


def normalize_period_clock(
    payload: object,
) -> list[PeriodBoundary]:
    try:
        events = TypeAdapter(list[ClockEvent]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid clock event data") from exc

    durations: dict[int, int] = {}

    for event in events:
        if event.type.name != "Half End" or event.period == 5:
            continue

        try:
            duration = timestamp_to_milliseconds(event.timestamp)
        except ValueError as exc:
            raise ProviderUnavailable(f"Invalid end timestamp for period {event.period}") from exc

        if duration <= 0:
            raise ProviderUnavailable(f"Invalid duration for period {event.period}")

        existing = durations.get(event.period)

        if existing is not None and existing != duration:
            raise ProviderUnavailable(f"Conflicting end timestamps for period {event.period}")

        durations[event.period] = duration

    periods = set(durations)

    if periods != {1, 2} and periods != {1, 2, 3, 4}:
        raise ProviderUnavailable("Expected two complete periods or four with extra time")

    result: list[PeriodBoundary] = []
    elapsed = 0

    for period in sorted(durations):
        duration = durations[period]

        result.append(
            PeriodBoundary(
                period=period,
                duration_ms=duration,
                start_elapsed_ms=elapsed,
                end_elapsed_ms=elapsed + duration,
            )
        )

        elapsed += duration

    return result


def event_to_elapsed_ms(
    period: int,
    timestamp: str,
    periods: list[PeriodBoundary],
) -> int:
    boundaries = [item for item in periods if item.period == period]

    if period not in {1, 2, 3, 4} or len(boundaries) != 1:
        raise ValueError(f"Unknown or repeated playing period: {period}")

    boundary = boundaries[0]
    within_period = timestamp_to_milliseconds(timestamp)

    if within_period > boundary.duration_ms:
        raise ValueError(f"Event timestamp exceeds duration of period {period}")

    return boundary.start_elapsed_ms + within_period
