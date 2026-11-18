"""Resolve local PNG flags by provider/team identity and verify their integrity."""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FlagPNG(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)


class FlagRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    provider: str = Field(min_length=1)
    external_team_id: str = Field(min_length=1)
    display_name: str
    flag_code: str
    png: FlagPNG


class FlagCatalogue(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    catalogue_version: Literal["1.0.0"]
    teams: list[FlagRecord]

    @model_validator(mode="after")
    def check_identities(self) -> Self:
        keys = [(r.provider, r.external_team_id) for r in self.teams]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate flag provider/team identity")
        return self


@dataclass(frozen=True)
class FlagAsset:
    record: FlagRecord
    image: bytes


@dataclass(frozen=True)
class ResolvedFlags:
    catalogue_version: str
    catalogue_sha256: str
    provider: str
    assets: dict[str, FlagAsset]
    missing: dict[str, str]

    def metadata(self) -> dict[str, object]:
        return {
            "catalogue_version": self.catalogue_version,
            "catalogue_sha256": self.catalogue_sha256,
            "provider": self.provider,
            "used": [
                {
                    "team_id": team,
                    "flag_code": asset.record.flag_code,
                    "png": asset.record.png.model_dump(),
                }
                for team, asset in sorted(self.assets.items())
            ],
            "fallbacks": self.missing,
        }


def resolve_team_flags(
    catalogue_path: Path,
    team_ids: tuple[str, ...],
    *,
    provider: str = "statsbomb",
) -> ResolvedFlags:
    raw = catalogue_path.read_bytes()
    catalogue = FlagCatalogue.model_validate_json(raw)
    records = {(r.provider, r.external_team_id): r for r in catalogue.teams}
    root = catalogue_path.parent.resolve()
    assets: dict[str, FlagAsset] = {}
    missing = {}
    for team in sorted(set(team_ids)):
        record = records.get((provider, team))
        if record is None:
            missing[team] = "team_flag_not_registered"
            continue
        path = (root / record.png.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Flag path is outside the catalogue directory")
        if not path.is_file():
            missing[team] = "flag_file_missing"
            continue
        image = path.read_bytes()
        if len(image) != record.png.bytes or hashlib.sha256(image).hexdigest() != record.png.sha256:
            raise ValueError(f"Flag PNG hash/size mismatch: {provider}/{team}")
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.format != "PNG" or decoded.size != (record.png.width, record.png.height):
                raise ValueError(f"Flag PNG format/dimensions mismatch: {provider}/{team}")
            decoded.load()
        assets[team] = FlagAsset(record, image)
    return ResolvedFlags(
        catalogue.catalogue_version, hashlib.sha256(raw).hexdigest(), provider, assets, missing
    )
