"""Read-only structured execution for pass networks and defensive maps."""

from sqlalchemy import Connection, Engine, text

from football_analytics.analytics.capabilities import STRUCTURED_EXECUTOR_RESOURCES
from football_analytics.analytics.contracts import SourceReference
from football_analytics.analytics.executor import load_query_sources
from football_analytics.analytics.structured_contracts import (
    MapData,
    MapOptions,
    NetworkData,
    StructuredQuery,
    StructuredResult,
)
from football_analytics.analytics.structured_registry import get_structured_resource
from football_analytics.analytics.structured_sequences import (
    SEQUENCE_RESOURCES,
    execute_sequences,
)
from football_analytics.ingestion.action_context import contextualize_actions
from football_analytics.ingestion.audited_roster import verify_stored_participation
from football_analytics.ingestion.defensive_map import build_defensive_map
from football_analytics.ingestion.defensive_scope import DefensiveEvent
from football_analytics.ingestion.match_participation import reconstruct_match_participation
from football_analytics.ingestion.pass_network import build_pass_connections
from football_analytics.ingestion.pass_network_nodes import build_pass_network_nodes
from football_analytics.ingestion.player_scope_participation import (
    PlayerScopeParticipation,
    scope_player_participation,
)
from football_analytics.integrations.base import MatchEvent
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.event_context import normalize_event_context
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.spatial import normalize_event_spatial
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi

SUPPORTED_RESOURCES = STRUCTURED_EXECUTOR_RESOURCES


def _unavailable(
    query: StructuredQuery,
    reason: str,
    source: SourceReference | None = None,
) -> StructuredResult:
    return StructuredResult(
        query=query,
        source=source,
        status="unavailable",
        reasons=(reason,),
    )


def _check_actor(
    event: MatchEvent,
    presence: dict[str, PlayerScopeParticipation],
) -> None:
    player = presence.get(event.external_player_id or "")

    if player is None or player.external_team_id != event.external_team_id:
        raise ValueError("Action player/team is outside the source roster")

    if player.status != "observed_in_scope":
        raise ValueError("Observed action lacks participation in the selected scope")


def _network(
    query: StructuredQuery,
    source: SourceReference,
    events: object,
    presence: dict[str, PlayerScopeParticipation],
    periods: tuple[int, ...],
) -> StructuredResult:
    passes = [
        p
        for p in normalize_passes(events, match_id=query.match_id)
        if p.period in periods and p.external_team_id == query.team_id
    ]

    for event in passes:
        _check_actor(event, presence)

        if event.is_completed is True:
            recipient = event.external_recipient_player_id

            if recipient is None:
                return _unavailable(
                    query,
                    "completed_pass_without_recipient",
                    source,
                )

            target = presence.get(recipient)
            if target is None or target.external_team_id != query.team_id:
                raise ValueError("Pass recipient is not listed for the selected team")

            if target.status != "observed_in_scope":
                return _unavailable(
                    query,
                    "recipient_presence_not_established_in_scope",
                    source,
                )

    contextual = contextualize_actions(
        actions=list(passes),
        contexts=normalize_event_context(events, match_id=query.match_id),
        spatial_events=normalize_event_spatial(events, match_id=query.match_id),
    )

    nodes = tuple(build_pass_network_nodes(contextual))
    connections = tuple(build_pass_connections(passes))
    positioned = {n.external_player_id for n in nodes}

    reasons = []

    if any(c.external_recipient_player_id not in positioned for c in connections):
        reasons.append("recipient_without_origin")

    if any(p.is_completed is None for p in passes):
        reasons.append("network_pass_outcome_unknown")

    if any(
        item.spatial.start_location is not None
        and not (
            0 <= item.spatial.start_location.x <= 120 and 0 <= item.spatial.start_location.y <= 80
        )
        for item in contextual
    ):
        reasons.append("network_origin_outside_pitch")

    return StructuredResult(
        query=query,
        source=source,
        status="review" if reasons else "available",
        reasons=tuple(reasons),
        data=NetworkData(
            periods=periods,
            nodes=nodes,
            connections=connections,
        ),
    )


