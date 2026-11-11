from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_analytics.integrations.base import MatchTacticalSnapshot


class TacticalPositionEvidence(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    external_position_id: str = Field(min_length=1)
    position_name: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)


class TacticalPlayerState(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    external_player_id: str = Field(min_length=1)
    last_recorded_position: TacticalPositionEvidence | None = None


class TacticalSegment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    external_team_id: str = Field(min_length=1)
    start_elapsed_ms: int = Field(ge=0)
    end_elapsed_ms: int = Field(ge=0)
    last_recorded_formation: str = Field(min_length=1)
    formation_source_event_id: str = Field(min_length=1)
    players: tuple[TacticalPlayerState, ...] = Field(max_length=11)

    @model_validator(mode="after")
    def validate_segment(self) -> "TacticalSegment":
        if self.end_elapsed_ms < self.start_elapsed_ms:
            raise ValueError("Segment end must not precede start")

        player_ids = {player.external_player_id for player in self.players}

        if len(player_ids) != len(self.players):
            raise ValueError("Segment contains duplicate players")

        return self

    @property
    def duration_ms(self) -> int:
        return self.end_elapsed_ms - self.start_elapsed_ms

    @property
    def player_count(self) -> int:
        return len(self.players)


def build_tactical_segment(
    *,
    snapshot: MatchTacticalSnapshot,
    snapshot_elapsed_ms: int,
    active_player_ids: set[str],
    start_elapsed_ms: int,
    end_elapsed_ms: int,
) -> TacticalSegment:
    if snapshot.external_team_id is None:
        raise ValueError("Tactical segment requires a team")

    if snapshot.period not in {1, 2, 3, 4}:
        raise ValueError("Tactical segment requires a playing period")

    if snapshot_elapsed_ms < 0:
        raise ValueError("Snapshot elapsed time must not be negative")

    if snapshot_elapsed_ms > start_elapsed_ms:
        raise ValueError("Snapshot occurs after segment start")

    positions = {
        player.external_player_id: TacticalPositionEvidence(
            external_position_id=player.external_position_id,
            position_name=player.position_name,
            source_event_id=snapshot.external_event_id,
        )
        for player in snapshot.lineup
    }

    return TacticalSegment(
        provider=snapshot.provider,
        external_match_id=snapshot.external_match_id,
        external_team_id=snapshot.external_team_id,
        start_elapsed_ms=start_elapsed_ms,
        end_elapsed_ms=end_elapsed_ms,
        last_recorded_formation=snapshot.formation,
        formation_source_event_id=snapshot.external_event_id,
        players=tuple(
            TacticalPlayerState(
                external_player_id=player_id,
                last_recorded_position=positions.get(player_id),
            )
            for player_id in sorted(active_player_ids)
        ),
    )
