from typing import Literal

from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping


def load_latest_match_payload(
    connection: Connection,
    *,
    match_id: int,
    kind: Literal["events", "lineups"],
) -> RowMapping:
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("Match ID must be a positive integer")

    if kind not in {"events", "lineups"}:
        raise ValueError("Unsupported match payload kind")

    row = (
        connection.execute(
            text("""
            SELECT id, endpoint, payload, sha256
            FROM raw.provider_payloads
            WHERE provider = 'statsbomb'
              AND endpoint LIKE :endpoint
            ORDER BY fetched_at DESC, id DESC
            LIMIT 1
        """),
            {"endpoint": f"%/{kind}/{match_id}.json"},
        )
        .mappings()
        .one_or_none()
    )

    if row is None:
        raise ValueError(f"Stored {kind} source not found for match {match_id}")

    return row
