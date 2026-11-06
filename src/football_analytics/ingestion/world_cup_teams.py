import hashlib
import json
from uuid import uuid4

import httpx
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine
from football_analytics.infrastructure.settings import get_settings
from football_analytics.ingestion.teams import persist_teams
from football_analytics.integrations.statsbomb.client import StatsBombClient
from football_analytics.integrations.statsbomb.teams import normalize_teams


def main() -> None:
    engine = get_engine()
    run_id = uuid4()

    # 1. Registra o início da execução.
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO raw.ingestion_runs (
                    id, provider, pipeline_name, status
                )
                VALUES (
                    :id, 'statsbomb', 'world_cup_2022_teams', 'running'
                )
            """),
            {"id": run_id},
        )

    try:
        # 2. Busca as partidas na StatsBomb.
        with httpx.Client(
            base_url=get_settings().statsbomb_base_url,
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=True,
        ) as http:
            response = StatsBombClient(http).fetch_matches(43, 106)

        try:
            payload = response.json()
        except ValueError:
            payload = None

        # 3. Preserva a resposta antes de validar os dados.
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO raw.provider_payloads (
                        id,
                        provider,
                        endpoint,
                        payload,
                        raw_body,
                        sha256,
                        ingestion_run_id
                    )
                    VALUES (
                        :id,
                        'statsbomb',
                        :endpoint,
                        CAST(:payload AS jsonb),
                        :raw_body,
                        :sha256,
                        :run_id
                    )
                """),
                {
                    "id": uuid4(),
                    "endpoint": str(response.url),
                    "payload": json.dumps(payload),
                    "raw_body": response.text,
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "run_id": run_id,
                },
            )

        # 4. Valida e extrai as equipes sem repetições.
        teams = normalize_teams(payload)

        if not teams:
            raise ValueError("No teams found in the matches payload")

        # 5. Grava equipes, mapeamentos e conclusão juntos.
        with engine.begin() as connection:
            created = persist_teams(connection, teams)

            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'success',
                        finished_at = now(),
                        records_processed = :processed
                    WHERE id = :run_id
                """),
                {
                    "run_id": run_id,
                    "processed": len(teams),
                },
            )

    except Exception as exc:
        # 6. Se falhar, registra o erro separadamente.
        with engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'failed',
                        finished_at = now(),
                        records_failed = 1
                    WHERE id = :run_id
                """),
                {"run_id": run_id},
            )

            connection.execute(
                text("""
                    INSERT INTO raw.ingestion_errors (
                        id, ingestion_run_id, message
                    )
                    VALUES (:id, :run_id, :message)
                """),
                {
                    "id": uuid4(),
                    "run_id": run_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )

        raise

    print(f"Execução: {run_id}")
    print(f"Equipes processadas: {len(teams)}")
    print(f"Equipes criadas: {created}")
    print(f"Equipes já existentes: {len(teams) - created}")


if __name__ == "__main__":
    main()
