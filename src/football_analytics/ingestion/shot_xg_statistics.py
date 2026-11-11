from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.shot_statistics import aggregate_player_shots
from football_analytics.integrations.base import MatchShot

NON_PENALTY_TYPES = {"Open Play", "Free Kick", "Corner", "Kick Off"}


class ShotXgTotals(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    shots: int = Field(ge=0)
    goals: int = Field(ge=0)
    shots_with_xg: int = Field(ge=0)
    available_xg_sum: Decimal | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )

    @property
    def missing_xg(self) -> int:
        return self.shots - self.shots_with_xg

    @property
    def xg_total(self) -> Decimal | None:
        if self.shots_with_xg != self.shots:
            return None

        return self.available_xg_sum


class PlayerShotXgStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    overall: ShotXgTotals
    penalties: ShotXgTotals
    non_penalties: ShotXgTotals
    unclassified_type: ShotXgTotals


def _summarize_shots(shots: Sequence[MatchShot]) -> ShotXgTotals:
    values = [Decimal(str(shot.statsbomb_xg)) for shot in shots if shot.statsbomb_xg is not None]

    return ShotXgTotals(
        shots=len(shots),
        goals=sum(shot.is_goal for shot in shots),
        shots_with_xg=len(values),
        available_xg_sum=sum(values, Decimal(0)) if values else None,
    )


def aggregate_player_shot_xg(
    shots: list[MatchShot],
) -> list[PlayerShotXgStatistics]:
    validated_players = aggregate_player_shots(shots)
    result: list[PlayerShotXgStatistics] = []

    for player in validated_players:
        player_shots = [
            shot
            for shot in shots
            if shot.period in {1, 2, 3, 4}
            and shot.external_team_id == player.external_team_id
            and shot.external_player_id == player.external_player_id
        ]

        result.append(
            PlayerShotXgStatistics(
                provider=player.provider,
                external_match_id=player.external_match_id,
                external_team_id=player.external_team_id,
                external_player_id=player.external_player_id,
                overall=_summarize_shots(player_shots),
                penalties=_summarize_shots(
                    [shot for shot in player_shots if shot.shot_type == "Penalty"]
                ),
                non_penalties=_summarize_shots(
                    [shot for shot in player_shots if shot.shot_type in NON_PENALTY_TYPES]
                ),
                unclassified_type=_summarize_shots(
                    [
                        shot
                        for shot in player_shots
                        if shot.shot_type != "Penalty" and shot.shot_type not in NON_PENALTY_TYPES
                    ]
                ),
            )
        )

    return result
