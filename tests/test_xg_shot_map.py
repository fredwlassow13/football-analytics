import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchShot,
)
from football_analytics.visualization.shot_map import render_xg_shot_map


def make_shot(
    index: int,
    *,
    xg: float | None = 0.2,
    period: int = 1,
    team_id: str = "team-1",
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"shot-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "event_type": "Shot",
        "external_team_id": team_id,
        "external_player_id": "player-1",
    }

    return ContextualizedAction(
        action=MatchShot.model_validate(header | {"outcome": "goal", "statsbomb_xg": xg}),
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": 108.1, "y": 40.1},
            }
        ),
    )


@pytest.mark.parametrize("extension", ["png", "svg"])
def test_exports_map_and_excludes_shootout(
    tmp_path: Path,
    extension: str,
) -> None:
    output = tmp_path / f"shots.{extension}"

    result = render_xg_shot_map(
        [
            make_shot(1),
            make_shot(2, team_id="team-2"),
            make_shot(3, period=5, xg=None),
        ],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Final de 2022",
    )

    assert result == output

    if extension == "png":
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    else:
        assert ET.parse(output).getroot().tag.endswith("svg")


def test_exports_observed_zero_xg(tmp_path: Path) -> None:
    output = tmp_path / "zero.png"

    render_xg_shot_map(
        [make_shot(1, xg=0.0)],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="xG zero observado",
    )

    assert output.is_file()


def test_rejects_missing_xg_before_writing_file(tmp_path: Path) -> None:
    output = tmp_path / "missing.png"

    with pytest.raises(ValueError, match="observed xG"):
        render_xg_shot_map(
            [make_shot(1, xg=None)],
            team_names={"team-1": "Argentina", "team-2": "França"},
            output_path=output,
            title="Final de 2022",
        )

    assert not output.exists()


def test_rejects_unknown_team(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown team"):
        render_xg_shot_map(
            [make_shot(1, team_id="another-team")],
            team_names={"team-1": "Argentina", "team-2": "França"},
            output_path=tmp_path / "shots.png",
            title="Final de 2022",
        )


def test_rejects_unsupported_output_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PNG or SVG"):
        render_xg_shot_map(
            [make_shot(1)],
            team_names={"team-1": "Argentina", "team-2": "França"},
            output_path=tmp_path / "shots.txt",
            title="Final de 2022",
        )


def test_marks_coincident_shots_and_excludes_shootout(tmp_path: Path) -> None:
    output = tmp_path / "coincident.svg"

    render_xg_shot_map(
        [
            make_shot(1),
            make_shot(2),
            make_shot(3, period=5),
        ],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Finalizações coincidentes",
    )

    svg = output.read_text(encoding="utf-8")
    assert "×2" in svg
    assert "×3" not in svg


def test_does_not_combine_coordinates_from_different_teams(tmp_path: Path) -> None:
    output = tmp_path / "separate_teams.svg"

    render_xg_shot_map(
        [
            make_shot(1),
            make_shot(2, team_id="team-2"),
        ],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Equipes separadas",
    )

    assert "×2" not in output.read_text(encoding="utf-8")


def test_does_not_round_nearby_coordinates_into_coincidence(tmp_path: Path) -> None:
    output = tmp_path / "nearby.svg"
    second = make_shot(2)
    data = second.spatial.model_dump()
    data["start_location"] = {"x": 108.100001, "y": 40.1}
    second.spatial = MatchEventSpatial.model_validate(data)

    render_xg_shot_map(
        [make_shot(1), second],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Coordenadas próximas",
    )

    assert "×2" not in output.read_text(encoding="utf-8")
