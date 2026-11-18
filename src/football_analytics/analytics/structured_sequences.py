"""Full-match possession and attack adapters for structured execution."""

from football_analytics.analytics.contracts import SourceReference
from football_analytics.analytics.structured_contracts import (
    AttackData,
    PossessionData,
    StructuredQuery,
    StructuredResult,
)
from football_analytics.ingestion.attack_sequences import build_attack_sequences
from football_analytics.ingestion.possession_sequences import build_possession_sequences
from football_analytics.integrations.base import MatchFoulCommitted, MatchFoulWon
from football_analytics.integrations.statsbomb.event_context import normalize_event_context
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.play_metadata import normalize_event_play_metadata
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.shots import normalize_shots

SEQUENCE_RESOURCES = frozenset({"analysis.possession_fragments", "analysis.attacks"})


def execute_sequences(
    query: StructuredQuery,
    source: SourceReference,
    events: object,
) -> StructuredResult:
    """Adapt existing calculations without estimating possession duration."""
    if query.resource_id not in SEQUENCE_RESOURCES:
        raise ValueError("Unsupported sequence resource")

    if source.match_id != query.match_id:
        raise ValueError("Sequence source belongs to another match")

    contexts = normalize_event_context(events, match_id=query.match_id)
    clock = normalize_period_clock(events)

    if tuple(p.period for p in clock) != source.available_periods:
        raise ValueError("Source periods differ from normalized event clock")

    reasons: list[str] = []
    data: PossessionData | AttackData

    if query.resource_id == "analysis.possession_fragments":
        fragments = build_possession_sequences(
            contexts,
            clock,
            provider=query.provider,
            match_id=str(query.match_id),
            events_sha256=source.events_sha256,
        )

        if fragments.issues:
            reasons.append("possession_source_issues")

        data = PossessionData(value=fragments)

    else:
        actions = normalize_pressures_and_fouls(
            events,
            match_id=query.match_id,
        )

        attacks = build_attack_sequences(
            contexts,
            normalize_event_play_metadata(events, match_id=query.match_id),
            normalize_passes(events, match_id=query.match_id),
            normalize_shots(events, match_id=query.match_id),
            [
                action
                for action in actions
                if isinstance(action, (MatchFoulCommitted, MatchFoulWon))
            ],
            clock,
            provider=query.provider,
            match_id=str(query.match_id),
            events_sha256=source.events_sha256,
        )

        if attacks.issues:
            reasons.append("possession_source_issues")

        if any(attack.status == "review" for attack in attacks.attacks):
            reasons.append("attack_boundary_issues")

        if any(stoppage.status == "review" for stoppage in attacks.stoppages):
            reasons.append("stoppage_issues")

        data = AttackData(value=attacks)

    return StructuredResult(
        query=query,
        source=source,
        status="review" if reasons else "available",
        reasons=tuple(reasons),
        data=data,
    )
