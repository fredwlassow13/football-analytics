import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchShot


def shot_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "shot-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "event_type": "Shot",
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "goal",
    }


def test_preserves_enriched_attributes() -> None:
    data = shot_data()
    data.update(
        body_part="left_foot",
        technique="Volley",
        shot_type="Open Play",
        statsbomb_xg=0.25,
    )

    shot = MatchShot.model_validate(data)

    assert shot.body_part == "left_foot"
    assert shot.technique == "Volley"
    assert shot.shot_type == "Open Play"
    assert shot.statsbomb_xg == 0.25
    assert shot.is_goal is True
    assert shot.is_on_target is True


def test_preserves_missing_attributes() -> None:
    shot = MatchShot.model_validate(shot_data())

    assert shot.body_part is None
    assert shot.technique is None
    assert shot.shot_type is None
    assert shot.statsbomb_xg is None


@pytest.mark.parametrize("xg", [0.0, 0.25, 1.0])
def test_accepts_probability_boundaries(xg: float) -> None:
    data = shot_data()
    data["statsbomb_xg"] = xg

    shot = MatchShot.model_validate(data)

    assert shot.statsbomb_xg == xg
    assert shot.statsbomb_xg is not None


@pytest.mark.parametrize(
    "xg",
    [
        -0.01,
        1.01,
        float("nan"),
        float("inf"),
        float("-inf"),
        "0.25",
        True,
    ],
)
def test_rejects_invalid_xg(xg: object) -> None:
    data = shot_data()
    data["statsbomb_xg"] = xg

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)


@pytest.mark.parametrize(
    "body_part",
    ["left_foot", "right_foot", "head", "other"],
)
def test_accepts_body_part_categories(body_part: str) -> None:
    data = shot_data()
    data["body_part"] = body_part

    shot = MatchShot.model_validate(data)

    assert shot.body_part == body_part


@pytest.mark.parametrize("body_part", ["unknown", "", True])
def test_rejects_invalid_body_part(body_part: object) -> None:
    data = shot_data()
    data["body_part"] = body_part

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)


@pytest.mark.parametrize("field", ["technique", "shot_type"])
def test_rejects_empty_attribute_name(field: str) -> None:
    data = shot_data()
    data[field] = ""

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)
