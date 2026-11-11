from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import (
    MatchTacticalSnapshot,
    TacticalPlayer,
)


class TacticalPositionChange(BaseModel):
    model_config = ConfigDict(strict=True)

    previous: TacticalPlayer
    current: TacticalPlayer


class TacticalChanges(BaseModel):
    model_config = ConfigDict(strict=True)

    previous_event_id: str
    current_event_id: str
    previous_formation: str
    current_formation: str
    added: list[TacticalPlayer]
    removed: list[TacticalPlayer]
    position_changes: list[TacticalPositionChange]

    @property
    def formation_changed(self) -> bool:
        return self.previous_formation != self.current_formation


def compare_tactical_snapshots(
    previous: MatchTacticalSnapshot,
    current: MatchTacticalSnapshot,
) -> TacticalChanges:
    previous_identity = (
        previous.provider,
        previous.external_match_id,
        previous.external_team_id,
    )
    current_identity = (
        current.provider,
        current.external_match_id,
        current.external_team_id,
    )

    if previous_identity != current_identity:
        raise ValueError("Snapshots must belong to one provider, match and team")

    if previous.external_team_id is None:
        raise ValueError("Tactical comparison requires a team")

    if previous.external_event_id == current.external_event_id:
        raise ValueError("Snapshots must reference distinct events")

    if current.source_index <= previous.source_index:
        raise ValueError("Current snapshot must follow previous source index")

    before = {player.external_player_id: player for player in previous.lineup}
    after = {player.external_player_id: player for player in current.lineup}

    position_changes = []

    for player_id in sorted(before.keys() & after.keys()):
        old = before[player_id]
        new = after[player_id]

        if (
            old.external_position_id,
            old.position_name,
        ) != (
            new.external_position_id,
            new.position_name,
        ):
            position_changes.append(TacticalPositionChange(previous=old, current=new))

    return TacticalChanges(
        previous_event_id=previous.external_event_id,
        current_event_id=current.external_event_id,
        previous_formation=previous.formation,
        current_formation=current.formation,
        added=[after[player_id] for player_id in sorted(after.keys() - before.keys())],
        removed=[before[player_id] for player_id in sorted(before.keys() - after.keys())],
        position_changes=position_changes,
    )
