"""Prepare metric observations from verified export files, without a database."""

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from football_analytics.analytics.executor import combine_values
from football_analytics.analytics.match_export import MatchExport
from football_analytics.analytics.match_export_batch import ExportedItem, ExportIndex
from football_analytics.visualization.metric_plot_contracts import (
    ExcludedObservation,
    PlotObservation,
    PlotRequest,
    PlotSource,
    PreparedPlot,
)


def select_observations(
    observations: Sequence[PlotObservation],
    request: PlotRequest,
) -> tuple[tuple[PlotObservation, ...], tuple[ExcludedObservation, ...], int]:
    """Aggregate components before excluding unknowns; never average percentages."""
    rows = list(observations)
    if any(tuple(v.metric_id for v in row.values) != request.metric_ids for row in rows):
        raise ValueError("Observation metrics differ from plot request")
    if request.aggregation == "selected_matches":
        groups: dict[tuple[str, str | None], list[PlotObservation]] = defaultdict(list)
        for row in rows:
            groups[row.team_id, row.player_id].append(row)
        rows = []
        for group in groups.values():
            first = group[0]
            matches = tuple(sorted(mid for row in group for mid in row.match_ids))
            if len(matches) != len(set(matches)):
                raise ValueError("Duplicate entity/match observation")
            values = tuple(
                combine_values([next(v for v in row.values if v.metric_id == mid) for row in group])
                for mid in request.metric_ids
            )
            rows.append(first.model_copy(update={"match_ids": matches, "values": values}))
    else:
        keys = [(r.match_ids, r.team_id, r.player_id) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate entity/match observation")

    points = []
    excluded = []
    for row in rows:
        reasons = tuple(
            f"{value.metric_id}:{value.status}"
            for value in row.values
            if value.status != "available" or value.value is None
        )
        if request.review_policy == "exclude":
            reasons += tuple(f"{v.metric_id}:review" for v in row.values if v.review_reasons)
        if reasons:
            excluded.append(ExcludedObservation(observation=row, reasons=reasons))
        else:
            points.append(row)
    omitted = 0
    if request.kind == "bar":
        points.sort(
            key=lambda row: (
                -Decimal(row.values[0].value or 0),
                row.team_id,
                row.player_id or "",
                row.match_ids,
            )
        )
        if request.top_n is not None:
            omitted = max(0, len(points) - request.top_n)
            points = points[: request.top_n]
    return tuple(points), tuple(excluded), omitted


def prepare_metric_plot(index_path: Path, request: PlotRequest) -> PreparedPlot:
    request = PlotRequest.model_validate_json(request.model_dump_json())
    raw_index = index_path.read_bytes()
    index = ExportIndex.model_validate_json(raw_index)
    wanted = set(request.match_ids or index.requested_match_ids)
    if not wanted.issubset(index.requested_match_ids):
        raise ValueError("Requested match is outside export index")
    observations = []
    sources = []
    definitions = None
    seen_teams: set[str] = set()
    seen_players: set[str] = set()
    for item in index.items:
        if item.match_id not in wanted:
            continue
        if not isinstance(item, ExportedItem):
            raise ValueError(f"Requested match export failed: {item.match_id}")
        raw = (index_path.parent / item.file).read_bytes()
        if len(raw) != item.bytes or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"Export file hash/size differs from index: {item.match_id}")
        result = MatchExport.model_validate_json(raw)
        if (result.match_id, result.export_id, result.source, result.catalogue_version) != (
            item.match_id,
            item.export_id,
            item.source,
            index.catalogue_version,
        ):
            raise ValueError("Export identity/source/catalogue differs from index")
        selected = tuple(
            next(d for d in result.definitions if d.id == mid) for mid in request.metric_ids
        )
        if definitions is None:
            definitions = selected
        elif definitions != selected:
            raise ValueError("Selected metric definitions differ between exports")
        sources.append(
            PlotSource(
                match_id=item.match_id, export_id=item.export_id, file=item.file, sha256=item.sha256
            )
        )
        teams = {str(t.team_id): t.team_name or str(t.team_id) for t in result.entities}
        players = {
            str(p.player_id): p.player_name or str(p.player_id)
            for t in result.entities
            for p in t.lineup
        }
        rows = (
            result.team_metrics.rows if request.group_by == "team" else result.player_metrics.rows
        )
        seen_teams.update(teams)
        seen_players.update(players)
        for row in rows:
            if request.team_ids and row.team_id not in request.team_ids:
                continue
            if request.player_ids and row.player_id not in request.player_ids:
                continue
            name = teams[row.team_id]
            label = name if row.player_id is None else f"{players[row.player_id]} · {name}"
            observations.append(
                PlotObservation(
                    match_ids=row.match_ids,
                    team_id=row.team_id,
                    team_name=name,
                    player_id=row.player_id,
                    label=label,
                    values=tuple(
                        next(v for v in row.values if v.metric_id == mid)
                        for mid in request.metric_ids
                    ),
                )
            )
    if not set(request.team_ids).issubset(seen_teams) or not set(request.player_ids).issubset(
        seen_players
    ):
        raise ValueError("Requested entity is outside selected match rosters")
    if not observations or definitions is None:
        raise ValueError("No observations match the plot selection")
    points, excluded, omitted = select_observations(observations, request)
    return PreparedPlot(
        request=request,
        batch_id=index.batch_id,
        index_sha256=hashlib.sha256(raw_index).hexdigest(),
        sources=tuple(sources),
        definitions=definitions,
        population_count=len(points) + len(excluded) + omitted,
        points=points,
        excluded=excluded,
        omitted_by_top_n=omitted,
    )
