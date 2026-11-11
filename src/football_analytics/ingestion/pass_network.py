from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.pass_statistics import aggregate_player_passes
from football_analytics.integrations.base import MatchPass


class PassConnection(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    external_match_id: str
    external_team_id: str
    external_sender_player_id: str
    external_recipient_player_id: str
    external_event_ids: tuple[str, ...] = Field(min_length=1)

    @property
    def completed_passes(self) -> int:
        return len(self.external_event_ids)


def build_pass_connections(
    passes: list[MatchPass],
) -> list[PassConnection]:
    """Build directed completed-pass connections; temporal audit is separate."""

    validated_players = aggregate_player_passes(passes)

    if not validated_players:
        return []

    provider = validated_players[0].provider
    match_id = validated_players[0].external_match_id
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for event in sorted(passes, key=lambda item: item.source_index):
        if event.period not in {1, 2, 3, 4}:
            continue

        if event.is_completed is not True:
            continue

        team_id = event.external_team_id
        sender_id = event.external_player_id
        recipient_id = event.external_recipient_player_id

        if team_id is None or sender_id is None:
            raise ValueError("Pass connection requires team and sender")

        if recipient_id is None:
            raise ValueError("Completed pass requires a recipient for the network")

        grouped[(team_id, sender_id, recipient_id)].append(event.external_event_id)

    return [
        PassConnection(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_sender_player_id=sender_id,
            external_recipient_player_id=recipient_id,
            external_event_ids=tuple(event_ids),
        )
        for (team_id, sender_id, recipient_id), event_ids in sorted(grouped.items())
    ]
