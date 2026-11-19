"""Standalone map props available in the Envirnment Tab"""
from dataclasses import dataclass
from pathlib import Path

from src.database import load_asset_index
from src.gadgets import BREACH_ACTIVATOR
from src.metadata import parse_file_metadata

@dataclass(frozen=True)
class EnvironmentAsset:
    uid: int
    name: str
    owners: tuple[tuple[str, int], ...]
    markers: tuple[bytes, ...]
    owner_type: int = 0xFAEB9AB4 # CCTV type
    geometry_uid: int | None = None
    extra_meshes: tuple[int, ...] = ()

    @property
    def mesh_uids(self):
        return (self.mesh_uid, *self.extra_meshes)

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
            if header.uid == owner_uid and header.file_type == self.owner_type and all(marker in payload for marker in self.markers) and set(self.mesh_uids).issubset(read_mesh_bindings(payload)):
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

def enabled_visual(uid):
    return bytes.fromhex("2b80eac50100") + uid.to_bytes(8, "little")

PROP_MAPS = (
    ("datapc64_onb02_tutorial_mod_playgo.forge", 0x69260940A1),
    ("datapc64_pvp02_oregon_v2_mod.forge", 0x66535FF3DB),
)

FIRE_EXTINGUISHER = EnvironmentAsset(
    uid=0x5F4C5238DE,
    name="Fire extinguisher (intact)",
    owners=PROP_MAPS,
    markers=(b"BNK_S_GDI_FireExtinguisher", enabled_visual(0x5FFF471C56)),
)

GAS_PIPE = EnvironmentAsset(
    uid=0x6075D21103,
    name="Gas pipe (intact)",
    owners=PROP_MAPS,
    markers=(b"BNK_S_GDI_GasPipe", enabled_visual(0x60F0FAC868)),
)

OBSERVATION_PHONE = EnvironmentAsset(
    uid=0x53984ECF3,
    name="Observation phone (shared)",
    owners=(("datapc64_ondemand_playgo.forge", 0x106FC5C90),),
    markers=(enabled_visual(0xBE6A0054),),
    owner_type=0xE261EA92,
)

HATCH_REINFORCEMENT = EnvironmentAsset(
    uid=0x5F0654F7D4,
    name="Hatch reinforcement (fully deployed)",
    owners=(("datapc64_ondemand_playgo.forge", 0x5E8C091400),),
    markers=(enabled_visual(0x5F0654F7C9),),
    owner_type=0xE261EA92,
)

def equipment(name, mesh, owner, visual, archive, *extra_meshes):
    return EnvironmentAsset(
        uid=mesh,
        name=name,
        owners=((archive, owner),),
        markers=(enabled_visual(visual),),
        owner_type=0xE261EA92,
        extra_meshes=extra_meshes,
    )

ON_DEMAND = "datapc64_ondemand.forge"
PLAYGO = "datapc64_ondemand_playgo.forge"
SET01 = "datapc64_ondemand_set01.forge"
SET02 = "datapc64_ondemand_set02.forge"

ENVIRONMENT_GROUPS = {
    "Map props and equipment": (
        CCTV, DOOR_BARRICADE, DOUBLE_DOOR_BARRICADE, WINDOW_BARRICADE,
        PLANTED_DEFUSER, OPEN_DEFUSER, WALL_REINFORCEMENT, HATCH_REINFORCEMENT,
        FIRE_EXTINGUISHER, GAS_PIPE, OBSERVATION_PHONE, BREACH_ACTIVATOR
    ),
    "Attackger gadgets": (
        equipment("Hard breach charge", 0x43C4DB4CCF, 0x3F547D17BF, 0x43C4DB4CCA, PLAYGO),
        equipment("Soft breach charge", 0x539850B81, 0x24F9498260, 0x61EB5BA9, PLAYGO),
        equipment("Claymore", 0x8C1A46EF8, 0x356FD1B2B4, 0x8C1A46EF5, SET01),
        equipment("Frag grenade", 0x45CB3E5E0, 0x24F9498250, 0x13096816, PLAYGO, 0x45CB3E5DF, 0x45CB3E5DE),
        equipment("Stun grenade", 0x526B02A34, 0x9B2CAF0E, 0xCEFFD93, PLAYGO, 0x526B02A33, 0x526B02A32),
        equipment("Smoke grenade", 0x119CFDDEE, 0x9B2CAF14, 0x2E53FD9C, PLAYGO, 0x526B033E7),
    ),
    "Defender gadgets": (
        equipment("Proximity alarm", 0x1BC016173A, 0x1018C91F6D, 0x1BC0161737, SET01),
        equipment("Barbed wire (deployed)", 0x10A05486E, 0x3C8F4197B4, 0x1CA3C34C, PLAYGO),
        equipment("Nitro cell", 0x119CF4D22, 0xC6227CBC, 0x680A8ABA, ON_DEMAND),
        equipment("Bulletproof camera", 0x10463D49ED, 0x103D71009E, 0x10463D49EB, SET02),
        equipment("Observation blocker", 0x59FE711F56, 0x55610D2000, 0x5694E28DFF, ON_DEMAND),
    ),
}

ENVIRONMENT_ASSETS = {
    asset.uid: asset
    for group in ENVIRONMENT_GROUPS.values()
    for asset in group
    if isinstance(asset, EnvironmentAsset)
}
