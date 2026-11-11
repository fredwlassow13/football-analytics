import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.passes import normalize_passes


def source_pass(details: dict[str, object]) -> dict[str, object]:
    return {
        "id": "pass-1",
        "index": 100,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": "Pass"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "pass": details,
    }


def test_preserves_recipient_and_pass_type() -> None:
    result = normalize_passes(
        [
            source_pass(
                {
                    "recipient": {"id": 3090, "name": "Recipient"},
                    "type": {"id": 61, "name": "Corner"},
                }
            )
        ],
        match_id=3869685,
    )

    assert result[0].external_recipient_player_id == "3090"
    assert result[0].pass_type == "Corner"
    assert result[0].is_completed is True


def test_preserves_missing_attributes() -> None:
    result = normalize_passes(
        [source_pass({})],
        match_id=3869685,
    )

    assert result[0].external_recipient_player_id is None
    assert result[0].pass_type is None


def test_recipient_does_not_imply_completed_pass() -> None:
    result = normalize_passes(
        [
            source_pass(
                {
                    "recipient": {"id": 3090},
                    "outcome": {"name": "Incomplete"},
                }
            )
        ],
        match_id=3869685,
    )

    assert result[0].external_recipient_player_id == "3090"
    assert result[0].outcome == "incomplete"
    assert result[0].is_completed is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient", {}),
        ("recipient", {"id": 0}),
        ("recipient", {"id": "3090"}),
        ("type", {}),
        ("type", {"name": ""}),
        ("type", {"name": 1}),
    ],
)
def test_rejects_invalid_references(field: str, value: object) -> None:
    with pytest.raises(ProviderUnavailable, match="Invalid pass details"):
        normalize_passes(
            [source_pass({field: value})],
            match_id=3869685,
        )


@pytest.mark.parametrize(
    ("source_field", "internal_field"),
    [
        ("recipient", "external_recipient_player_id"),
        ("type", "pass_type"),
    ],
)
def test_preserves_explicit_null_attribute(
    source_field: str,
    internal_field: str,
) -> None:
    result = normalize_passes(
        [source_pass({source_field: None})],
        match_id=3869685,
    )

    assert getattr(result[0], internal_field) is None
