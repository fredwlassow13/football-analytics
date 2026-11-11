from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.offensive_scope import select_offensive_periods
from football_analytics.ingestion.participation import ParticipationInterval
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


class PlayerScopeParticipation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_match_id: str
    external_team_id: str
    external_player_id: str
    is_starter: bool | None
    periods: tuple[int, ...]
    has_participation_evidence: bool
    elapsed_in_scope_ms: int | None
    intervals: tuple[ParticipationInterval, ...]

    @property
    def status(self) -> Literal["observed_in_scope", "no_interval_in_scope", "unknown"]:
        if self.elapsed_in_scope_ms is None:
            return "unknown"
        if self.elapsed_in_scope_ms > 0:
            return "observed_in_scope"
        return "no_interval_in_scope"


def scope_player_participation(
    players: Sequence[PlayerParticipation],
    periods: Sequence[PeriodBoundary],
    *,
    match_id: str,
    selected_periods: tuple[int, ...] | None = None,
) -> list[PlayerScopeParticipation]:
    ordered_periods = sorted(periods, key=lambda item: item.period)
    available = tuple(period.period for period in ordered_periods)

    # Reutiliza a mesma política de períodos do resumo ofensivo.
    select_offensive_periods(
        [],
        available_periods=available,
        periods=selected_periods,
    )
    requested = tuple(sorted(available if selected_periods is None else selected_periods))

    previous_end = 0
    for period in ordered_periods:
        if (
            period.start_elapsed_ms != previous_end
            or period.duration_ms <= 0
            or period.end_elapsed_ms != period.start_elapsed_ms + period.duration_ms
        ):
            raise ValueError("Invalid or noncontiguous period boundaries")
        previous_end = period.end_elapsed_ms

    selected_boundaries = [period for period in ordered_periods if period.period in requested]

    seen_players: set[str] = set()
    result: list[PlayerScopeParticipation] = []

    for player in sorted(
        players,
        key=lambda item: (item.external_team_id, item.external_player_id),
    ):
        if player.external_match_id != match_id:
            raise ValueError("Participation belongs to another match")
        if player.external_player_id in seen_players:
            raise ValueError("Duplicate player participation")
        seen_players.add(player.external_player_id)

        intervals = sorted(player.intervals, key=lambda item: item.start_ms)
        interval_end = 0

        for interval in intervals:
            if (
                interval.start_ms < interval_end
                or interval.start_ms < 0
                or interval.end_ms < interval.start_ms
                or interval.end_ms > previous_end
            ):
                raise ValueError("Invalid participation intervals")
            interval_end = interval.end_ms

        if not intervals:
            if player.elapsed_on_field_ms is not None:
                raise ValueError("Duration without participation intervals")
        elif player.elapsed_on_field_ms != sum(interval.duration_ms for interval in intervals):
            raise ValueError("Participation duration differs from intervals")

        clipped: list[ParticipationInterval] = []
        for interval in intervals:
            for period in selected_boundaries:
                start = max(interval.start_ms, period.start_elapsed_ms)
                end = min(interval.end_ms, period.end_elapsed_ms)
                if start < end:
                    clipped.append(ParticipationInterval(start_ms=start, end_ms=end))

        result.append(
            PlayerScopeParticipation(
                external_match_id=match_id,
                external_team_id=player.external_team_id,
                external_player_id=player.external_player_id,
                is_starter=player.is_starter,
                periods=requested,
                has_participation_evidence=bool(intervals),
                elapsed_in_scope_ms=(
                    sum(interval.duration_ms for interval in clipped) if intervals else None
                ),
                intervals=tuple(clipped),
            )
        )

    return result
