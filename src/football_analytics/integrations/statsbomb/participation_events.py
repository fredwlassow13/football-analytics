from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.starting_xi import (
    EventType,
    PlayerReference,
    StartingTactics,
    TeamReference,
)


class SubstitutionDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    replacement: PlayerReference


class CardReference(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str


class DisciplineDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    card: CardReference | None = None


class ParticipationEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    type: EventType
    index: int = Field(gt=0)
    period: int = Field(ge=1, le=5)
    timestamp: str
    team: TeamReference | None = None
    player: PlayerReference | None = None
    tactics: object = None
    substitution: SubstitutionDetails | None = None
    foul_committed: DisciplineDetails | None = None
    bad_behaviour: DisciplineDetails | None = None


class PlayerAction(BaseModel):
    external_player_id: str
    external_team_id: str
    action: Literal["enter", "leave"]
    reason: str
    event_index: int
    period: int
    timestamp: str


def normalize_participation_events(
    payload: object,
) -> list[PlayerAction]:
    try:
        events = TypeAdapter(list[ParticipationEvent]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid participation event data") from exc

    result: list[PlayerAction] = []

    for event in events:
        if event.period == 5:
            continue

        actions: list[tuple[int, Literal["enter", "leave"], str]] = []
        event_type = event.type.name

        if event_type == "Starting XI":
            try:
                tactics = StartingTactics.model_validate(event.tactics)
            except ValidationError as exc:
                raise ProviderUnavailable("Invalid Starting XI tactics") from exc

            for item in tactics.lineup:
                actions.append((item.player.id, "enter", "Starting XI"))

        elif event_type == "Substitution":
            if event.player is None or event.substitution is None:
                raise ProviderUnavailable("Substitution requires outgoing and incoming players")

            outgoing_id = event.player.id
            incoming_id = event.substitution.replacement.id

            if outgoing_id == incoming_id:
                raise ProviderUnavailable("Substitution cannot replace a player with himself")

            actions.extend(
                [
                    (outgoing_id, "leave", "Substitution"),
                    (incoming_id, "enter", "Substitution"),
                ]
            )

        elif event_type in {"Player Off", "Player On"}:
            if event.player is None:
                raise ProviderUnavailable(f"{event_type} requires a player")

            if event_type == "Player Off":
                actions.append((event.player.id, "leave", event_type))
            else:
                actions.append((event.player.id, "enter", event_type))

        for discipline in (
            event.foul_committed,
            event.bad_behaviour,
        ):
            if discipline is None or discipline.card is None:
                continue

            card_name = discipline.card.name

            if card_name == "Yellow Card":
                continue

            if card_name not in {"Red Card", "Second Yellow"}:
                raise ProviderUnavailable(f"Unsupported card category: {card_name}")

            if event.player is None:
                raise ProviderUnavailable("Dismissal requires a player")

            actions.append((event.player.id, "leave", card_name))

        if actions and event.team is None:
            raise ProviderUnavailable("Participation action requires a team")

        for player_id, action, reason in actions:
            if event.team is None:
                raise ProviderUnavailable("Participation action requires a team")

            result.append(
                PlayerAction(
                    external_player_id=str(player_id),
                    external_team_id=str(event.team.id),
                    action=action,
                    reason=reason,
                    event_index=event.index,
                    period=event.period,
                    timestamp=event.timestamp,
                )
            )

    return result
