"""Event-local progression/control classified with the existing policies."""

from typing import Literal, TypedDict

from football_analytics.analytics.complementary_execution import FamilyContext
from football_analytics.analytics.contracts import Contract
from football_analytics.ingestion.ball_control_statistics import (
    build_ball_control_statistics,
)
from football_analytics.ingestion.match_evolution import MatchEvolution
from football_analytics.ingestion.movement_statistics import (
    build_movement_statistics,
)
from football_analytics.integrations.base import MatchDribbledPast, MatchPressure
from football_analytics.integrations.statsbomb.ball_control import normalize_ball_control_incidents
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.pressure_metadata import (
    normalize_event_pressure_metadata,
)
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.spatial import normalize_event_spatial


class SummaryOptions(TypedDict):
    provider: str
    match_id: str
    events_sha256: str
    available_periods: tuple[int, ...]
    periods: tuple[int, ...] | None


COMPLEMENTARY_SCORE_FAMILIES = frozenset({"movement", "ball_control"})


class ScoreStateComplementaryEvidence(Contract):
    team_id: str
    family: Literal["movement", "ball_control"]
    selected_event_ids: tuple[str, ...]


def prepare_score_state_complementary(
    payload: object,
    evolution: MatchEvolution,
    *,
    state: str,
    periods: tuple[int, ...] | None,
    available_periods: tuple[int, ...],
    families: frozenset[str],
) -> tuple[dict[str, FamilyContext], tuple[ScoreStateComplementaryEvidence, ...]]:
    if not isinstance(payload, list):
        raise ValueError("Expected event list")
    timeline = {e.external_event_id: e for e in evolution.events}
    # These classifications are local to an event. Preserve its original header,
    # geometry and pressure metadata together; no links are reconstructed here.
    selected = [
        e
        for e in payload
        if e["id"] in timeline
        and e["period"] != 5
        and timeline[e["id"]].score_state == state
        and (periods is None or e["period"] in periods)
    ]
    common = SummaryOptions(
        provider=evolution.provider,
        match_id=evolution.external_match_id,
        events_sha256=evolution.events_sha256,
        available_periods=available_periods,
        periods=periods,
    )
    spatial = normalize_event_spatial(selected, match_id=int(evolution.external_match_id))
    passes = normalize_passes(selected, match_id=int(evolution.external_match_id))
    result: dict[str, FamilyContext] = {}
    evidence = []
    if "movement" in families:
        movement = build_movement_statistics(spatial, passes, **common)
        result["movement"] = FamilyContext({}, {t.external_team_id: t for t in movement.teams}, {})
        for team in evolution.team_ids:
            evidence.append(
                ScoreStateComplementaryEvidence(
                    team_id=team,
                    family="movement",
                    selected_event_ids=tuple(
                        e.external_event_id
                        for e in spatial
                        if e.event_type in {"Pass", "Carry"} and e.external_team_id == team
                    ),
                )
            )
    if "ball_control" in families:
        match_id = int(evolution.external_match_id)
        actions = [
            *normalize_ball_control_incidents(selected, match_id=match_id),
            *passes,
            *normalize_dribbles(selected, match_id=match_id),
            *normalize_ball_recoveries(selected, match_id=match_id),
            *normalize_interceptions(selected, match_id=match_id),
            *normalize_duels(selected, match_id=match_id),
            *normalize_blocks_and_clearances(selected, match_id=match_id),
            *(
                a
                for a in normalize_pressures_and_fouls(selected, match_id=match_id)
                if isinstance(a, MatchPressure | MatchDribbledPast)
            ),
        ]
        control = build_ball_control_statistics(
            actions,
            spatial,
            normalize_event_pressure_metadata(selected, match_id=match_id),
            **common,
        )
        result["ball_control"] = FamilyContext(
            {}, {t.external_team_id: t for t in control.teams}, {}
        )
        for team in evolution.team_ids:
            evidence.append(
                ScoreStateComplementaryEvidence(
                    team_id=team,
                    family="ball_control",
                    selected_event_ids=tuple(
                        a.external_event_id
                        for a in sorted(actions, key=lambda a: a.source_index)
                        if a.external_team_id == team
                    ),
                )
            )
    return result, tuple(evidence)
