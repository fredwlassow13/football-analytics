"""Resumable full-match export with an index; no files are overwritten."""

import hashlib
import os
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import Connection, text

from football_analytics.analytics.catalogue import (
    CATALOGUE_VERSION,
    METRICS,
    PENDING_CAPABILITIES,
    RESOURCES,
)
from football_analytics.analytics.contracts import Contract, SourceReference
from football_analytics.analytics.executor import load_query_sources
from football_analytics.analytics.match_export import (
    MatchExport,
    build_match_export,
    write_match_export,
)

MATCHES_SQL = """
SELECT mm.external_id AS match_id
FROM core.matches m
JOIN core.seasons s ON s.id=m.season_id
JOIN core.external_id_mappings mm ON mm.match_id=m.id
    AND mm.provider='statsbomb' AND mm.entity_type='match'
JOIN core.external_id_mappings cm ON cm.competition_id=s.competition_id
    AND cm.provider='statsbomb' AND cm.entity_type='competition'
JOIN core.external_id_mappings sm ON sm.season_id=s.id
    AND sm.provider='statsbomb' AND sm.entity_type='season'
WHERE cm.external_id=:competition AND sm.external_id=:season
ORDER BY m.match_date,mm.external_id
"""


class ExportedItem(Contract):
    match_id: int = Field(gt=0)
    status: Literal["created", "reused"]
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    export_id: UUID
    source: SourceReference
    team_names: tuple[str | None, ...]
    metric_definitions: int = Field(gt=0)
    player_rows: int = Field(gt=0)
    structured_statuses: dict[str, int]

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        if self.file != f"{self.match_id}.json" or self.source.match_id != self.match_id:
            raise ValueError("Index file/source differs from match identity")
        if len(self.team_names) != 2:
            raise ValueError("Index entry requires two teams")
        if set(self.structured_statuses) != {"available", "review", "unavailable"} or any(
            count < 0 for count in self.structured_statuses.values()
        ):
            raise ValueError("Index structured statuses require nonnegative counts")
        return self


class FailedItem(Contract):
    match_id: int = Field(gt=0)
    status: Literal["failed"] = "failed"
    error_type: str
    error: str


BatchItem = Annotated[ExportedItem | FailedItem, Field(discriminator="status")]


