from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from football_analytics.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness() -> None:
    with patch("football_analytics.main.get_engine") as engine:
        connection = MagicMock()
        engine.return_value.connect.return_value.__enter__.return_value = connection
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    connection.execute.assert_called_once()


def test_readiness_database_unavailable() -> None:
    with patch("football_analytics.main.get_engine") as engine:
        engine.return_value.connect.side_effect = OperationalError("SELECT 1", {}, Exception())
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_competition_endpoints_report_database_failure() -> None:
    with patch("football_analytics.modules.competitions.router.get_engine") as engine:
        engine.return_value.connect.side_effect = OperationalError("SELECT", {}, Exception())
        response = client.get("/api/v1/competitions")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
