"""Standalone map props available in the Envirnment Tab"""
from dataclasses import dataclass
from pathlib import Path

from src.database import load_asset_index
from src.metadata import parse_file_metadata

@dataclass(frozen=True)
class EnvironmentAsset:
    uid: int
    name: str
    owners: tuple[tuple[str, int], ...]
    markers: tuple[bytes, ...]

    def source(self, game, database):
        from src.model import load_asset_payload, read_mesh_bindings

        game = Path(game).resolve()

        for filename, owner_uid in self.owners:
            archive = game / filename
            if not archive.is_file() or not archive.with_suffix(".depgraphbin").is_file():
                continue

            record = load_asset_index(database, {owner_uid}).primary(owner_uid)
            if record is None or record.archive.resolve() != archive:
                continue

            payload = load_asset_payload(record)
            header = parse_file_metadata(payload)
            if header.uid == owner_uid and header.file_type == 0xFAEB9AB4 and all(marker in payload for marker in self.markers) and self.uid in read_mesh_bindings(payload):
                return record

        raise ValueError(f"No verified source for {self.name}. Update the asset index for this game folder.")

CCTV = EnvironmentAsset(
    uid=0x539851156,
    name="Default CCTV camera",
    owners=(
        ("datapc64_onb02_tutorial_mod_playgo.forge", 0x692609411B),
        ("datapc64_pvp13_border_v2_mod.forge", 0x5FB92784FC),
        ("datapc64_pvp02_oregon_v2_mod.forge", 0x66535FF503),
    ),
    markers=(
        b"BNK_S_O_GP_SecurityCam",
        # Enabled camera binding followed by its visual template UID
        bytes.fromhex("2b80eac501001139c41400000000")
    ),
)

ENVIRONMENT_ASSETS = {CCTV.uid: CCTV}
