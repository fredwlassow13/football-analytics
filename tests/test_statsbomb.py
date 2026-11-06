from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.client import StatsBombClient
from football_analytics.main import app
from football_analytics.modules.providers.router import get_provider

ENTRY = {
    "competition_id": 9,
    "season_id": 281,
    "competition_name": "Bundesliga",
    "season_name": "2023/2024",
    "country_name": "Germany",
    "competition_gender": "male",
    "match_available_360": None,
}


def test_adapter() -> None:
    with httpx.Client(
        base_url="https://example.test/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[ENTRY])),
    ) as http:
        item = StatsBombClient(http).list_competitions()[0]
    assert item.external_competition_id == "9"
    assert item.external_season_id == "281"
    assert item.provider == "statsbomb"
    assert item.competition_name == "Bundesliga"


@pytest.mark.parametrize("payload", [{}, [{"competition_id": "invalid"}], None])
def test_invalid_contract(payload: object) -> None:
    with (
        httpx.Client(
            base_url="https://example.test/",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as http,
        pytest.raises(ProviderUnavailable),
    ):
        StatsBombClient(http).list_competitions()


@pytest.mark.parametrize("status, calls", [(404, 1), (500, 3), (429, 3)])
def test_retry_policy(status: int, calls: int) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status)

    with httpx.Client(
        base_url="https://example.test/", transport=httpx.MockTransport(handle)
    ) as http:
        with (
            patch("football_analytics.integrations.statsbomb.client.time.sleep"),
            pytest.raises(ProviderUnavailable),
        ):
            StatsBombClient(http).list_competitions()
    assert len(requests) == calls


def test_retry_recovers_after_timeout() -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=[ENTRY])

    with httpx.Client(
        base_url="https://example.test/", transport=httpx.MockTransport(handle)
    ) as http:
        with patch("football_analytics.integrations.statsbomb.client.time.sleep"):
            assert len(StatsBombClient(http).list_competitions()) == 1
    assert attempts == 2


def test_provider_endpoint_filters_and_paginates() -> None:
    with httpx.Client(
        base_url="https://example.test/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=[ENTRY, {**ENTRY, "season_id": 27, "season_name": "2015/2016"}]
            )
        ),
    ) as http:
        app.dependency_overrides[get_provider] = lambda: StatsBombClient(http)
        try:
            client = TestClient(app)
            response = client.get(
                "/api/v1/providers/statsbomb/competitions?country=germany&limit=1&offset=1"
            )
            assert response.status_code == 200
            assert response.json()["total"] == 2
            assert response.json()["items"][0]["external_season_id"] == "281"
            assert client.get("/api/v1/providers/statsbomb/competitions?limit=0").status_code == 422
            assert (
                client.get("/api/v1/providers/statsbomb/competitions?country=Brazil").json()[
                    "items"
                ]
                == []
            )
        finally:
            app.dependency_overrides.clear()


def test_provider_failure_is_502() -> None:
    with httpx.Client(
        base_url="https://example.test/",
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    ) as http:
        app.dependency_overrides[get_provider] = lambda: StatsBombClient(http)
        try:
            assert (
                TestClient(app).get("/api/v1/providers/statsbomb/competitions").status_code == 502
            )
        finally:
            app.dependency_overrides.clear()
