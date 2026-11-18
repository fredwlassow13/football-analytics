import hashlib
import json

import pytest
from PIL import Image

from football_analytics.visualization.team_flags import resolve_team_flags


@pytest.fixture
def catalogue(tmp_path):
    teams = []
    (tmp_path / "png").mkdir()
    for team, color in (("1", "red"), ("2", "blue")):
        path = tmp_path / "png" / f"{team}.png"
        Image.new("RGB", (32, 24), color).save(path)
        raw = path.read_bytes()
        teams.append(
            {
                "provider": "statsbomb",
                "external_team_id": team,
                "display_name": "Same name",
                "flag_code": f"test-{team}",
                "png": {
                    "path": f"png/{team}.png",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "width": 32,
                    "height": 24,
                },
            }
        )
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"catalogue_version": "1.0.0", "teams": teams}))
    return path


def test_resolution_uses_provider_and_id_instead_of_name(catalogue):
    flags = resolve_team_flags(catalogue, ("1", "2", "999"))
    assert flags.assets["1"].image != flags.assets["2"].image
    assert flags.missing == {"999": "team_flag_not_registered"}
    assert flags.catalogue_sha256 == hashlib.sha256(catalogue.read_bytes()).hexdigest()
    other = resolve_team_flags(catalogue, ("1",), provider="another-provider")
    assert not other.assets
    assert other.missing == {"1": "team_flag_not_registered"}


def test_missing_image_has_explicit_fallback(catalogue):
    (catalogue.parent / "png/1.png").unlink()
    flags = resolve_team_flags(catalogue, ("1", "2"))
    assert flags.missing == {"1": "flag_file_missing"}
    assert set(flags.assets) == {"2"}


def test_modified_image_is_rejected_even_with_same_file_size(catalogue):
    path = catalogue.parent / "png/1.png"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="hash/size mismatch"):
        resolve_team_flags(catalogue, ("1",))


def test_duplicate_identity_is_rejected(catalogue):
    content = json.loads(catalogue.read_text())
    content["teams"].append(content["teams"][0])
    catalogue.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="Duplicate flag"):
        resolve_team_flags(catalogue, ("1",))


def test_catalogue_paths_cannot_escape_asset_directory(catalogue):
    content = json.loads(catalogue.read_text())
    content["teams"][0]["png"]["path"] = "../outside.png"
    catalogue.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="outside the catalogue"):
        resolve_team_flags(catalogue, ("1",))
