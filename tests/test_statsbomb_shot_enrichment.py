import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.shots import normalize_shots


def shot_details() -> dict[str, object]:
    return {
        "outcome": {"name": "Goal"},
        "body_part": {"id": 38, "name": "Left Foot"},
        "technique": {"id": 95, "name": "Volley"},
        "type": {"id": 87, "name": "Open Play"},
        "statsbomb_xg": 0.25,
    }


def source_shot(details: dict[str, object]) -> dict[str, object]:
    return {
        "id": "shot-1",
        "index": 100,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": "Shot"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "shot": details,
    }


def test_preserves_enriched_shot_attributes() -> None:
    result = normalize_shots(
        [source_shot(shot_details())],
        match_id=3869685,
    )

    shot = result[0]
    assert shot.outcome == "goal"
    assert shot.body_part == "left_foot"
    assert shot.technique == "Volley"
    assert shot.shot_type == "Open Play"
    assert shot.statsbomb_xg == 0.25


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("Left Foot", "left_foot"),
        ("Right Foot", "right_foot"),
        ("Head", "head"),
        ("Other", "other"),
    ],
)
def test_translates_body_part(source_name: str, expected: str) -> None:
    details = shot_details()
    details["body_part"] = {"name": source_name}

    result = normalize_shots(
        [source_shot(details)],
        match_id=3869685,
    )

    assert result[0].body_part == expected


def test_preserves_missing_optional_attributes() -> None:
    result = normalize_shots(
        [source_shot({"outcome": {"name": "Goal"}})],
        match_id=3869685,
    )

    shot = result[0]
    assert shot.body_part is None
    assert shot.technique is None
    assert shot.shot_type is None
    assert shot.statsbomb_xg is None


@pytest.mark.parametrize(
    ("source_field", "internal_field"),
    [
        ("body_part", "body_part"),
        ("technique", "technique"),
        ("type", "shot_type"),
        ("statsbomb_xg", "statsbomb_xg"),
    ],
)
def test_preserves_explicit_null_attribute(
    source_field: str,
    internal_field: str,
) -> None:
    details = shot_details()
    details[source_field] = None

    result = normalize_shots(
        [source_shot(details)],
        match_id=3869685,
    )

    assert getattr(result[0], internal_field) is None


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
def test_rejects_invalid_source_xg(xg: object) -> None:
    details = shot_details()
    details["statsbomb_xg"] = xg

    with pytest.raises(ProviderUnavailable, match="Invalid shot details"):
        normalize_shots([source_shot(details)], match_id=3869685)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("body_part", {"name": "Unknown"}),
        ("body_part", {}),
        ("technique", {}),
        ("type", {}),
        ("technique", {"name": ""}),
        ("type", {"name": ""}),
    ],
)
def test_rejects_invalid_attribute_reference(field: str, value: object) -> None:
    details = shot_details()
    details[field] = value

    with pytest.raises(ProviderUnavailable, match="Invalid shot details"):
        normalize_shots([source_shot(details)], match_id=3869685)
