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
    owner_type: int = 0xFAEB9AB4 # CCTV type
    geometry_uid: int | None = None

    @property
    def mesh_uid(self) -> int:
        return self.uid if self.geometry_uid is None else self.geometry_uid

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
            if header.uid == owner_uid and header.file_type == self.owner_type and all(marker in payload for marker in self.markers) and self.mesh_uid in read_mesh_bindings(payload):
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

DOOR_BARRICADE = EnvironmentAsset(
    uid=0x510F6B21D,
    name="Wooden doorway barricade (intact)",
    owners=(("datapc64_ondemand_playgo.forge", 0x106FC47D2),),
    markers=(b"Core_ReinforcementBarricade_SM", b"BarricadeDoor"),
    owner_type=0xE261EA92,
)

WINDOW_BARRICADE = EnvironmentAsset(
    uid=0x5545CBAAC,
    name="Wooden window barricade (intact)",
    owners=(("datapc64_ondemand_playgo.forge", 0x106FC47E0),),
    # enabled intact visual in single-window world model
    markers=(bytes.fromhex("2b80eac50100") + (0xCF0176A1).to_bytes(8, "little"),),
    owner_type=0xE261EA92,
)

PLANTED_DEFUSER = EnvironmentAsset(
    uid=0x51190D3C8,
    name="Defuser (planted)",
    owners=(("datapc64_ondemand_playgo.forge", 0x1A7366CA6),),
    markers=(b"BNK_GM_PlantBomb_Diffuser", b"BNK_S_O_GP_DefuserCase"),
    owner_type=0xE261EA92,
)

OPEN_DEFUSER = EnvironmentAsset(
    uid=0x10B3D0958, # open idle clip. Also identifies this selection and its cache
    name="Defuser (open keyboard)",
    owners=(("datapc64_ondemand_playgo.forge", 0x106FD48CE),),
    markers=(b"Core_Defuser_SM",),
    owner_type=0xE261EA92,
    geometry_uid=PLANTED_DEFUSER.uid,
)

DOUBLE_DOOR_BARRICADE = EnvironmentAsset(
    uid=0x540CFA01B,
    name="Wooden double doorway barricade (intact)",
    owners=(("datapc64_ondemand_playgo.forge", 0x106FC47C6),),
    # enabled intact visual in the double-door world package
    markers=(bytes.fromhex("2b80eac50100") + (0xCF016F58).to_bytes(8, "little"),),
    owner_type=0xE261EA92,
)

WALL_REINFORCEMENT = EnvironmentAsset(
    uid=0x614D71100B, # fully deployed 2m panel with all 14 fasteners
    name="Wall reinforcement (fully deployed, 2m)",
    owners=(("datapc64_ondemand_playgo.forge", 0x614D711006),),
    markers=(b"Core_ReinforcementBarricade_SM", b"Wall_Full_Height"),
    owner_type=0xE261EA92,
)

ENVIRONMENT_ASSETS = {
    asset.uid: asset
    for asset in (
            CCTV, DOOR_BARRICADE, DOUBLE_DOOR_BARRICADE,
            WINDOW_BARRICADE, PLANTED_DEFUSER, OPEN_DEFUSER,
            WALL_REINFORCEMENT
        )
}
