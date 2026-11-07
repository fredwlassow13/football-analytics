from unittest.mock import patch

import pytest

from football_analytics.ingestion.match_lineups import main


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--match-id", "0"],
        ["--match-id", "-1"],
        ["--match-id", "abc"],
    ],
)
def test_invalid_arguments_do_not_start_ingestion(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("sys.argv", ["match_lineups", *arguments]),
        patch("football_analytics.ingestion.match_lineups.get_engine") as mocked_engine,
        patch("football_analytics.ingestion.match_lineups.httpx.Client") as mocked_http,
    ):
        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 2
        mocked_engine.assert_not_called()
        mocked_http.assert_not_called()

    assert "--match-id" in capsys.readouterr().err


def test_help_does_not_start_ingestion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("sys.argv", ["match_lineups", "--help"]),
        patch("football_analytics.ingestion.match_lineups.get_engine") as mocked_engine,
        patch("football_analytics.ingestion.match_lineups.httpx.Client") as mocked_http,
    ):
        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        mocked_engine.assert_not_called()
        mocked_http.assert_not_called()

    assert "--match-id" in capsys.readouterr().out
