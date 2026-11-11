from pathlib import Path

import pytest

from football_analytics.ingestion.pass_network import PassConnection
from football_analytics.ingestion.pass_network_nodes import PassNetworkNode
from football_analytics.visualization.pass_network import render_pass_network

SHIRT_NUMBERS = {
    ("team-1", "player-a"): 7,
    ("team-1", "player-b"): 19,
}


def make_nodes() -> list[PassNetworkNode]:
    return [
        PassNetworkNode(
            provider="statsbomb",
            external_match_id="match-1",
            external_team_id="team-1",
            external_player_id=player_id,
            mean_x=x,
            mean_y=y,
            external_event_ids=tuple(f"pass-{index}" for index in indexes),
        )
        for player_id, x, y, indexes in [
            ("player-a", 30.0, 20.0, range(1, 11)),
            ("player-b", 70.0, 60.0, range(11, 21)),
        ]
    ]


def make_connections() -> list[PassConnection]:
    return [
        PassConnection(
            provider="statsbomb",
            external_match_id="match-1",
            external_team_id="team-1",
            external_sender_player_id=sender,
            external_recipient_player_id=recipient,
            external_event_ids=tuple(f"pass-{index}" for index in indexes),
        )
        for sender, recipient, indexes in [
            ("player-a", "player-b", range(1, 7)),
            ("player-b", "player-a", range(11, 13)),
        ]
    ]


@pytest.mark.parametrize("extension", ["png", "svg"])
def test_exports_network_with_explicit_display_coverage(
    tmp_path: Path,
    extension: str,
) -> None:
    output = tmp_path / f"network.{extension}"

    render_pass_network(
        make_nodes(),
        make_connections(),
        team_names={"team-1": "Argentina", "team-2": "França"},
        player_names={"player-a": "Jogador A", "player-b": "Jogador B"},
        shirt_numbers=SHIRT_NUMBERS,
        output_path=output,
        title="Rede de passes",
        window_label="Trecho de teste: início até primeira substituição",
    )

    if extension == "png":
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    else:
        svg = output.read_text(encoding="utf-8")
        assert "Trecho de teste" in svg
        assert "1/2 conexões" in svg
        assert "6/8 passes completos" in svg


