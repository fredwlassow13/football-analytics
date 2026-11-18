"""Full-match result export; validation reports remain separate."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import Connection, Engine, text

from football_analytics.analytics.catalogue import (
    CATALOGUE_VERSION,
    METRICS,
    PENDING_CAPABILITIES,
    RESOURCES,
)
from football_analytics.analytics.contracts import (
    Contract,
    MetricQuery,
    QueryResult,
    SourceReference,
)
from football_analytics.analytics.definitions import Group, MetricDefinition
from football_analytics.analytics.executor import execute_query, load_query_sources
from football_analytics.analytics.resources import PendingCapability
from football_analytics.analytics.structured_contracts import (
    MapOptions,
    StructuredQuery,
    StructuredResult,
)
from football_analytics.analytics.structured_executor import execute_structured_query

MAP_GROUPS = ("all", "recoveries", "duels", "pressure", "blocks_clearances", "fouls", "aerials")


class ExportPlayer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    player_id: int = Field(gt=0)
    player_name: str | None = None
    jersey_number: int | None = None


class ExportTeam(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    team_id: int = Field(gt=0)
    team_name: str | None = None
    lineup: list[ExportPlayer]


def metric_query(
    match_id: int, group: Group, definitions: tuple[MetricDefinition, ...]
) -> MetricQuery:
    return MetricQuery(
        match_ids=(match_id,),
        group_by=group,
        metrics=tuple(d.id for d in definitions if group in d.groups),
    )


def structured_queries(match_id: int, teams: tuple[str, ...]) -> tuple[StructuredQuery, ...]:
    queries = []
    for team in teams:
        queries.append(
            StructuredQuery(
                resource_id="analysis.pass_connections", match_id=match_id, team_id=team
            )
        )
        for group in MAP_GROUPS:
            queries.append(
                StructuredQuery(
                    resource_id="analysis.defensive_map",
                    match_id=match_id,
                    team_id=team,
                    map_options=MapOptions.model_validate({"group": group}),
                )
            )
    queries.extend(
        [
            StructuredQuery(resource_id="analysis.possession_fragments", match_id=match_id),
            StructuredQuery(resource_id="analysis.attacks", match_id=match_id),
            StructuredQuery(resource_id="analysis.tactical_windows", match_id=match_id),
        ]
    )
    return tuple(queries)


class MatchExport(Contract):
    export_version: Literal["1.0.0"] = "1.0.0"
    scope: Literal["shared_executor_full_match"] = "shared_executor_full_match"
    validation_report_included: Literal[False] = False
    export_id: UUID
    created_at: datetime
    catalogue_version: str
    match_id: int = Field(gt=0)
    source: SourceReference
    entities: tuple[ExportTeam, ...]
    definitions: tuple[MetricDefinition, ...]
    team_metrics: QueryResult
    player_metrics: QueryResult
    structured: tuple[StructuredResult, ...]
    pending: tuple[PendingCapability, ...]
    resources_outside_export: tuple[str, ...]

    @model_validator(mode="after")
    def check_export(self) -> Self:
        if self.created_at.utcoffset() is None:
            raise ValueError("Export timestamp requires a timezone")
        if self.source.match_id != self.match_id:
            raise ValueError("Export source belongs to another match")
        teams = tuple(str(t.team_id) for t in self.entities)
        if len(teams) != 2 or len(set(teams)) != 2:
            raise ValueError("Export requires two distinct teams")
        players = [(str(t.team_id), str(p.player_id)) for t in self.entities for p in t.lineup]
        if not players or len({p for _, p in players}) != len(players):
            raise ValueError("Export roster requires distinct players")
        ids = [d.id for d in self.definitions]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Export requires distinct metric definitions")
        numeric: tuple[tuple[Group, QueryResult], ...] = (
            ("team", self.team_metrics),
            ("player", self.player_metrics),
        )
        for group, result in numeric:
            if result.query != metric_query(self.match_id, group, self.definitions):
                raise ValueError("Export numeric query differs from declared profile")
            if result.sources != (self.source,):
                raise ValueError("Export numeric source differs")
            actual = {(r.team_id, r.player_id) for r in result.rows}
            expected = {(t, None) for t in teams} if group == "team" else set(players)
            if actual != expected:
                raise ValueError("Export numeric population differs from roster")
        expected_queries = structured_queries(self.match_id, teams)
        if tuple(r.query for r in self.structured) != expected_queries:
            raise ValueError("Export structured queries differ from declared profile")
        for structured_result in self.structured:
            if structured_result.source != self.source and not (
                structured_result.status == "unavailable" and structured_result.source is None
            ):
                raise ValueError("Export structured source differs")
        return self


def build_match_export(connection: Connection, match_id: int) -> MatchExport:
    """Caller manages the transaction; run_match_export provides the snapshot."""
    if match_id <= 0:
        raise ValueError("Match ID must be positive")
    source, _, lineups = load_query_sources(connection, match_id=match_id)
    entities = tuple(TypeAdapter(list[ExportTeam]).validate_python(lineups))
    definitions = tuple(d for d in METRICS if d.execution_available)
    team_metrics = execute_query(connection, metric_query(match_id, "team", definitions))
    player_metrics = execute_query(connection, metric_query(match_id, "player", definitions))
    queries = structured_queries(match_id, tuple(str(t.team_id) for t in entities))
    structured = tuple(execute_structured_query(connection, q) for q in queries)
    included = {q.resource_id for q in queries}
    return MatchExport(
        export_id=uuid4(),
        created_at=datetime.now(UTC),
        catalogue_version=CATALOGUE_VERSION,
        match_id=match_id,
        source=source,
        entities=entities,
        definitions=definitions,
        team_metrics=team_metrics,
        player_metrics=player_metrics,
        structured=structured,
        pending=PENDING_CAPABILITIES,
        resources_outside_export=tuple(r.id for r in RESOURCES if r.id not in included),
    )


def run_match_export(engine: Engine, match_id: int) -> MatchExport:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return build_match_export(connection, match_id)


def write_match_export(result: MatchExport, output: Path) -> None:
    """Validate again and publish a complete file, refusing existing paths."""
    checked = MatchExport.model_validate_json(result.model_dump_json())
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(checked.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    import argparse
    import json
    from collections import Counter

    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.match_id <= 0:
        parser.error("Match ID must be positive")
    if args.output.exists():
        parser.error("Output already exists; choose another path")
    engine = create_engine(args.database_url)
    try:
        result = run_match_export(engine, args.match_id)
        write_match_export(result, args.output)
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "match_id": result.match_id,
                "export_id": str(result.export_id),
                "metric_definitions": len(result.definitions),
                "team_rows": len(result.team_metrics.rows),
                "player_rows": len(result.player_metrics.rows),
                "structured_statuses": dict(Counter(r.status for r in result.structured)),
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
