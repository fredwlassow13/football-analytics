import os
from contextlib import nullcontext
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.world_cup_teams import main
from football_analytics.integrations.base import ProviderUnavailable


def test_team_pipeline_audit_and_rollback() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database")
    engine = create_engine(url)
    identifier = uuid4().int % 100000000000
    name = f"Team {identifier}"
    payload = [
        {
            "home_team": {"home_team_id": identifier, "home_team_name": name},
            "away_team": {"away_team_id": identifier + 1, "away_team_name": f"Other {identifier}"},
        }
    ]
    try:
        for _ in range(2):
            with httpx.Client(
                base_url="https://test.invalid/",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
            ) as http:
                with patch(
                    "football_analytics.ingestion.world_cup_teams.get_engine", return_value=engine
                ):
                    with patch(
                        "football_analytics.ingestion.world_cup_teams.httpx.Client",
                        return_value=nullcontext(http),
                    ):
                        main()
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM core.teams WHERE name=:name"), {"name": name}
                ).scalar_one()
                == 1
            )
            runs_before = set(conn.execute(text("SELECT id FROM raw.ingestion_runs")).scalars())
        # Insert a new team before encountering a changed existing team: both must roll back.
        conflicting = [
            {
                "home_team": {
                    "home_team_id": identifier + 2,
                    "home_team_name": f"AAA {identifier}",
                },
                "away_team": {"away_team_id": identifier, "away_team_name": f"ZZZ {identifier}"},
            }
        ]
        with httpx.Client(
            base_url="https://test.invalid/",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=conflicting)),
        ) as http:
            with patch(
                "football_analytics.ingestion.world_cup_teams.get_engine", return_value=engine
            ):
                with patch(
                    "football_analytics.ingestion.world_cup_teams.httpx.Client",
                    return_value=nullcontext(http),
                ):
                    with pytest.raises(ValueError, match="Team name changed"):
                        main()
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM core.teams WHERE name=:name"),
                    {"name": f"AAA {identifier}"},
                ).scalar_one()
                == 0
            )
            run = next(
                r
                for r in conn.execute(text("SELECT id, status FROM raw.ingestion_runs"))
                if r.id not in runs_before
            )
            assert run.status == "failed"
            assert (
                conn.execute(
                    text("SELECT count(*) FROM raw.provider_payloads WHERE ingestion_run_id=:id"),
                    {"id": run.id},
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM raw.ingestion_errors WHERE ingestion_run_id=:id"),
                    {"id": run.id},
                ).scalar_one()
                == 1
            )
        with httpx.Client(
            base_url="https://test.invalid/",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"invalid json")
            ),
        ) as http:
            with patch(
                "football_analytics.ingestion.world_cup_teams.get_engine", return_value=engine
            ):
                with patch(
                    "football_analytics.ingestion.world_cup_teams.httpx.Client",
                    return_value=nullcontext(http),
                ):
                    with pytest.raises(ProviderUnavailable):
                        main()
    finally:
        engine.dispose()