def _map(
    query: StructuredQuery,
    source: SourceReference,
    events: object,
    presence: dict[str, PlayerScopeParticipation],
    periods: tuple[int, ...],
) -> StructuredResult:
    if query.player_id is not None:
        player = presence.get(query.player_id)

        if player is None or player.external_team_id != query.team_id:
            raise ValueError("Map player is not listed for the selected team")

        if player.status != "observed_in_scope":
            return _unavailable(
                query,
                "player_presence_not_established_in_scope",
                source,
            )

    actions: list[DefensiveEvent] = [
        *normalize_duels(events, match_id=query.match_id),
        *normalize_interceptions(events, match_id=query.match_id),
        *normalize_ball_recoveries(events, match_id=query.match_id),
        *normalize_blocks_and_clearances(events, match_id=query.match_id),
        *normalize_pressures_and_fouls(events, match_id=query.match_id),
    ]
    aerials = normalize_aerial_duels(events, match_id=query.match_id)

    actors: list[MatchEvent] = [*actions, *aerials]
    for event in actors:
        if event.period in periods:
            _check_actor(event, presence)

    options = query.map_options or MapOptions()

    if query.team_id is None:
        raise ValueError("Defensive map requires a team")

    value = build_defensive_map(
        actions,
        aerials,
        normalize_event_spatial(events, match_id=query.match_id),
        provider=query.provider,
        match_id=str(query.match_id),
        events_sha256=source.events_sha256,
        team_id=query.team_id,
        player_id=query.player_id,
        available_periods=source.available_periods,
        periods=periods,
        group=options.group,
        columns=options.columns,
        rows=options.rows,
    )

    reasons = ("map_location_issues",) if value.location_issues else ()

    return StructuredResult(
        query=query,
        source=source,
        status="review" if reasons else "available",
        reasons=reasons,
        data=MapData(value=value),
    )


def execute_structured_query(
    connection: Connection,
    query: StructuredQuery,
) -> StructuredResult:
    """Caller manages the transaction; run_structured_query provides a snapshot."""
    get_structured_resource(query.resource_id)

    if query.resource_id not in SUPPORTED_RESOURCES | SEQUENCE_RESOURCES:
        return _unavailable(query, "structured_resource_not_integrated")

    source, events, lineup_payload = load_query_sources(
        connection,
        match_id=query.match_id,
    )

    if source.match_id != query.match_id:
        raise ValueError("Selected source belongs to another match")

    if source.participation_calculation_id is None or source.lineups_sha256 is None:
        raise ValueError("Structured execution requires participation provenance")

    clock = normalize_period_clock(events)
    if tuple(p.period for p in clock) != source.available_periods:
        raise ValueError("Source periods differ from normalized event clock")

    periods = tuple(sorted(query.periods or source.available_periods))
    if not set(periods).issubset(source.available_periods):
        raise ValueError("Requested period is unavailable")

    lineups = normalize_lineups(
        lineup_payload,
        query.match_id,
        starting_xi=normalize_starting_xi(events),
    )

    if query.team_id is not None and query.team_id not in {p.external_team_id for p in lineups}:
        raise ValueError("Requested team does not belong to this match")

    players = reconstruct_match_participation(
        lineup_payload,
        events,
        query.match_id,
    )
    verify_stored_participation(
        connection,
        source.participation_calculation_id,
        players,
    )

    if query.resource_id in SEQUENCE_RESOURCES:
        return execute_sequences(query, source, events)

    scoped = scope_player_participation(
        players,
        clock,
        match_id=str(query.match_id),
        selected_periods=periods,
    )
    presence = {p.external_player_id: p for p in scoped}

    if query.resource_id == "analysis.pass_connections":
        return _network(query, source, events, presence, periods)

    return _map(query, source, events, presence, periods)


def run_structured_query(
    engine: Engine,
    query: StructuredQuery,
) -> StructuredResult:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return execute_structured_query(connection, query)