@pytest.mark.parametrize("minimum", [0, -1, True])
def test_rejects_invalid_display_threshold(
    tmp_path: Path,
    minimum: int,
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        render_pass_network(
            make_nodes(),
            make_connections(),
            team_names={"team-1": "Argentina", "team-2": "França"},
            player_names={},
            output_path=tmp_path / "network.png",
            title="Rede de passes",
            min_completed_passes=minimum,
            shirt_numbers=SHIRT_NUMBERS,
        )


def test_rejects_connection_without_node(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing node"):
        render_pass_network(
            make_nodes()[:1],
            make_connections(),
            team_names={"team-1": "Argentina", "team-2": "França"},
            player_names={},
            output_path=tmp_path / "network.png",
            title="Rede de passes",
            shirt_numbers=SHIRT_NUMBERS,
        )


def test_rejects_coincident_connected_nodes(tmp_path: Path) -> None:
    nodes = make_nodes()
    data = nodes[1].model_dump()
    data.update(mean_x=nodes[0].mean_x, mean_y=nodes[0].mean_y)
    nodes[1] = PassNetworkNode.model_validate(data)

    with pytest.raises(ValueError, match="layout review"):
        render_pass_network(
            nodes,
            make_connections(),
            team_names={"team-1": "Argentina", "team-2": "França"},
            player_names={},
            output_path=tmp_path / "network.png",
            title="Rede de passes",
            shirt_numbers=SHIRT_NUMBERS,
        )


@pytest.mark.parametrize(
    "numbers",
    [
        {("team-1", "player-a"): 7},
        {
            ("team-1", "player-a"): 7,
            ("team-1", "player-b"): 7,
        },
    ],
)
def test_rejects_missing_or_duplicate_shirts(
    tmp_path: Path,
    numbers: dict[tuple[str, str], int],
) -> None:
    with pytest.raises(ValueError, match="shirt number"):
        render_pass_network(
            make_nodes(),
            make_connections(),
            team_names={"team-1": "Argentina", "team-2": "França"},
            player_names={},
            shirt_numbers=numbers,
            output_path=tmp_path / "network.png",
            title="Rede de passes",
        )


def test_keeps_nominal_player_without_pass_origins(tmp_path: Path) -> None:
    output = tmp_path / "nominal_roster.svg"

    render_pass_network(
        make_nodes(),
        make_connections(),
        team_names={"team-1": "Argentina", "team-2": "França"},
        player_names={
            "player-a": "Jogador A",
            "player-b": "Jogador B",
            "player-c": "Reserva sem passes",
            "player-d": "Jogador D",
        },
        shirt_numbers={
            **SHIRT_NUMBERS,
            ("team-1", "player-c"): 10,
            ("team-2", "player-d"): 1,
        },
        nominal_player_ids={
            "team-1": ("player-a", "player-b", "player-c"),
            "team-2": ("player-d",),
        },
        output_path=output,
        title="Rede por trecho",
        window_label="P1 · trecho 1",
    )

    svg = output.read_text(encoding="utf-8")
    assert "Reserva sem passes" in svg
    assert "Argentina · 3 jogadores na tabela" in svg
    assert "2 com posição média" in svg
    assert "Jogadores sem passes aparecem na tabela" in svg


def test_counts_connections_with_unpositioned_recipient(tmp_path: Path) -> None:
    output = tmp_path / "unpositioned.svg"

    render_pass_network(
        make_nodes()[:1],
        make_connections()[:1],
        team_names={"team-1": "Argentina", "team-2": "França"},
        player_names={
            "player-a": "Jogador A",
            "player-b": "Destinatário sem posição",
            "player-d": "Jogador D",
        },
        shirt_numbers={
            **SHIRT_NUMBERS,
            ("team-2", "player-d"): 1,
        },
        nominal_player_ids={
            "team-1": ("player-a", "player-b"),
            "team-2": ("player-d",),
        },
        output_path=output,
        title="Rede por trecho",
        window_label="P1 · trecho 2",
    )

    svg = output.read_text(encoding="utf-8")
    assert "Destinatário sem posição" in svg
    assert "0/1 conexões exibidas" in svg
    assert "0/6 passes completos exibidos" in svg
    assert "6 sem coordenadas para ligar os jogadores" in svg


def test_exports_nominal_rosters_without_passes(tmp_path: Path) -> None:
    output = tmp_path / "no_passes.svg"

    render_pass_network(
        [],
        [],
        team_names={"team-1": "Argentina", "team-2": "França"},
        player_names={
            "player-a": "Jogador sem passes A",
            "player-d": "Jogador sem passes D",
        },
        shirt_numbers={
            ("team-1", "player-a"): 7,
            ("team-2", "player-d"): 1,
        },
        nominal_player_ids={
            "team-1": ("player-a",),
            "team-2": ("player-d",),
        },
        output_path=output,
        title="Rede por trecho",
        window_label="P1 · trecho sem passes",
    )

    svg = output.read_text(encoding="utf-8")
    assert "Jogador sem passes A" in svg
    assert "Jogador sem passes D" in svg
    assert "Sem passes tentados neste trecho" in svg
    assert "0/0 passes completos exibidos" in svg


@pytest.mark.parametrize("include_outside_node", [True, False])
def test_rejects_network_player_outside_nominal_roster(
    tmp_path: Path,
    include_outside_node: bool,
) -> None:
    output = tmp_path / "invalid_roster.svg"
    nodes = make_nodes() if include_outside_node else make_nodes()[:1]

    with pytest.raises(ValueError, match="outside nominal roster"):
        render_pass_network(
            nodes,
            make_connections()[:1],
            team_names={"team-1": "Argentina", "team-2": "França"},
            player_names={},
            shirt_numbers={
                **SHIRT_NUMBERS,
                ("team-2", "player-d"): 1,
            },
            nominal_player_ids={
                "team-1": ("player-a",),
                "team-2": ("player-d",),
            },
            output_path=output,
            title="Rede por trecho",
            window_label="P1 · trecho inválido",
        )

    assert not output.exists()