class ExportIndex(Contract):
    index_version: Literal["1.0.0"] = "1.0.0"
    scope: Literal["shared_executor_full_match"] = "shared_executor_full_match"
    validation_report_included: Literal[False] = False
    batch_id: UUID
    created_at: datetime
    catalogue_version: str
    requested_match_ids: tuple[int, ...]
    items: tuple[BatchItem, ...]

    @model_validator(mode="after")
    def check_items(self) -> Self:
        ids = self.requested_match_ids
        if not ids or any(i <= 0 for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Index requires distinct positive match IDs")
        if tuple(item.match_id for item in self.items) != ids:
            raise ValueError("Index must record every requested match in order")
        if self.created_at.utcoffset() is None:
            raise ValueError("Index timestamp requires a timezone")
        return self


def select_match_ids(
    connection: Connection,
    *,
    competition_id: int,
    season_id: int,
    expected_matches: int,
) -> tuple[int, ...]:
    if min(competition_id, season_id, expected_matches) <= 0:
        raise ValueError("Competition, season and expected count must be positive")
    rows = (
        connection.execute(
            text(MATCHES_SQL),
            {"competition": str(competition_id), "season": f"{competition_id}:{season_id}"},
        )
        .mappings()
        .all()
    )
    ids = tuple(int(row["match_id"]) for row in rows)
    if len(ids) != expected_matches or len(ids) != len(set(ids)) or any(i <= 0 for i in ids):
        raise ValueError("Stored match selection differs from expected count or identity")
    return ids


def _check_current(result: MatchExport, match_id: int, source: SourceReference) -> None:
    if result.match_id != match_id or result.source != source:
        raise ValueError("Existing export differs from the current match/source; use a new folder")
    definitions = tuple(d for d in METRICS if d.execution_available)
    included = {r.query.resource_id for r in result.structured}
    outside = tuple(r.id for r in RESOURCES if r.id not in included)
    if (
        result.catalogue_version != CATALOGUE_VERSION
        or result.definitions != definitions
        or result.pending != PENDING_CAPABILITIES
        or result.resources_outside_export != outside
    ):
        raise ValueError("Export differs from the current catalogue/profile; use a new folder")


def export_match_batch(
    connection: Connection,
    match_ids: Sequence[int],
    output_dir: Path,
    *,
    progress: Callable[[int, int, BatchItem], None] | None = None,
) -> ExportIndex:
    """Caller owns the read-only snapshot; each match uses its own savepoint."""
    ids = tuple(match_ids)
    if not ids or any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("Provide distinct positive integer match IDs")
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[BatchItem] = []
    for number, match_id in enumerate(ids, 1):
        path = output_dir / f"{match_id}.json"
        item: BatchItem
        try:
            with connection.begin_nested():
                source, _, _ = load_query_sources(connection, match_id=match_id)
                existing = path.exists()
                if existing:
                    raw = path.read_bytes()
                    result = MatchExport.model_validate_json(raw)
                else:
                    result = build_match_export(connection, match_id)
                _check_current(result, match_id, source)
            if not existing:
                write_match_export(result, path)
                raw = path.read_bytes()
            item = ExportedItem(
                match_id=match_id,
                status="reused" if existing else "created",
                file=path.name,
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                export_id=result.export_id,
                source=result.source,
                team_names=tuple(team.team_name for team in result.entities),
                metric_definitions=len(result.definitions),
                player_rows=len(result.player_metrics.rows),
                structured_statuses={
                    status: sum(r.status == status for r in result.structured)
                    for status in ("available", "review", "unavailable")
                },
            )
        except Exception as exc:
            item = FailedItem(
                match_id=match_id, error_type=type(exc).__name__, error=str(exc)[:2000]
            )
        items.append(item)
        if progress is not None:
            progress(number, len(ids), item)
    return ExportIndex(
        batch_id=uuid4(),
        created_at=datetime.now(UTC),
        catalogue_version=CATALOGUE_VERSION,
        requested_match_ids=ids,
        items=tuple(items),
    )


def write_export_index(index: ExportIndex, output: Path) -> None:
    """Publish the complete index without replacing any existing file."""
    checked = ExportIndex.model_validate_json(index.model_dump_json())
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
    parser.add_argument("--competition-id", type=int, required=True)
    parser.add_argument("--season-id", type=int, required=True)
    parser.add_argument("--expected-matches", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--index-name", default="index.json")
    args = parser.parse_args()
    name = args.index_name
    if Path(name).name != name or not name.startswith("index") or not name.endswith(".json"):
        parser.error("Index name must be a filename starting with index and ending with .json")
    if min(args.competition_id, args.season_id, args.expected_matches) <= 0:
        parser.error("Competition, season and expected count must be positive")
    index_path = args.output_dir / name
    if index_path.exists():
        parser.error("Index already exists; select another --index-name")

    def progress(number: int, total: int, item: BatchItem) -> None:
        print(f"Processed {number}/{total}: {item.match_id} {item.status}", flush=True)

    engine = create_engine(args.database_url)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                ids = select_match_ids(
                    connection,
                    competition_id=args.competition_id,
                    season_id=args.season_id,
                    expected_matches=args.expected_matches,
                )
                index = export_match_batch(connection, ids, args.output_dir, progress=progress)
        write_export_index(index, index_path)
    finally:
        engine.dispose()
    counts = Counter(item.status for item in index.items)
    print(
        json.dumps(
            {
                "batch_id": str(index.batch_id),
                "requested": len(index.requested_match_ids),
                "created": counts["created"],
                "reused": counts["reused"],
                "failed": counts["failed"],
                "result_bytes": sum(
                    item.bytes for item in index.items if isinstance(item, ExportedItem)
                ),
                "index": str(index_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
